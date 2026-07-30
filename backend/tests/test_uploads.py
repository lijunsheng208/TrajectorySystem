import csv
import io

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


HEADERS = [
    "user_id",
    "call_type",
    "road_len",
    "gps_len",
    "road",
    "gps",
    "time",
    "ptime",
]


def csv_bytes(headers=HEADERS, row_count=2):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    for index in range(row_count):
        writer.writerow(
            [index + 1, 0, 1, 2, "[10]", "[[1,2],[3,4]]", "[1,2]", "[1]"]
        )
    return output.getvalue().encode("utf-8")


def make_client(tmp_path, max_bytes=1024 * 1024):
    app = create_app(Settings(upload_root=tmp_path / "uploads", max_upload_bytes=max_bytes))
    return TestClient(app), tmp_path / "uploads"


def test_upload_saves_file_and_generates_index(tmp_path):
    client, upload_root = make_client(tmp_path)

    response = client.post(
        "/api/uploads",
        files={"file": ("query_traj_od.csv", csv_bytes(), "text/csv")},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["trajectory_count"] == 2
    assert result["first_trajectory_id"] == "Q000001"
    assert result["last_trajectory_id"] == "Q000002"
    upload_dir = upload_root / result["upload_id"]
    assert (upload_dir / "query_traj_od.csv").is_file()
    assert (upload_dir / "metadata.json").is_file()
    assert (upload_dir / "trajectory_index.csv").read_text().splitlines() == [
        "trajectory_id,csv_row_number",
        "Q000001,2",
        "Q000002,3",
    ]


def test_rejects_wrong_file_name(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post(
        "/api/uploads", files={"file": ("other.csv", csv_bytes(), "text/csv")}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_FILE_NAME"


def test_rejects_missing_header_without_committing_file(tmp_path):
    client, upload_root = make_client(tmp_path)
    response = client.post(
        "/api/uploads",
        files={"file": ("query_traj_od.csv", csv_bytes(HEADERS[:-1]), "text/csv")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MISSING_HEADERS"
    committed = [path for path in upload_root.iterdir() if path.name != ".staging"]
    assert committed == []


def test_rejects_file_over_size_limit(tmp_path):
    content = csv_bytes()
    client, _ = make_client(tmp_path, max_bytes=len(content) - 1)
    response = client.post(
        "/api/uploads",
        files={"file": ("query_traj_od.csv", content, "text/csv")},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_requires_file_part(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post("/api/uploads")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_invalid_trajectory_data_reports_row_and_does_not_commit(tmp_path):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    writer.writerow(
        [1, 0, 1, 2, "[10]", "[[1,2],[3,4]]", "[2,1]", "[1]"]
    )
    client, upload_root = make_client(tmp_path)

    response = client.post(
        "/api/uploads",
        files={
            "file": (
                "query_traj_od.csv",
                output.getvalue().encode("utf-8"),
                "text/csv",
            )
        },
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_TRAJECTORY_DATA"
    assert "CSV row 2, field time" in error["message"]
    committed = [path for path in upload_root.iterdir() if path.name != ".staging"]
    assert committed == []
