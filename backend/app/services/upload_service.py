import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Dict, Tuple
from uuid import uuid4

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from ..errors import UploadError
from ..schemas import UploadResult


EXPECTED_FILENAME = "query_traj_od.csv"
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
CHUNK_SIZE = 1024 * 1024


class UploadService:
    def __init__(self, upload_root: Path, max_upload_bytes: int) -> None:
        self.upload_root = upload_root
        self.max_upload_bytes = max_upload_bytes

    async def upload(self, upload: UploadFile) -> UploadResult:
        if upload.filename != EXPECTED_FILENAME:
            raise UploadError(
                400,
                "INVALID_FILE_NAME",
                f"only {EXPECTED_FILENAME} can be uploaded in phase one",
            )

        self.upload_root.mkdir(parents=True, exist_ok=True)
        staging_root = self.upload_root / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        upload_id = uuid4().hex
        staging_dir = staging_root / upload_id
        final_dir = self.upload_root / upload_id
        staging_dir.mkdir()
        stored_file = staging_dir / EXPECTED_FILENAME

        try:
            size_bytes, checksum = await self._stream_to_disk(upload, stored_file)
            trajectory_count = await run_in_threadpool(
                self._validate_and_index,
                stored_file,
                staging_dir / "trajectory_index.csv",
            )
            created_at = datetime.now(timezone.utc)
            result = UploadResult(
                upload_id=upload_id,
                file_name=EXPECTED_FILENAME,
                trajectory_count=trajectory_count,
                first_trajectory_id=self._trajectory_id(1),
                last_trajectory_id=self._trajectory_id(trajectory_count),
                size_bytes=size_bytes,
                sha256=checksum,
                created_at=created_at,
            )
            self._write_metadata(staging_dir / "metadata.json", result)
            staging_dir.replace(final_dir)
            return result
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

    async def _stream_to_disk(self, upload: UploadFile, destination: Path) -> Tuple[int, str]:
        size_bytes = 0
        digest = hashlib.sha256()
        with destination.open("xb") as output:
            while chunk := await upload.read(CHUNK_SIZE):
                size_bytes += len(chunk)
                if size_bytes > self.max_upload_bytes:
                    raise UploadError(
                        413,
                        "FILE_TOO_LARGE",
                        f"file exceeds the {self.max_upload_bytes}-byte upload limit",
                    )
                digest.update(chunk)
                output.write(chunk)
        if size_bytes == 0:
            raise UploadError(422, "EMPTY_FILE", "uploaded file is empty")
        return size_bytes, digest.hexdigest()

    def _validate_and_index(self, source: Path, index_path: Path) -> int:
        with source.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.reader(csv_file, strict=True)
            try:
                headers = next(reader)
            except StopIteration as exc:
                raise UploadError(422, "EMPTY_FILE", "uploaded file is empty") from exc

            headers = [header.strip() for header in headers]
            self._validate_headers(headers)

            count = 0
            with index_path.open("x", encoding="utf-8", newline="") as index_file:
                writer = csv.writer(index_file)
                writer.writerow(("trajectory_id", "csv_row_number"))
                for row_number, row in enumerate(reader, start=2):
                    if not row or all(not value.strip() for value in row):
                        continue
                    if len(row) != len(headers):
                        raise UploadError(
                            422,
                            "INVALID_ROW",
                            f"CSV row {row_number} has {len(row)} columns; expected {len(headers)}",
                        )
                    if count == 999_999:
                        raise UploadError(
                            422,
                            "TOO_MANY_TRAJECTORIES",
                            "CSV exceeds the Q000001-Q999999 identifier capacity",
                        )
                    count += 1
                    writer.writerow((self._trajectory_id(count), row_number))

        if count == 0:
            raise UploadError(422, "NO_TRAJECTORIES", "CSV contains no trajectory rows")
        return count

    @staticmethod
    def _validate_headers(headers: list) -> None:
        if not headers or all(not header for header in headers):
            raise UploadError(422, "MISSING_HEADER", "CSV header is missing")
        duplicates = sorted({header for header in headers if headers.count(header) > 1})
        if duplicates:
            raise UploadError(
                422,
                "DUPLICATE_HEADERS",
                f"duplicate CSV headers: {', '.join(duplicates)}",
            )
        missing = [header for header in REQUIRED_HEADERS if header not in headers]
        if missing:
            raise UploadError(
                422,
                "MISSING_HEADERS",
                f"missing required CSV headers: {', '.join(missing)}",
            )

    @staticmethod
    def _trajectory_id(sequence: int) -> str:
        return f"Q{sequence:06d}"

    @staticmethod
    def _write_metadata(path: Path, result: UploadResult) -> None:
        payload: Dict[str, object] = result.model_dump(mode="json")
        with path.open("x", encoding="utf-8") as metadata_file:
            json.dump(payload, metadata_file, ensure_ascii=True, indent=2)
            metadata_file.write("\n")
