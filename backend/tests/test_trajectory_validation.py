import pytest

from backend.app.validation import TrajectoryRowValidationError, validate_trajectory_row


def valid_row(**overrides):
    row = {
        "user_id": "42",
        "call_type": "0",
        "road_len": "2",
        "gps_len": "3",
        "road": "[101, 102]",
        "gps": "[[120.1, 30.1], [120.2, 30.2], [120.3, 30.3]]",
        "time": "[1000, 1010, 1020]",
        "ptime": "[1001, 1011]",
    }
    row.update(overrides)
    return row


def test_accepts_json_lists():
    validate_trajectory_row(valid_row())


def test_accepts_python_literal_lists_without_eval():
    validate_trajectory_row(valid_row(road="['R1', 'R2']"))


def test_accepts_equal_adjacent_projected_timestamps():
    validate_trajectory_row(valid_row(ptime="[1001, 1001]"))


@pytest.mark.parametrize(
    ("overrides", "field", "message"),
    [
        ({"user_id": ""}, "user_id", "required"),
        ({"call_type": " "}, "call_type", "required"),
        ({"road_len": "2.0"}, "road_len", "integer"),
        ({"gps": "[120.1, 30.1]", "gps_len": "2", "time": "[1, 2]"}, "gps", "point 1"),
        ({"gps": "[[181, 30], [120, 31], [121, 32]]"}, "gps", "longitude"),
        ({"gps": "[[120, 91], [120, 31], [121, 32]]"}, "gps", "latitude"),
        ({"time": "[1000, 1000, 1020]"}, "time", "strictly increasing"),
        ({"ptime": "[1001, 1000]"}, "ptime", "non-decreasing"),
        ({"time": "[1000, 1010]"}, "gps/time", "length mismatch"),
        ({"ptime": "[1001]"}, "road/ptime", "length mismatch"),
        ({"road_len": "1"}, "road_len", "declared 1"),
        ({"gps_len": "2"}, "gps_len", "declared 2"),
        ({"road": "__import__('os').system('id')"}, "road", "valid JSON"),
    ],
)
def test_rejects_invalid_trajectory_data(overrides, field, message):
    with pytest.raises(TrajectoryRowValidationError) as error:
        validate_trajectory_row(valid_row(**overrides))

    assert error.value.field == field
    assert message in error.value.message
