import logging
from uuid import uuid4
from typing import Optional

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import Settings
from .errors import UploadError
from .schemas import (
    ErrorResponse,
    EvaluationResult,
    EvaluationTrajectoryPage,
    TrajectoryDetail,
)
from .services.evaluation_service import EvaluationService
from .services.trajectory_query_service import EvaluationTrajectoryQueryService
from starlette.concurrency import run_in_threadpool


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


def create_app(settings: Settings = None) -> FastAPI:
    app_settings = settings or Settings.from_environment()
    evaluation_service = EvaluationService(
        app_settings.upload_root, app_settings.max_upload_bytes
    )
    evaluation_query_service = EvaluationTrajectoryQueryService(evaluation_service)
    app = FastAPI(title="Trajectory Similarity API", version="1.0.0")

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(UploadError)
    async def upload_error_handler(request: Request, exc: UploadError) -> JSONResponse:
        logger.info(
            "upload rejected request_id=%s code=%s", request.state.request_id, exc.code
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request.state.request_id,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "request parameters are invalid",
                    "request_id": request.state.request_id,
                }
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unexpected request failure request_id=%s", request.state.request_id
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "an unexpected server error occurred",
                    "request_id": request.state.request_id,
                }
            },
        )

    @app.post(
        "/api/evaluations",
        response_model=EvaluationResult,
        status_code=201,
    )
    async def create_evaluation() -> EvaluationResult:
        return await run_in_threadpool(evaluation_service.create)

    @app.get(
        "/api/evaluations/{evaluation_id}",
        response_model=EvaluationResult,
        responses={404: {"model": ErrorResponse}},
    )
    async def get_evaluation(evaluation_id: str) -> EvaluationResult:
        return await run_in_threadpool(evaluation_service.get, evaluation_id)

    @app.put(
        "/api/evaluations/{evaluation_id}/files/query",
        response_model=EvaluationResult,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            413: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )
    async def upload_query_file(
        evaluation_id: str, file: UploadFile = File(...)
    ) -> EvaluationResult:
        return await evaluation_service.upload_file(evaluation_id, "query", file)

    @app.put(
        "/api/evaluations/{evaluation_id}/files/query-sim",
        response_model=EvaluationResult,
        responses={
            404: {"model": ErrorResponse},
            413: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )
    async def upload_query_sim_file(
        evaluation_id: str, file: UploadFile = File(...)
    ) -> EvaluationResult:
        return await evaluation_service.upload_file(evaluation_id, "query-sim", file)

    @app.put(
        "/api/evaluations/{evaluation_id}/files/database",
        response_model=EvaluationResult,
        responses={
            404: {"model": ErrorResponse},
            413: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )
    async def upload_database_file(
        evaluation_id: str, file: UploadFile = File(...)
    ) -> EvaluationResult:
        return await evaluation_service.upload_file(evaluation_id, "database", file)

    async def list_role_trajectories(
        evaluation_id: str,
        role: str,
        page: int,
        page_size: int,
        search: Optional[str],
    ) -> EvaluationTrajectoryPage:
        return await run_in_threadpool(
            evaluation_query_service.list_trajectories,
            evaluation_id,
            role,
            page,
            page_size,
            search,
        )

    @app.get(
        "/api/evaluations/{evaluation_id}/trajectories/query",
        response_model=EvaluationTrajectoryPage,
        responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    )
    async def list_query_trajectories(
        evaluation_id: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        search: Optional[str] = Query(None, min_length=1, max_length=128),
    ) -> EvaluationTrajectoryPage:
        return await list_role_trajectories(
            evaluation_id, "query", page, page_size, search
        )

    @app.get(
        "/api/evaluations/{evaluation_id}/trajectories/query-sim",
        response_model=EvaluationTrajectoryPage,
        responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    )
    async def list_query_sim_trajectories(
        evaluation_id: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        search: Optional[str] = Query(None, min_length=1, max_length=128),
    ) -> EvaluationTrajectoryPage:
        return await list_role_trajectories(
            evaluation_id, "query-sim", page, page_size, search
        )

    @app.get(
        "/api/evaluations/{evaluation_id}/trajectories/database",
        response_model=EvaluationTrajectoryPage,
        responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    )
    async def list_database_trajectories(
        evaluation_id: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        search: Optional[str] = Query(None, min_length=1, max_length=128),
    ) -> EvaluationTrajectoryPage:
        return await list_role_trajectories(
            evaluation_id, "database", page, page_size, search
        )

    @app.get(
        "/api/evaluations/{evaluation_id}/trajectories/{trajectory_id}",
        response_model=TrajectoryDetail,
        responses={404: {"model": ErrorResponse}},
    )
    async def get_evaluation_trajectory(
        evaluation_id: str, trajectory_id: str
    ) -> TrajectoryDetail:
        return await run_in_threadpool(
            evaluation_query_service.get_trajectory,
            evaluation_id,
            trajectory_id,
        )

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
