"""Exception handlers. Registered on EVERY sub-app (Starlette does not
propagate a parent app's handlers into mounted apps)."""
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import get_correlation_id
from app.core.exceptions import HotelbedsError, ServiceError

logger = structlog.get_logger("errors")


def _envelope(code: str, message: str, details=None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {},
                      "correlationId": get_correlation_id()}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HotelbedsError)
    async def _hotelbeds(_: Request, exc: HotelbedsError):
        logger.warning("hotelbeds_error_returned", code=exc.code, upstream_status=exc.upstream_status,
                       upstream_code=exc.upstream_code, path=exc.path)
        return JSONResponse(status_code=exc.http_status, content=_envelope(exc.code, exc.message, exc.details))

    @app.exception_handler(ServiceError)
    async def _service(_: Request, exc: ServiceError):
        if exc.http_status >= 500:
            logger.error("service_error", code=exc.code, message=exc.message)
        return JSONResponse(status_code=exc.http_status, content=_envelope(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        errors = []
        for e in exc.errors():
            errors.append({"loc": [str(x) for x in e.get("loc", [])], "msg": e.get("msg"), "type": e.get("type")})
        return JSONResponse(status_code=422, content=_envelope("REQUEST_VALIDATION_FAILED",
                                                               "Request body/parameters are invalid",
                                                               {"errors": errors}))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code,
                            content=_envelope(f"HTTP_{exc.status_code}", str(exc.detail)))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("unhandled_exception", error_type=type(exc).__name__)
        return JSONResponse(status_code=500, content=_envelope("INTERNAL_ERROR", "Unexpected server error"))
