"""/fit — FIT package engine (docs + form at /fit/docs, HTML form at /fit/form)."""
import os
from typing import Optional

from fastapi import Body, Depends, Query
from fastapi.responses import FileResponse, RedirectResponse

from app.apis.common import container, create_subapp
from app.apis.examples import CONFIRM_EXAMPLE, FIT_MULTI_CITY, FIT_SINGLE_CITY
from app.container import Container
from app.core.exceptions import NotFound
from app.fit.booking_service import BookingService
from app.fit.orchestrator import FITOrchestrator
from app.schemas.fit import (CancelPackageRequest, ConfirmPackageRequest, FITPackageResponse, FITRequest,
                             PrebookRequest)

DESCRIPTION = """
**Fully Independent Travel (FIT) package engine — powered 100% by Hotelbeds.**

👉 **Interactive FIT form:** [/fit/form](/fit/form) · Request below is pre-filled — open
`POST /api/v1/packages` → *Try it out* → pick an example → *Execute*.

### How a package is built
1. **Hotels** — every destination is resolved to a Hotelbeds destination code (+ zone from `accommodation.location`)
   and searched once; rooms are fitted to the party (`occupancies`), scored on stars, room/bed type, area, brand,
   refundability and budget.
2. **Activities** — each requested excursion is matched against Hotelbeds Activities on its date/day-part/language;
   free days can get optional suggestions.
3. **Transfers** — arrival (airport/station → hotel) and departure (hotel → airport/station) using the selected
   hotel's Hotelbeds code (ATLAS) for exact pickup/drop-off.
4. **Package** — day-by-day itinerary, pricing (net / selling / markup), budget check, cancellation summary,
   admin advisories. Persisted, bookable for `PACKAGE_TTL_MINUTES`.

### Lifecycle
`POST /packages` → (optional) `POST /packages/{id}/prebook` → `POST /packages/{id}/confirm`
→ `GET /packages/{id}/bookings` → `POST /packages/{id}/cancel` (simulate first).

Product APIs: [/hotels/docs](/hotels/docs) · [/activities/docs](/activities/docs) · [/transfers/docs](/transfers/docs)
"""

TAGS = [{"name": "FIT Packages"}, {"name": "Booking"}, {"name": "Form & Templates"}]

app = create_subapp(title="Ubidtours FIT Service — Hotelbeds", description=DESCRIPTION, openapi_tags=TAGS)
P = "/api/v1"
_FORM = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "fit_form.html")


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="form")


@app.get("/form", tags=["Form & Templates"], summary="Interactive FIT request form (HTML)",
         response_class=FileResponse)
async def form():
    return FileResponse(_FORM, media_type="text/html")


@app.get(f"{P}/request-template", tags=["Form & Templates"], summary="Complete FIT request example")
async def request_template(kind: str = Query("multi_city", pattern="^(multi_city|single_city)$")):
    return FIT_MULTI_CITY if kind == "multi_city" else FIT_SINGLE_CITY


@app.post(f"{P}/packages", tags=["FIT Packages"], response_model=FITPackageResponse,
          summary="Generate a FIT package (hotels → activities → transfers → package)")
async def generate_package(
    req: FITRequest = Body(..., openapi_examples={
        "multi_city_family": {"summary": "Dubai → Istanbul, family of 3", "value": FIT_MULTI_CITY},
        "single_city_couple": {"summary": "Dubai, luxury couple", "value": FIT_SINGLE_CITY},
    }),
    c: Container = Depends(container),
):
    return await FITOrchestrator(c).generate(req)


@app.post(f"{P}/generate", tags=["FIT Packages"], response_model=FITPackageResponse,
          summary="Alias of POST /packages (backward compatible with the previous service)")
async def generate_alias(req: FITRequest = Body(..., openapi_examples={
        "multi_city_family": {"summary": "Dubai → Istanbul, family of 3", "value": FIT_MULTI_CITY}}),
        c: Container = Depends(container)):
    return await FITOrchestrator(c).generate(req)


@app.get(f"{P}/packages", tags=["FIT Packages"], summary="List generated packages")
async def list_packages(limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0),
                        status: Optional[str] = None, c: Container = Depends(container)):
    return {"items": await c.store.list_packages(limit=limit, offset=offset, status=status),
            "limit": limit, "offset": offset}


@app.get(f"{P}/packages/{{package_id}}", tags=["FIT Packages"], summary="Get a package (with booking state)")
async def get_package(package_id: str, include_request: bool = False, c: Container = Depends(container)):
    rec = await c.store.get_package(package_id)
    if rec is None:
        raise NotFound(f"Package {package_id} not found", code="PACKAGE_NOT_FOUND")
    body = {**rec["package"], "status": rec["status"], "booking": rec["booking"], "updatedAt": rec["updatedAt"]}
    if include_request:
        body["request"] = rec["request"]
    return body


@app.post(f"{P}/packages/{{package_id}}/prebook", tags=["Booking"],
          summary="Re-price & validate before booking (CheckRate hotels, fresh activity rateKeys, questions)")
async def prebook(package_id: str, req: PrebookRequest = Body(default_factory=PrebookRequest),
                  c: Container = Depends(container)):
    return await BookingService(c).prebook(package_id, req)


@app.post(f"{P}/packages/{{package_id}}/confirm", tags=["Booking"],
          summary="Book every component with Hotelbeds (hotels → activities → transfers)")
async def confirm(package_id: str,
                  req: ConfirmPackageRequest = Body(..., openapi_examples={
                      "family": {"summary": "Family of 3 — multi-city example", "value": CONFIRM_EXAMPLE}}),
                  c: Container = Depends(container)):
    return await BookingService(c).confirm(package_id, req)


@app.get(f"{P}/packages/{{package_id}}/bookings", tags=["Booking"],
         summary="Live booking status from Hotelbeds (+ vouchers)")
async def bookings(package_id: str, c: Container = Depends(container)):
    return await BookingService(c).live_status(package_id)


@app.post(f"{P}/packages/{{package_id}}/cancel", tags=["Booking"],
          summary="Cancel the package bookings (simulate=true returns fees only)")
async def cancel(package_id: str, req: CancelPackageRequest = Body(default_factory=CancelPackageRequest),
                 c: Container = Depends(container)):
    return await BookingService(c).cancel(package_id, req)
