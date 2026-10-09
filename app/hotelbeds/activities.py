"""
Hotelbeds ACTIVITIES — Booking API (/activity-api/3.0), Content API
(/activity-content-api/3.0) and Cache API (/activity-cache-api/1.0).

Flow per Hotelbeds: search/availability → details (fresh rateKey, valid
30 min, single use) → confirm (PUT /bookings). Preconfirm locks stock for
30 minutes and is completed with reconfirm.
"""
from typing import Any, Optional

from app.hotelbeds.client import HBResponse, HotelbedsClient

BOOKING = "/activity-api/3.0"
CONTENT = "/activity-content-api/3.0"
CACHE = "/activity-cache-api/1.0"


class ActivitiesAPI:
    def __init__(self, client: HotelbedsClient):
        self.c = client

    # ── Booking API: availability ───────────────────────────────────
    async def search(self, payload: dict) -> Any:
        """POST /activities — lightweight search."""
        return (await self.c.request("POST", f"{BOOKING}/activities", json=payload, idempotent=True)).data

    async def availability(self, payload: dict) -> Any:
        """POST /activities/availability — complete availability (needs paxes)."""
        return (await self.c.request("POST", f"{BOOKING}/activities/availability", json=payload,
                                     idempotent=True)).data

    async def details(self, payload: dict, *, full: bool = False) -> Any:
        """POST /activities/details[/full] — the ONLY valid source of a bookable rateKey."""
        path = f"{BOOKING}/activities/details/full" if full else f"{BOOKING}/activities/details"
        return (await self.c.request("POST", path, json=payload, idempotent=True)).data

    # ── Booking API: bookings ───────────────────────────────────────
    async def booking_confirm(self, payload: dict) -> Any:
        return (await self.c.request("PUT", f"{BOOKING}/bookings", json=payload, idempotent=False)).data

    async def booking_preconfirm(self, payload: dict) -> Any:
        return (await self.c.request("PUT", f"{BOOKING}/bookings/preconfirm", json=payload,
                                     idempotent=False)).data

    async def booking_reconfirm(self, payload: dict) -> Any:
        return (await self.c.request("PUT", f"{BOOKING}/bookings/reconfirm", json=payload,
                                     idempotent=False)).data

    async def booking_detail(self, reference: str, language: str) -> Any:
        return (await self.c.request("GET", f"{BOOKING}/bookings/{language}/{reference}")).data

    async def booking_list(self, language: str, params: dict) -> Any:
        return (await self.c.request("GET", f"{BOOKING}/bookings/{language}", params=params)).data

    async def booking_cancel(self, reference: str, language: str, *, simulation: bool) -> Any:
        flag = "SIMULATION" if simulation else "CANCELLATION"
        return (await self.c.request("DELETE", f"{BOOKING}/bookings/{language}/{reference}",
                                     params={"cancellationFlag": flag}, idempotent=simulation)).data

    # ── Content API ──────────────────────────────────────────────────
    async def content_countries(self, language: str) -> Any:
        return (await self.c.request("GET", f"{CONTENT}/countries/{language}")).data

    async def content_destinations(self, language: str, country_code: str) -> Any:
        return (await self.c.request("GET", f"{CONTENT}/destinations/{language}/{country_code}")).data

    async def content_currencies(self, language: str) -> Any:
        return (await self.c.request("GET", f"{CONTENT}/currencies/{language}")).data

    async def content_languages(self) -> Any:
        return (await self.c.request("GET", f"{CONTENT}/languages")).data

    async def content_segments(self, language: str) -> Any:
        return (await self.c.request("GET", f"{CONTENT}/segments/{language}")).data

    async def content_activity(self, language: str, activity_code: str, modality_code: Optional[str] = None) -> Any:
        path = f"{CONTENT}/activities/{language}/{activity_code}"
        if modality_code:
            path += f"/{modality_code}"
        return (await self.c.request("GET", path)).data

    async def content_activities_multi(self, payload: dict) -> Any:
        return (await self.c.request("POST", f"{CONTENT}/activities", json=payload, idempotent=True)).data

    # ── Cache API ────────────────────────────────────────────────────
    async def cache_portfolio(self, destination: str, offset: int, limit: int) -> HBResponse:
        """Returns the raw HBResponse so callers can read X-Total-Count."""
        return await self.c.request("GET", f"{CACHE}/portfolio",
                                    params={"destination": destination, "offset": offset, "limit": limit})
