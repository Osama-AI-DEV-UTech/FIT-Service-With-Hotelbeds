"""
Hotelbeds TRANSFERS — Booking API (/transfer-api/1.0) and Cache/Content
API (/transfer-cache-api/1.0).

Location types: IATA (airport), ATLAS (Hotelbeds hotel code), GPS
("lat,lng"), PORT, STATION. Unsupported routes: IATA→IATA, PORT→PORT,
STATION→STATION and point-to-point ATLAS/GPS→ATLAS/GPS.
"""
from typing import Any, Optional
from urllib.parse import quote

from app.config import settings
from app.hotelbeds.client import HBResponse, HotelbedsClient

BOOKING = "/transfer-api/1.0"
CACHE = "/transfer-cache-api/1.0"

LOCATION_TYPES = ("IATA", "ATLAS", "GPS", "PORT", "STATION", "GIATA")


def availability_path(*, language: str, from_type: str, from_code: str, to_type: str, to_code: str,
                      outbound: str, inbound: Optional[str], adults: int, children: int, infants: int) -> str:
    fc = quote(str(from_code), safe=",.-")
    tc = quote(str(to_code), safe=",.-")
    path = f"{BOOKING}/availability/{language}/from/{from_type}/{fc}/to/{to_type}/{tc}/{outbound}"
    if inbound:
        path += f"/{inbound}"
    return f"{path}/{adults}/{children}/{infants}"


class TransfersAPI:
    def __init__(self, client: HotelbedsClient):
        self.c = client

    # ── Booking API: availability ───────────────────────────────────
    async def availability(self, *, language: str, from_type: str, from_code: str, to_type: str, to_code: str,
                           outbound: str, inbound: Optional[str] = None, adults: int, children: int = 0,
                           infants: int = 0) -> Any:
        path = availability_path(language=language, from_type=from_type, from_code=from_code, to_type=to_type,
                                 to_code=to_code, outbound=outbound, inbound=inbound, adults=adults,
                                 children=children, infants=infants)
        return (await self.c.request("GET", path)).data

    async def availability_routes(self, *, language: str, adults: int, children: int, infants: int,
                                  routes: list[dict], params: dict) -> Any:
        path = f"{BOOKING}/availability/routes/{language}/{adults}/{children}/{infants}"
        return (await self.c.request("POST", path, json=routes, params=params, idempotent=True)).data

    # ── Booking API: bookings ───────────────────────────────────────
    async def booking_confirm(self, payload: dict) -> Any:
        return (await self.c.request("POST", settings.HOTELBEDS_TRANSFER_BOOKING_PATH, json=payload,
                                     idempotent=False)).data

    async def booking_detail(self, reference: str, language: str) -> Any:
        return (await self.c.request("GET", f"{BOOKING}/bookings/{language}/reference/{reference}")).data

    async def booking_list(self, language: str, params: dict) -> Any:
        return (await self.c.request("GET", f"{BOOKING}/bookings/{language}", params=params)).data

    async def booking_cancel(self, reference: str, language: str, *, simulation: bool,
                             service_id: Optional[str] = None) -> Any:
        path = f"{BOOKING}/bookings/{language}/reference/{reference}"
        if service_id:
            path += f"/id/{service_id}"
        params = {"simulation": "true"} if simulation else None
        return (await self.c.request("DELETE", path, params=params, idempotent=simulation)).data

    async def booking_amend(self, payload: dict) -> Any:
        return (await self.c.request("PUT", settings.HOTELBEDS_TRANSFER_AMEND_PATH, json=payload,
                                     idempotent=False)).data

    # ── Cache / Content API ──────────────────────────────────────────
    async def cache(self, resource: str, params: dict) -> HBResponse:
        """resource examples: locations/countries, locations/destinations, locations/terminals, hotels,
        routes, masters/categories, masters/vehicles, masters/transferTypes, currencies."""
        return await self.c.request("GET", f"{CACHE}/{resource}", params=params)
