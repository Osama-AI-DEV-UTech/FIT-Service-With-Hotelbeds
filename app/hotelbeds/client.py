"""
Low-level Hotelbeds APItude HTTP client — one instance per product line.

Auth (developer.hotelbeds.com → Getting started):
    Api-key:     <apiKey>
    X-Signature: sha256_hex(apiKey + secret + unix_timestamp_seconds)
recomputed on EVERY attempt (it is only valid around its own timestamp),
plus `Accept: application/json`, `Accept-Encoding: gzip` (checked during
certification) and `Content-Type: application/json` on bodies.

Retry policy — deliberately asymmetric:
  * Safe/idempotent calls (searches, content, details, GET): retried with
    exponential backoff on network errors, 429 and 5xx.
  * Money-moving calls (booking confirm, cancellation, modification):
    NEVER retried on network errors/5xx — a timeout does not mean the
    booking failed, and a blind retry can double-book. Only 429 (request
    rejected before processing) is retried. Callers reconcile instead.

Error bodies differ per product and per layer, all are normalised into
HotelbedsError:
    hotels       {"auditData":{..},"error":{"code":"INVALID_REQUEST","message":".."}}
    activities   {"errors":[{"code":"E_REQUEST_PASTDATE","text":".."}], ...}  (sometimes with HTTP 200!)
    gateway      {"error":"Request signature verification failed"}
    transfers    {"code":"..","message":".."} / {"errors":[...]} / free text
"""
import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx
import structlog

from app.core.context import current_ledger
from app.core.exceptions import HotelbedsError

logger = structlog.get_logger("hotelbeds")

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class HBResponse:
    data: Any
    status: int
    headers: httpx.Headers

    def header_int(self, name: str) -> Optional[int]:
        try:
            return int(self.headers.get(name))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None


class HotelbedsClient:
    def __init__(
        self,
        *,
        product: str,
        base_url: str,
        api_key: str,
        secret: str,
        secure_base_url: Optional[str] = None,
        timeout: float = 60.0,
        connect_timeout: float = 10.0,
        max_retries: int = 2,
        retry_base_delay: float = 0.8,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        if not api_key or not secret:
            raise ValueError(f"Hotelbeds credentials missing for product '{product}'")
        self.product = product
        self.base_url = base_url.rstrip("/")
        self.secure_base_url = (secure_base_url or base_url).rstrip("/")
        self._api_key = api_key
        self._secret = secret
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ── auth ─────────────────────────────────────────────────────────
    def signature(self, timestamp: Optional[int] = None) -> str:
        ts = int(time.time()) if timestamp is None else timestamp
        return hashlib.sha256(f"{self._api_key}{self._secret}{ts}".encode()).hexdigest()

    def _headers(self, has_body: bool) -> dict[str, str]:
        headers = {
            "Api-key": self._api_key,
            "X-Signature": self.signature(),
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }
        if has_body:
            headers["Content-Type"] = "application/json"
        return headers

    # ── core ─────────────────────────────────────────────────────────
    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json: Any = None,
        idempotent: Optional[bool] = None,
        secure: bool = False,
    ) -> HBResponse:
        """Perform one logical call (with retries where safe)."""
        if idempotent is None:
            idempotent = method.upper() == "GET"
        base = self.secure_base_url if secure else self.base_url
        url = f"{base}{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        attempt = 0
        log = logger.bind(product=self.product, method=method, path=path)

        while True:
            started = time.perf_counter()
            try:
                resp = await self._http.request(
                    method, url, params=clean_params or None, json=json, headers=self._headers(json is not None)
                )
            except httpx.TimeoutException as exc:
                self._ledger(method, path, 0, started)
                if idempotent and attempt < self.max_retries:
                    attempt += 1
                    await self._backoff(log, attempt, reason="timeout")
                    continue
                log.error("hotelbeds_timeout", attempts=attempt + 1)
                raise HotelbedsError(
                    f"Hotelbeds {self.product} timed out on {method} {path}"
                    + ("" if idempotent else " — outcome UNKNOWN, reconcile before retrying"),
                    product=self.product, path=path, upstream_status=0, upstream_code="TIMEOUT",
                ) from exc
            except httpx.RequestError as exc:
                self._ledger(method, path, 0, started)
                if idempotent and attempt < self.max_retries:
                    attempt += 1
                    await self._backoff(log, attempt, reason=type(exc).__name__)
                    continue
                log.error("hotelbeds_network_error", error=str(exc))
                raise HotelbedsError(
                    f"Network error calling Hotelbeds {self.product}: {exc}",
                    product=self.product, path=path, upstream_status=0, upstream_code="NETWORK_ERROR",
                ) from exc

            self._ledger(method, path, resp.status_code, started)
            status = resp.status_code
            retry_allowed = status == 429 or (idempotent and status in _RETRYABLE_STATUSES)
            if retry_allowed and attempt < self.max_retries:
                attempt += 1
                retry_after = self._retry_after(resp)
                await self._backoff(log, attempt, reason=f"http_{status}", min_delay=retry_after)
                continue

            body = self._parse_body(resp)
            if status >= 400:
                code, message = self._extract_error(body)
                log.warning("hotelbeds_error", status=status, upstream_code=code, message=message)
                raise HotelbedsError(
                    message or f"Hotelbeds {self.product} returned HTTP {status}",
                    product=self.product, path=path, upstream_status=status, upstream_code=code,
                    body=body,
                )

            # Activities can answer HTTP 200 with a populated `errors` array.
            if isinstance(body, dict) and body.get("errors"):
                errs = body["errors"]
                if isinstance(errs, list) and errs and not self._has_payload(body):
                    code, message = self._extract_error(body)
                    raise HotelbedsError(
                        message or "Hotelbeds returned errors", product=self.product, path=path,
                        upstream_status=400, upstream_code=code, body=body,
                    )
            return HBResponse(data=body, status=status, headers=resp.headers)

    # ── helpers ──────────────────────────────────────────────────────
    async def _backoff(self, log, attempt: int, reason: str, min_delay: float = 0.0) -> None:
        delay = max(min_delay, self.retry_base_delay * (2 ** (attempt - 1)))
        log.warning("hotelbeds_retrying", attempt=attempt, delay_s=round(delay, 2), reason=reason)
        await asyncio.sleep(delay)

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float:
        try:
            return min(float(resp.headers.get("Retry-After", 0)), 30.0)
        except ValueError:
            return 0.0

    def _ledger(self, method: str, path: str, status: int, started: float) -> None:
        ledger = current_ledger()
        if ledger is not None:
            ledger.record(self.product, method, path, status, (time.perf_counter() - started) * 1000)

    @staticmethod
    def _parse_body(resp: httpx.Response) -> Any:
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text[:2000]}

    @staticmethod
    def _has_payload(body: dict) -> bool:
        payload_keys = {"activities", "activity", "booking", "bookings", "activitiesContent", "countries",
                        "country", "currencies", "languages", "segmentationGroups"}
        return any(body.get(k) for k in payload_keys)

    @staticmethod
    def _extract_error(body: Any) -> tuple[Optional[str], Optional[str]]:
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                return err.get("code"), err.get("message") or err.get("text")
            if isinstance(err, str):
                return None, err
            errs = body.get("errors")
            if isinstance(errs, list) and errs:
                first = errs[0] if isinstance(errs[0], dict) else {"text": str(errs[0])}
                text = "; ".join(
                    (e.get("text") or e.get("message") or e.get("description") or str(e))
                    if isinstance(e, dict) else str(e) for e in errs
                )
                return first.get("code"), text
            if body.get("message") or body.get("code"):
                return body.get("code"), body.get("message") or body.get("description")
            if body.get("raw"):
                return None, str(body["raw"])[:500]
        return None, None
