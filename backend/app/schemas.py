from datetime import datetime
from typing import Any, Dict, List

from pydantic import BaseModel


class TrajectorySummary(BaseModel):
    trajectory_id: str
    user_id: str
    call_type: str
    road_len: int
    gps_len: int


class EvaluationFileResult(BaseModel):
    role: str
    file_name: str
    trajectory_count: int
    first_trajectory_id: str
    last_trajectory_id: str
    size_bytes: int
    sha256: str
    created_at: datetime


class EvaluationResult(BaseModel):
    evaluation_id: str
    status: str
    files: Dict[str, EvaluationFileResult]
    created_at: datetime
    updated_at: datetime


class EvaluationTrajectoryPage(BaseModel):
    evaluation_id: str
    role: str
    page: int
    page_size: int
    total: int
    total_pages: int
    items: List[TrajectorySummary]


class TrajectoryDetail(TrajectorySummary):
    road: List[Any]
    gps: List[List[float]]
    time: List[float]
    ptime: List[float]


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
