"""
Hotelbeds HOTELS — Booking API (/hotel-api/1.0) + Content API
(/hotel-content-api/1.0). Thin, typed-by-docstring wrappers: payloads are
built by callers (product endpoints pass the validated request through,
the FIT engine builds its own), responses are returned as Hotelbeds JSON.
"""
from typing import Any, Optional

from app.hotelbeds.client import HotelbedsClient

BOOKING = "/hotel-api/1.0"
BOOKING_V12 = "/hotel-api/1.2"
CONTENT = "/hotel-content-api/1.0"

CONTENT_TYPES = (
    "accommodations", "boards", "boardgroups", "categories", "chains", "classifications", "currencies",
    "facilities", "facilitygroups", "facilitytypologies", "groupcategories", "imagetypes", "issues",
    "languages", "promotions", "ratecomments", "ratecommentdetails", "rooms", "segments", "terminals",
)

IMAGE_BASE_URL = "https://photos.hotelbeds.com/giata/"
IMAGE_SIZES = {"standard": "", "small": "small/", "medium": "medium/", "bigger": "bigger/", "xl": "xl/",
               "xxl": "xxl/", "original": "original/"}


def image_url(path: str, size: str = "bigger") -> str:
    return f"{IMAGE_BASE_URL}{IMAGE_SIZES.get(size, '')}{path.lstrip('/')}"


class HotelsAPI:
    def __init__(self, client: HotelbedsClient):
        self.c = client

    # ── Booking API ──────────────────────────────────────────────────
    async def status(self) -> Any:
        return (await self.c.request("GET", f"{BOOKING}/status")).data

    async def availability(self, payload: dict) -> Any:
        """POST /hotels — read-only search, safe to retry."""
        return (await self.c.request("POST", f"{BOOKING}/hotels", json=payload, idempotent=True)).data

    async def checkrates(self, payload: dict) -> Any:
        """POST /checkrates — re-prices rateKeys (mandatory for RECHECK rates)."""
        return (await self.c.request("POST", f"{BOOKING}/checkrates", json=payload, idempotent=True)).data

    async def booking_confirm(self, payload: dict, *, use_v12: bool = False) -> Any:
        """POST /bookings — money-moving: never retried. Bookings carrying
        paymentData go to the secure host."""
        secure = bool(payload.get("paymentData"))
        base = BOOKING_V12 if use_v12 else BOOKING
        return (await self.c.request("POST", f"{base}/bookings", json=payload, idempotent=False,
                                     secure=secure)).data

    async def booking_list(self, params: dict) -> Any:
        return (await self.c.request("GET", f"{BOOKING}/bookings", params=params)).data

    async def booking_detail(self, reference: str, language: Optional[str] = None) -> Any:
        return (await self.c.request("GET", f"{BOOKING}/bookings/{reference}",
                                     params={"language": language})).data

    async def booking_modify(self, reference: str, payload: dict) -> Any:
        simulation = str(payload.get("mode", "")).upper() == "SIMULATION"
        return (await self.c.request("PUT", f"{BOOKING}/bookings/{reference}", json=payload,
                                     idempotent=simulation)).data

    async def booking_cancel(self, reference: str, *, simulation: bool, language: Optional[str] = None) -> Any:
        flag = "SIMULATION" if simulation else "CANCELLATION"
        return (await self.c.request("DELETE", f"{BOOKING}/bookings/{reference}",
                                     params={"cancellationFlag": flag, "language": language},
                                     idempotent=simulation)).data

    # ── Content API ──────────────────────────────────────────────────
    async def content_hotels(self, params: dict) -> Any:
        return (await self.c.request("GET", f"{CONTENT}/hotels", params=params)).data

    async def content_hotel_details(self, codes: str, params: dict) -> Any:
        return (await self.c.request("GET", f"{CONTENT}/hotels/{codes}/details", params=params)).data

    async def content_countries(self, params: dict) -> Any:
        return (await self.c.request("GET", f"{CONTENT}/locations/countries", params=params)).data

    async def content_destinations(self, params: dict) -> Any:
        return (await self.c.request("GET", f"{CONTENT}/locations/destinations", params=params)).data

    async def content_type(self, type_name: str, params: dict) -> Any:
        if type_name not in CONTENT_TYPES:
            raise ValueError(f"Unknown content type '{type_name}'")
        return (await self.c.request("GET", f"{CONTENT}/types/{type_name}", params=params)).data
