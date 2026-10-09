"""
Typed exceptions. Every error that leaves the service is one of these,
rendered by app.core.errors into a single JSON error envelope:

    {"error": {"code": "...", "message": "...", "details": {...}, "correlationId": "..."}}
"""
from typing import Any, Optional


class ServiceError(Exception):
    http_status: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, *, details: Optional[dict[str, Any]] = None, code: Optional[str] = None,
                 http_status: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status


class ValidationFailed(ServiceError):
    http_status = 422
    code = "VALIDATION_FAILED"


class NotFound(ServiceError):
    http_status = 404
    code = "NOT_FOUND"


class Conflict(ServiceError):
    http_status = 409
    code = "CONFLICT"


class ProviderNotConfigured(ServiceError):
    http_status = 503
    code = "HOTELBEDS_NOT_CONFIGURED"


class LocationNotResolved(ServiceError):
    http_status = 422
    code = "LOCATION_NOT_RESOLVED"


class HotelbedsError(ServiceError):
    """Any non-2xx (or 2xx-with-errors) answer from Hotelbeds.

    `upstream_status` is the HTTP status Hotelbeds returned (0 = network
    failure / timeout), `upstream_code` the Hotelbeds error code when one
    was present (INVALID_REQUEST, E_REQUEST_PASTDATE, ...).
    """

    code = "HOTELBEDS_ERROR"

    def __init__(self, message: str, *, product: str, path: str, upstream_status: int,
                 upstream_code: Optional[str] = None, body: Any = None):
        self.product = product
        self.path = path
        self.upstream_status = upstream_status
        self.upstream_code = upstream_code
        self.body = body
        http_status, code = self._classify(upstream_status)
        super().__init__(
            message,
            code=code,
            http_status=http_status,
            details={
                "product": product,
                "path": path,
                "upstreamStatus": upstream_status,
                "upstreamCode": upstream_code,
                "upstreamBody": body,
            },
        )

    @staticmethod
    def _classify(status: int) -> tuple[int, str]:
        if status == 0:
            return 504, "HOTELBEDS_UNREACHABLE"
        if status in (401, 403):
            # Our credentials / quota problem, never the caller's.
            return 502, "HOTELBEDS_AUTH_OR_QUOTA"
        if status == 429:
            return 429, "HOTELBEDS_RATE_LIMITED"
        if status == 404:
            return 404, "HOTELBEDS_NOT_FOUND"
        if status == 409:
            return 409, "HOTELBEDS_CONFLICT"
        if 400 <= status < 500:
            return 400, "HOTELBEDS_REJECTED_REQUEST"
        return 502, "HOTELBEDS_UPSTREAM_ERROR"

    @property
    def is_timeout_or_network(self) -> bool:
        return self.upstream_status == 0
