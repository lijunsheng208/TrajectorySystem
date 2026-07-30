from dataclasses import dataclass
from pathlib import Path
import os


def _positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class Settings:
    upload_root: Path
    max_upload_bytes: int

    @classmethod
    def from_environment(cls) -> "Settings":
        project_root = Path(__file__).resolve().parents[2]
        return cls(
            upload_root=Path(
                os.getenv("UPLOAD_ROOT", str(project_root / "data" / "uploads"))
            ).resolve(),
            max_upload_bytes=_positive_int("MAX_UPLOAD_BYTES", 200 * 1024 * 1024),
        )

