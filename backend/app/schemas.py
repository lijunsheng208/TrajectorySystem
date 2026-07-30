from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class UploadResult(BaseModel):
    upload_id: str
    file_name: str
    trajectory_count: int
    first_trajectory_id: Optional[str]
    last_trajectory_id: Optional[str]
    size_bytes: int
    sha256: str
    created_at: datetime


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail

