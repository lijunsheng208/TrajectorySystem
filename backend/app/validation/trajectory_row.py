import ast
from dataclasses import dataclass
import json
import math
from typing import Any, List, Mapping


MAX_LIST_FIELD_CHARACTERS = 10 * 1024 * 1024


class TrajectoryRowValidationError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


@dataclass(frozen=True)
class ValidatedTrajectoryRow:
    user_id: str
    call_type: str
    road_len: int
    gps_len: int
    road: List[Any]
    gps: List[Any]
    time: List[Any]
    ptime: List[Any]


def validate_trajectory_row(row: Mapping[str, str]) -> ValidatedTrajectoryRow:
    user_id = _require_value(row, "user_id")
    call_type = _require_value(row, "call_type")

    declared_road_length = _parse_non_negative_integer(row, "road_len")
    declared_gps_length = _parse_non_negative_integer(row, "gps_len")
    road = _parse_list(row, "road")
    gps = _parse_list(row, "gps")
    timestamps = _parse_list(row, "time")
    projected_timestamps = _parse_list(row, "ptime")

    _validate_gps(gps)
    _validate_timestamps(timestamps, "time")
    _validate_timestamps(projected_timestamps, "ptime")

    if len(gps) != len(timestamps):
        raise TrajectoryRowValidationError(
            "gps/time",
            f"length mismatch: gps has {len(gps)} items but time has {len(timestamps)}",
        )
    if len(road) != len(projected_timestamps):
        raise TrajectoryRowValidationError(
            "road/ptime",
            f"length mismatch: road has {len(road)} items but ptime has {len(projected_timestamps)}",
        )
    if declared_road_length != len(road):
        raise TrajectoryRowValidationError(
            "road_len",
            f"declared {declared_road_length} but road contains {len(road)} items",
        )
    if declared_gps_length != len(gps):
        raise TrajectoryRowValidationError(
            "gps_len",
            f"declared {declared_gps_length} but gps contains {len(gps)} items",
        )

    return ValidatedTrajectoryRow(
        user_id=user_id,
        call_type=call_type,
        road_len=declared_road_length,
        gps_len=declared_gps_length,
        road=road,
        gps=gps,
        time=timestamps,
        ptime=projected_timestamps,
    )


def _require_value(row: Mapping[str, str], field: str) -> str:
    value = row.get(field, "")
    if value is None or not str(value).strip():
        raise TrajectoryRowValidationError(field, "value is required")
    return str(value).strip()


def _parse_non_negative_integer(row: Mapping[str, str], field: str) -> int:
    raw_value = _require_value(row, field)
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise TrajectoryRowValidationError(field, "must be an integer") from exc
    if value < 0:
        raise TrajectoryRowValidationError(field, "must not be negative")
    return value


def _parse_list(row: Mapping[str, str], field: str) -> List[Any]:
    raw_value = _require_value(row, field)
    if len(raw_value) > MAX_LIST_FIELD_CHARACTERS:
        raise TrajectoryRowValidationError(field, "list value is too large")

    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        try:
            value = ast.literal_eval(raw_value)
        except (ValueError, SyntaxError, MemoryError, RecursionError) as exc:
            raise TrajectoryRowValidationError(
                field, "must be a valid JSON or Python literal list"
            ) from exc

    if not isinstance(value, list):
        raise TrajectoryRowValidationError(field, "must be a list")
    return value


def _validate_gps(gps: List[Any]) -> None:
    for point_index, point in enumerate(gps, start=1):
        if not isinstance(point, list) or len(point) != 2:
            raise TrajectoryRowValidationError(
                "gps", f"point {point_index} must be a [longitude, latitude] list"
            )
        longitude = _finite_number(point[0], "gps", f"point {point_index} longitude")
        latitude = _finite_number(point[1], "gps", f"point {point_index} latitude")
        if not -180 <= longitude <= 180:
            raise TrajectoryRowValidationError(
                "gps", f"point {point_index} longitude must be between -180 and 180"
            )
        if not -90 <= latitude <= 90:
            raise TrajectoryRowValidationError(
                "gps", f"point {point_index} latitude must be between -90 and 90"
            )


def _validate_timestamps(values: List[Any], field: str) -> None:
    previous = None
    for index, raw_value in enumerate(values, start=1):
        timestamp = _finite_number(raw_value, field, f"item {index}")
        if previous is not None and timestamp < previous:
            raise TrajectoryRowValidationError(
                field, f"timestamps must be non-decreasing at item {index}"
            )
        previous = timestamp


def _finite_number(value: Any, field: str, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrajectoryRowValidationError(field, f"{location} must be numeric")
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise TrajectoryRowValidationError(field, f"{location} must be finite")
    return numeric_value
