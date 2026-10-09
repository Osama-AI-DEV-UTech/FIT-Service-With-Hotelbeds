"""Pure-ASGI middleware (no BaseHTTPMiddleware, so contextvars set here are
visible inside every endpoint, including mounted sub-apps)."""
import time

import structlog

from app.core.context import new_correlation_id, set_correlation_id, start_ledger

logger = structlog.get_logger("http")

HEADER = b"x-correlation-id"


class CorrelationAndLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(HEADER)
        cid = incoming.decode()[:64] if incoming else new_correlation_id()
        set_correlation_id(cid)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=cid)
        start_ledger()
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers") or [])
                headers.append((HEADER, cid.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            if not path.endswith(("/docs", "/openapi.json", "/redoc")) and "/static/" not in path:
                logger.info(
                    "http_request",
                    method=scope.get("method"),
                    path=path,
                    status=status_holder["status"],
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )
