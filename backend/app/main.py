import logging
from uuid import uuid4

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import Settings
from .errors import UploadError
from .schemas import ErrorResponse, UploadResult
from .services.upload_service import UploadService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


def create_app(settings: Settings = None) -> FastAPI:
    app_settings = settings or Settings.from_environment()
    upload_service = UploadService(
        app_settings.upload_root, app_settings.max_upload_bytes
    )
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
                    "message": "multipart field 'file' is required",
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
        "/api/uploads",
        response_model=UploadResult,
        status_code=201,
        responses={
            400: {"model": ErrorResponse},
            413: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    async def upload_trajectory_file(file: UploadFile = File(...)) -> UploadResult:
        return await upload_service.upload(file)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
