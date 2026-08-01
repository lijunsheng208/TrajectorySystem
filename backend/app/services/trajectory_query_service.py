import csv
from pathlib import Path
import re
from typing import Dict, Iterator, Optional, Tuple

from ..errors import UploadError
from ..schemas import EvaluationTrajectoryPage, TrajectoryDetail, TrajectorySummary
from ..validation import TrajectoryRowValidationError, validate_trajectory_row
from .evaluation_service import EvaluationService, ROLE_CONFIG


class EvaluationTrajectoryQueryService:
    def __init__(self, evaluation_service: EvaluationService) -> None:
        self.evaluation_service = evaluation_service

    def list_trajectories(
        self,
        evaluation_id: str,
        role: str,
        page: int,
        page_size: int,
        search: Optional[str] = None,
    ) -> EvaluationTrajectoryPage:
        file_name, prefix = self.evaluation_service.role_config(role)
        role_dir = self.evaluation_service.resolve_role_dir(evaluation_id, role)
        total = self.evaluation_service.get(evaluation_id).files[role].trajectory_count
        start = (page - 1) * page_size
        normalized_search = search.strip().casefold() if search else ""
        matched_total = 0
        items = []

        for sequence, (_, row) in enumerate(
            self._trajectory_rows(role_dir / file_name), start=1
        ):
            trajectory_id = f"{prefix}{sequence:06d}"
            if normalized_search and not self._matches_search(
                row, trajectory_id, normalized_search
            ):
                continue
            matched_total += 1
            if matched_total <= start or len(items) >= page_size:
                if not normalized_search and len(items) >= page_size:
                    break
                continue
            data = self._validated_row(row, sequence)
            items.append(
                TrajectorySummary(
                    trajectory_id=trajectory_id,
                    user_id=data.user_id,
                    call_type=data.call_type,
                    road_len=data.road_len,
                    gps_len=data.gps_len,
                )
            )

        result_total = matched_total if normalized_search else total
        expected_count = max(0, min(page_size, result_total - start))
        if len(items) != expected_count:
            raise RuntimeError("stored trajectory count does not match metadata")
        return EvaluationTrajectoryPage(
            evaluation_id=evaluation_id,
            role=role,
            page=page,
            page_size=page_size,
            total=result_total,
            total_pages=(result_total + page_size - 1) // page_size,
            items=items,
        )

    def get_trajectory(
        self, evaluation_id: str, trajectory_id: str
    ) -> TrajectoryDetail:
        match = re.fullmatch(r"([QSD])([0-9]{6})", trajectory_id)
        if match is None or int(match.group(2)) == 0:
            raise UploadError(404, "TRAJECTORY_NOT_FOUND", "trajectory was not found")
        prefix, sequence_text = match.groups()
        role = next(
            (
                name
                for name, (_, configured_prefix) in ROLE_CONFIG.items()
                if configured_prefix == prefix
            ),
            None,
        )
        if role is None:
            raise UploadError(404, "TRAJECTORY_NOT_FOUND", "trajectory was not found")
        current = self.evaluation_service.get(evaluation_id)
        if (
            role not in current.files
            or int(sequence_text) > current.files[role].trajectory_count
        ):
            raise UploadError(404, "TRAJECTORY_NOT_FOUND", "trajectory was not found")

        file_name, _ = ROLE_CONFIG[role]
        role_dir = self.evaluation_service.resolve_role_dir(evaluation_id, role)
        sequence = int(sequence_text)
        for current_sequence, (_, row) in enumerate(
            self._trajectory_rows(role_dir / file_name), start=1
        ):
            if current_sequence != sequence:
                continue
            data = self._validated_row(row, sequence)
            return TrajectoryDetail(
                trajectory_id=trajectory_id,
                user_id=data.user_id,
                call_type=data.call_type,
                road_len=data.road_len,
                gps_len=data.gps_len,
                road=data.road,
                gps=data.gps,
                time=data.time,
                ptime=data.ptime,
            )
        raise RuntimeError("stored trajectory count does not match metadata")

    @staticmethod
    def _trajectory_rows(source: Path) -> Iterator[Tuple[int, Dict[str, str]]]:
        try:
            with source.open("r", encoding="utf-8-sig", newline="") as csv_file:
                reader = csv.DictReader(csv_file, strict=True)
                for csv_row_number, row in enumerate(reader, start=2):
                    normalized = {
                        key.strip(): value for key, value in row.items() if key is not None
                    }
                    if not normalized or all(
                        value is None or not value.strip()
                        for value in normalized.values()
                    ):
                        continue
                    yield csv_row_number, normalized
        except (OSError, UnicodeError, csv.Error) as exc:
            raise RuntimeError("stored trajectory file could not be read") from exc

    @staticmethod
    def _validated_row(row: Dict[str, str], sequence: int):
        try:
            return validate_trajectory_row(row)
        except TrajectoryRowValidationError as exc:
            raise RuntimeError(
                f"stored trajectory {sequence} failed validation: {exc.field}"
            ) from exc

    @staticmethod
    def _matches_search(
        row: Dict[str, str], trajectory_id: str, normalized_search: str
    ) -> bool:
        user_id = str(row.get("user_id") or "").strip().casefold()
        return trajectory_id.casefold() == normalized_search or user_id == normalized_search
