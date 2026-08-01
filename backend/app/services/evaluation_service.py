import asyncio
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Dict, Iterator, Tuple
from uuid import uuid4

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from ..errors import UploadError
from ..schemas import EvaluationFileResult, EvaluationResult
from ..validation import TrajectoryRowValidationError, validate_trajectory_row


EVALUATION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
CHUNK_SIZE = 1024 * 1024
REQUIRED_HEADERS = (
    "user_id",
    "call_type",
    "road_len",
    "gps_len",
    "road",
    "gps",
    "time",
    "ptime",
)
ROLE_CONFIG = {
    "query": ("query_traj_od.csv", "Q"),
    "query-sim": ("query_sim_traj_od.csv", "S"),
    "database": ("database_traj_od.csv", "D"),
}


class EvaluationService:
    def __init__(self, upload_root: Path, max_upload_bytes: int) -> None:
        self.root = upload_root / "evaluations"
        self.max_upload_bytes = max_upload_bytes
        self._locks: Dict[str, asyncio.Lock] = {}

    def create(self) -> EvaluationResult:
        self.root.mkdir(parents=True, exist_ok=True)
        evaluation_id = uuid4().hex
        now = datetime.now(timezone.utc)
        result = EvaluationResult(
            evaluation_id=evaluation_id,
            status="collecting",
            files={},
            created_at=now,
            updated_at=now,
        )
        evaluation_dir = self.root / evaluation_id
        evaluation_dir.mkdir()
        (evaluation_dir / "files").mkdir()
        self._write_metadata(evaluation_dir / "metadata.json", result)
        return result

    def get(self, evaluation_id: str) -> EvaluationResult:
        return self._read_metadata(self._resolve_evaluation(evaluation_id))

    async def upload_file(
        self, evaluation_id: str, role: str, upload: UploadFile
    ) -> EvaluationResult:
        evaluation_dir = self._resolve_evaluation(evaluation_id)
        expected_name, prefix = self.role_config(role)

        version = uuid4().hex
        staging_dir = self.root / ".staging" / evaluation_id / version
        staging_dir.mkdir(parents=True)
        stored_file = staging_dir / expected_name
        try:
            size_bytes, checksum = await self._stream_to_disk(upload, stored_file)
            count = await run_in_threadpool(
                self._validate_and_index,
                stored_file,
                staging_dir / "trajectory_index.csv",
                prefix,
            )
            file_result = EvaluationFileResult(
                role=role,
                file_name=expected_name,
                trajectory_count=count,
                first_trajectory_id=f"{prefix}000001",
                last_trajectory_id=f"{prefix}{count:06d}",
                size_bytes=size_bytes,
                sha256=checksum,
                created_at=datetime.now(timezone.utc),
            )

            async with self._locks.setdefault(evaluation_id, asyncio.Lock()):
                current = self._read_metadata(evaluation_dir)
                counterpart = "query-sim" if role == "query" else "query"
                if role in {"query", "query-sim"} and counterpart in current.files:
                    counterpart_dir = self.resolve_role_dir(evaluation_id, counterpart)
                    await run_in_threadpool(
                        self._validate_pairing,
                        staging_dir / "trajectory_index.csv",
                        counterpart_dir / "trajectory_index.csv",
                    )

                final_dir = evaluation_dir / "files" / role / version
                final_dir.parent.mkdir(parents=True, exist_ok=True)
                staging_dir.replace(final_dir)
                current.files[role] = file_result
                current.status = (
                    "ready" if set(current.files) == set(ROLE_CONFIG) else "collecting"
                )
                current.updated_at = datetime.now(timezone.utc)
                self._write_metadata(evaluation_dir / "metadata.json", current, replace=True)
                return current
        except UploadError:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise
        except (csv.Error, UnicodeError) as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise UploadError(422, "INVALID_CSV", "CSV file could not be read") from exc
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise
        finally:
            await upload.close()

    def resolve_role_dir(self, evaluation_id: str, role: str) -> Path:
        evaluation_dir = self._resolve_evaluation(evaluation_id)
        current = self._read_metadata(evaluation_dir)
        if role not in current.files:
            raise UploadError(404, "EVALUATION_FILE_NOT_FOUND", "evaluation file was not uploaded")
        role_root = evaluation_dir / "files" / role
        candidates = [path for path in role_root.iterdir() if path.is_dir()]
        expected_sha = current.files[role].sha256
        for candidate in candidates:
            metadata_source = candidate / current.files[role].file_name
            if metadata_source.is_file() and self._sha_from_index(candidate) == expected_sha:
                return candidate
        raise RuntimeError("evaluation file metadata is inconsistent")

    @staticmethod
    def role_config(role: str) -> Tuple[str, str]:
        try:
            return ROLE_CONFIG[role]
        except KeyError as exc:
            raise UploadError(404, "EVALUATION_ROLE_NOT_FOUND", "evaluation file role was not found") from exc

    async def _stream_to_disk(self, upload: UploadFile, destination: Path) -> Tuple[int, str]:
        size_bytes = 0
        digest = hashlib.sha256()
        with destination.open("xb") as output:
            while chunk := await upload.read(CHUNK_SIZE):
                size_bytes += len(chunk)
                if size_bytes > self.max_upload_bytes:
                    raise UploadError(413, "FILE_TOO_LARGE", f"file exceeds the {self.max_upload_bytes}-byte upload limit")
                digest.update(chunk)
                output.write(chunk)
        if size_bytes == 0:
            raise UploadError(422, "EMPTY_FILE", "uploaded file is empty")
        (destination.parent / "sha256.txt").write_text(digest.hexdigest(), encoding="ascii")
        return size_bytes, digest.hexdigest()

    def _validate_and_index(self, source: Path, index_path: Path, prefix: str) -> int:
        with source.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.reader(csv_file, strict=True)
            try:
                headers = [header.strip() for header in next(reader)]
            except StopIteration as exc:
                raise UploadError(422, "EMPTY_FILE", "uploaded file is empty") from exc
            self._validate_headers(headers)
            count = 0
            with index_path.open("x", encoding="utf-8", newline="") as index_file:
                writer = csv.writer(index_file)
                writer.writerow(("trajectory_id", "csv_row_number", "user_id", "call_type"))
                for row_number, row in enumerate(reader, start=2):
                    if not row or all(not value.strip() for value in row):
                        continue
                    if len(row) != len(headers):
                        raise UploadError(422, "INVALID_ROW", f"CSV row {row_number} has {len(row)} columns; expected {len(headers)}")
                    try:
                        data = validate_trajectory_row(dict(zip(headers, row)))
                    except TrajectoryRowValidationError as exc:
                        raise UploadError(422, "INVALID_TRAJECTORY_DATA", f"CSV row {row_number}, field {exc.field}: {exc.message}") from exc
                    if count == 999_999:
                        raise UploadError(422, "TOO_MANY_TRAJECTORIES", f"CSV exceeds the {prefix}000001-{prefix}999999 identifier capacity")
                    count += 1
                    writer.writerow((f"{prefix}{count:06d}", row_number, data.user_id, data.call_type))
        if count == 0:
            raise UploadError(422, "NO_TRAJECTORIES", "CSV contains no trajectory rows")
        return count

    @staticmethod
    def _validate_headers(headers: list) -> None:
        if not headers or all(not header for header in headers):
            raise UploadError(422, "MISSING_HEADER", "CSV header is missing")
        duplicates = sorted({header for header in headers if headers.count(header) > 1})
        if duplicates:
            raise UploadError(422, "DUPLICATE_HEADERS", f"duplicate CSV headers: {', '.join(duplicates)}")
        missing = [header for header in REQUIRED_HEADERS if header not in headers]
        if missing:
            raise UploadError(422, "MISSING_HEADERS", f"missing required CSV headers: {', '.join(missing)}")

    @staticmethod
    def _validate_pairing(first_index: Path, second_index: Path) -> None:
        first_rows = EvaluationService._pair_rows(first_index)
        second_rows = EvaluationService._pair_rows(second_index)
        row_number = 0
        while True:
            first = next(first_rows, None)
            second = next(second_rows, None)
            if first is None or second is None:
                if first != second:
                    raise UploadError(422, "PAIR_COUNT_MISMATCH", "query and query-sim must contain the same number of trajectories")
                return
            row_number += 1
            if first != second:
                raise UploadError(422, "PAIR_DATA_MISMATCH", f"query and query-sim user_id/call_type differ at trajectory {row_number}")

    @staticmethod
    def _pair_rows(path: Path) -> Iterator[Tuple[str, str]]:
        with path.open("r", encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                yield row["user_id"], row["call_type"]

    def _resolve_evaluation(self, evaluation_id: str) -> Path:
        if not EVALUATION_ID_PATTERN.fullmatch(evaluation_id):
            raise UploadError(404, "EVALUATION_NOT_FOUND", "evaluation was not found")
        evaluation_dir = self.root / evaluation_id
        if not evaluation_dir.is_dir() or not (evaluation_dir / "metadata.json").is_file():
            raise UploadError(404, "EVALUATION_NOT_FOUND", "evaluation was not found")
        return evaluation_dir

    @staticmethod
    def _read_metadata(evaluation_dir: Path) -> EvaluationResult:
        try:
            return EvaluationResult.model_validate_json((evaluation_dir / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("evaluation metadata is invalid") from exc

    @staticmethod
    def _write_metadata(path: Path, result: EvaluationResult, replace: bool = False) -> None:
        target = path.with_suffix(".tmp") if replace else path
        target.write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        if replace:
            target.replace(path)

    @staticmethod
    def _sha_from_index(directory: Path) -> str:
        try:
            return (directory / "sha256.txt").read_text(encoding="ascii").strip()
        except OSError as exc:
            raise RuntimeError("evaluation file checksum metadata is missing") from exc
