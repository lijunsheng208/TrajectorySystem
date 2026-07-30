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


def upload_three_trajectories(client: TestClient) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    for index in range(3):
        writer.writerow(
            [
                f"user-{index + 1}",
                str(index % 2),
                2,
                3,
                "[101, 102]",
                "[[120.1,30.1],[120.2,30.2],[120.3,30.3]]",
                "[1000,1010,1020]",
                "[1001,1001]",
            ]
        )
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
    assert response.status_code == 201
    return response.json()["upload_id"]


def make_client(tmp_path) -> TestClient:
    settings = Settings(
        upload_root=tmp_path / "uploads", max_upload_bytes=1024 * 1024
    )
    return TestClient(create_app(settings))


def test_lists_trajectories_with_pagination(tmp_path):
    client = make_client(tmp_path)
    upload_id = upload_three_trajectories(client)

    first_page = client.get(
        f"/api/uploads/{upload_id}/trajectories", params={"page": 1, "page_size": 2}
    )
    second_page = client.get(
        f"/api/uploads/{upload_id}/trajectories", params={"page": 2, "page_size": 2}
    )

    assert first_page.status_code == 200
    assert first_page.json() == {
        "upload_id": upload_id,
        "page": 1,
        "page_size": 2,
        "total": 3,
        "total_pages": 2,
        "items": [
            {
                "trajectory_id": "Q000001",
                "user_id": "user-1",
                "call_type": "0",
                "road_len": 2,
                "gps_len": 3,
            },
            {
                "trajectory_id": "Q000002",
                "user_id": "user-2",
                "call_type": "1",
                "road_len": 2,
                "gps_len": 3,
            },
        ],
    }
    assert [item["trajectory_id"] for item in second_page.json()["items"]] == [
        "Q000003"
    ]


def test_returns_empty_page_after_last_page(tmp_path):
    client = make_client(tmp_path)
    upload_id = upload_three_trajectories(client)

    response = client.get(
        f"/api/uploads/{upload_id}/trajectories", params={"page": 3, "page_size": 2}
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total_pages"] == 2


def test_searches_all_trajectories_by_id_or_user_id(tmp_path):
    client = make_client(tmp_path)
    upload_id = upload_three_trajectories(client)

    by_trajectory = client.get(
        f"/api/uploads/{upload_id}/trajectories", params={"search": "q000003"}
    )
    by_user = client.get(
        f"/api/uploads/{upload_id}/trajectories", params={"search": "user-2"}
    )

    assert by_trajectory.status_code == 200
    assert by_trajectory.json()["total"] == 1
    assert by_trajectory.json()["items"][0]["trajectory_id"] == "Q000003"
    assert by_user.status_code == 200
    assert by_user.json()["total"] == 1
    assert by_user.json()["items"][0]["user_id"] == "user-2"


def test_search_returns_empty_result(tmp_path):
    client = make_client(tmp_path)
    upload_id = upload_three_trajectories(client)

    response = client.get(
        f"/api/uploads/{upload_id}/trajectories", params={"search": "missing-user"}
    )

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["total_pages"] == 0
    assert response.json()["items"] == []


def test_returns_trajectory_detail(tmp_path):
    client = make_client(tmp_path)
    upload_id = upload_three_trajectories(client)

    response = client.get(
        f"/api/uploads/{upload_id}/trajectories/Q000002"
    )

    assert response.status_code == 200
    assert response.json() == {
        "trajectory_id": "Q000002",
        "user_id": "user-2",
        "call_type": "1",
        "road_len": 2,
        "gps_len": 3,
        "road": [101, 102],
        "gps": [[120.1, 30.1], [120.2, 30.2], [120.3, 30.3]],
        "time": [1000.0, 1010.0, 1020.0],
        "ptime": [1001.0, 1001.0],
    }


def test_rejects_invalid_pagination(tmp_path):
    client = make_client(tmp_path)
    upload_id = upload_three_trajectories(client)

    response = client.get(
        f"/api/uploads/{upload_id}/trajectories", params={"page": 0, "page_size": 101}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_returns_not_found_for_unknown_upload(tmp_path):
    client = make_client(tmp_path)

    response = client.get(f"/api/uploads/{'0' * 32}/trajectories")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UPLOAD_NOT_FOUND"


def test_returns_not_found_for_unknown_or_invalid_trajectory(tmp_path):
    client = make_client(tmp_path)
    upload_id = upload_three_trajectories(client)

    missing = client.get(f"/api/uploads/{upload_id}/trajectories/Q000004")
    invalid = client.get(f"/api/uploads/{upload_id}/trajectories/not-an-id")

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "TRAJECTORY_NOT_FOUND"
    assert invalid.status_code == 404
    assert invalid.json()["error"]["code"] == "TRAJECTORY_NOT_FOUND"
