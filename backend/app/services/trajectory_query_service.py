import csv
import json
from pathlib import Path
import re
from typing import Dict, Iterator, Optional, Tuple

from ..errors import UploadError
from ..schemas import TrajectoryDetail, TrajectoryPage, TrajectorySummary
from ..validation import TrajectoryRowValidationError, validate_trajectory_row


UPLOAD_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
TRAJECTORY_ID_PATTERN = re.compile(r"Q([0-9]{6})")


class TrajectoryQueryService:
    def __init__(self, upload_root: Path) -> None:
        self.upload_root = upload_root

    def list_trajectories(
        self, upload_id: str, page: int, page_size: int, search: Optional[str] = None
    ) -> TrajectoryPage:
        upload_dir, stored_total = self._resolve_upload(upload_id)
        start = (page - 1) * page_size
        items = []
        normalized_search = search.strip().casefold() if search else ""
        matched_total = 0

        for sequence, (_, row) in enumerate(
            self._trajectory_rows(upload_dir / "query_traj_od.csv"), start=1
        ):
            trajectory_id = self._trajectory_id(sequence)
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

        total = matched_total if normalized_search else stored_total
        expected_count = max(0, min(page_size, total - start))
        if len(items) != expected_count:
            raise RuntimeError("stored trajectory count does not match metadata")

        return TrajectoryPage(
            upload_id=upload_id,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
            items=items,
        )

    def get_trajectory(self, upload_id: str, trajectory_id: str) -> TrajectoryDetail:
        upload_dir, total = self._resolve_upload(upload_id)
        sequence = self._parse_trajectory_id(trajectory_id)
        if sequence > total:
            raise UploadError(404, "TRAJECTORY_NOT_FOUND", "trajectory was not found")

        for current_sequence, (_, row) in enumerate(
            self._trajectory_rows(upload_dir / "query_traj_od.csv"), start=1
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

    def _resolve_upload(self, upload_id: str) -> Tuple[Path, int]:
        if not UPLOAD_ID_PATTERN.fullmatch(upload_id):
            raise UploadError(404, "UPLOAD_NOT_FOUND", "upload was not found")
        upload_dir = self.upload_root / upload_id
        source = upload_dir / "query_traj_od.csv"
        metadata_path = upload_dir / "metadata.json"
        if not upload_dir.is_dir() or not source.is_file() or not metadata_path.is_file():
            raise UploadError(404, "UPLOAD_NOT_FOUND", "upload was not found")

        try:
            with metadata_path.open("r", encoding="utf-8") as metadata_file:
                metadata = json.load(metadata_file)
            total = int(metadata["trajectory_count"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("upload metadata is invalid") from exc
        if total < 1:
            raise RuntimeError("upload metadata has an invalid trajectory count")
        return upload_dir, total

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
                        value is None or not value.strip() for value in normalized.values()
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
        return (
            trajectory_id.casefold() == normalized_search
            or user_id == normalized_search
        )

    @staticmethod
    def _parse_trajectory_id(trajectory_id: str) -> int:
        match = TRAJECTORY_ID_PATTERN.fullmatch(trajectory_id)
        if match is None or int(match.group(1)) == 0:
            raise UploadError(404, "TRAJECTORY_NOT_FOUND", "trajectory was not found")
        return int(match.group(1))

    @staticmethod
    def _trajectory_id(sequence: int) -> str:
        return f"Q{sequence:06d}"
