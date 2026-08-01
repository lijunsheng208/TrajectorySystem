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
ROLE_FILES = {
    "query": ("query_traj_od.csv", "Q"),
    "query-sim": ("query_sim_traj_od.csv", "S"),
    "database": ("database_traj_od.csv", "D"),
}


def csv_bytes(users=("user-1", "user-2"), call_type="0"):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    for user in users:
        writer.writerow(
            [
                user,
                call_type,
                2,
                3,
                "[101,102]",
                "[[-8.6,41.1],[-8.61,41.11],[-8.62,41.12]]",
                "[1000,1010,1020]",
                "[1001,1001]",
            ]
        )
    return output.getvalue().encode("utf-8")


def make_client(tmp_path):
    settings = Settings(
        upload_root=tmp_path / "uploads", max_upload_bytes=1024 * 1024
    )
    return TestClient(create_app(settings))


def create_evaluation(client):
    response = client.post("/api/evaluations")
    assert response.status_code == 201
    return response.json()["evaluation_id"]


def upload_role(client, evaluation_id, role, content=None):
    filename, _ = ROLE_FILES[role]
    return client.put(
        f"/api/evaluations/{evaluation_id}/files/{role}",
        files={"file": (filename, content or csv_bytes(), "text/csv")},
    )


def test_uploads_three_roles_with_independent_identifiers(tmp_path):
    client = make_client(tmp_path)
    evaluation_id = create_evaluation(client)

    for role, (_, prefix) in ROLE_FILES.items():
        response = upload_role(client, evaluation_id, role)
        assert response.status_code == 200
        result = response.json()["files"][role]
        assert result["first_trajectory_id"] == f"{prefix}000001"
        assert result["last_trajectory_id"] == f"{prefix}000002"

    status = client.get(f"/api/evaluations/{evaluation_id}").json()
    assert status["status"] == "ready"
    assert set(status["files"]) == set(ROLE_FILES)


def test_endpoint_role_does_not_depend_on_client_filename(tmp_path):
    client = make_client(tmp_path)
    evaluation_id = create_evaluation(client)

    response = client.put(
        f"/api/evaluations/{evaluation_id}/files/database",
        files={"file": ("renamed.csv", csv_bytes(), "text/csv")},
    )

    assert response.status_code == 200
    result = response.json()["files"]["database"]
    assert result["file_name"] == "database_traj_od.csv"
    assert result["first_trajectory_id"] == "D000001"


def test_query_pair_mismatch_is_rejected_without_committing_role(tmp_path):
    client = make_client(tmp_path)
    evaluation_id = create_evaluation(client)
    assert upload_role(client, evaluation_id, "query").status_code == 200

    response = upload_role(
        client,
        evaluation_id,
        "query-sim",
        csv_bytes(users=("different-user", "user-2")),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PAIR_DATA_MISMATCH"
    status = client.get(f"/api/evaluations/{evaluation_id}").json()
    assert set(status["files"]) == {"query"}


def test_lists_searches_and_gets_role_trajectory(tmp_path):
    client = make_client(tmp_path)
    evaluation_id = create_evaluation(client)
    assert upload_role(client, evaluation_id, "database").status_code == 200

    page = client.get(
        f"/api/evaluations/{evaluation_id}/trajectories/database",
        params={"page": 1, "page_size": 1},
    )
    search = client.get(
        f"/api/evaluations/{evaluation_id}/trajectories/database",
        params={"search": "user-2"},
    )
    detail = client.get(
        f"/api/evaluations/{evaluation_id}/trajectories/D000002"
    )

    assert page.status_code == 200
    assert page.json()["items"][0]["trajectory_id"] == "D000001"
    assert page.json()["total_pages"] == 2
    assert search.json()["items"][0]["trajectory_id"] == "D000002"
    assert detail.status_code == 200
    assert detail.json()["trajectory_id"] == "D000002"
    assert detail.json()["gps_len"] == 3


def test_unuploaded_role_returns_not_found(tmp_path):
    client = make_client(tmp_path)
    evaluation_id = create_evaluation(client)

    response = client.get(
        f"/api/evaluations/{evaluation_id}/trajectories/query",
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EVALUATION_FILE_NOT_FOUND"


def test_legacy_upload_api_is_removed(tmp_path):
    client = make_client(tmp_path)

    response = client.post(
        "/api/uploads",
        files={"file": ("query_traj_od.csv", csv_bytes(), "text/csv")},
    )

    assert response.status_code == 404
