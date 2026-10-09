"""
FIT orchestrator — the order is fixed by design:

    1. HOTELS      (all destinations in parallel)
    2. ACTIVITIES  (all destinations in parallel; can fall back to hotel destination / coordinates)
    3. TRANSFERS   (all destinations in parallel; uses the selected hotel code as ATLAS location)
    4. PACKAGE     (pricing, itinerary, cancellation summary, advisories, narrative) → persisted

A failing component never fails the whole package: it degrades to a
status + CRITICAL advisory, and the package status becomes PARTIAL.
"""
import asyncio
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import structlog

from app.config import settings
from app.container import Container
from app.core.context import current_ledger, get_correlation_id
from app.fit.activity_stage import ActivityStage
from app.fit.hotel_stage import HotelStage
from app.fit.narrative import build_narrative
from app.fit.package_builder import PackageBuilder
from app.fit.planner import Planner, TripPlan
from app.fit.resolvers import LocationResolver
from app.fit.transfer_stage import TransferStage
from app.schemas.fit import FITRequest

logger = structlog.get_logger("fit.orchestrator")

_SEVERITY_ORDER = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}


def new_package_id() -> str:
    return f"FIT-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(3).upper()}"


class FITOrchestrator:
    def __init__(self, c: Container):
        self.c = c
        self.resolver = LocationResolver(c)
        self.sem = asyncio.Semaphore(settings.FIT_MAX_PARALLEL_DESTINATIONS)

    async def generate(self, req: FITRequest) -> dict:
        t0 = time.perf_counter()
        plan = Planner().build(req)
        log = logger.bind(destinations=[d.name for d in plan.destinations])
        log.info("fit_generation_started")
        timings: dict[str, float] = {}

        hotel_stage = HotelStage(self.c, self.resolver, plan)
        activity_stage = ActivityStage(self.c, self.resolver, plan)
        transfer_stage = TransferStage(self.c, self.resolver, plan)

        hotels = await self._phase("hotels", timings, [lambda dp=dp: hotel_stage.run(dp) for dp in plan.destinations])
        activities = await self._phase("activities", timings, [
            lambda dp=dp, h=h: activity_stage.run(dp, h) for dp, h in zip(plan.destinations, hotels)])
        transfers = await self._phase("transfers", timings, [
            lambda dp=dp, h=h: transfer_stage.run(dp, h) for dp, h in zip(plan.destinations, hotels)])

        results = [{"hotel": h, "activities": a, "transfers": t} for h, a, t in zip(hotels, activities, transfers)]

        t_pkg = time.perf_counter()
        narrative, narrative_source = await build_narrative(plan, results)
        package_id = new_package_id()
        display, booking = PackageBuilder(plan).build(package_id, results, narrative)
        timings["package"] = round((time.perf_counter() - t_pkg) * 1000, 1)

        status = self._status(plan, results)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=settings.PACKAGE_TTL_MINUTES)
        ledger = current_ledger()
        advisories = sorted((a.dict() for a in plan.advisories),
                            key=lambda a: (_SEVERITY_ORDER[a["severity"]], a["destinationIndex"] or -1))
        response = {
            "packageId": package_id,
            "status": status,
            "provider": "HOTELBEDS",
            "createdAt": now,
            "expiresAt": expires,
            "displayPackage": display,
            "bookingPayload": booking,
            "adminComments": advisories,
            "requestFieldsAdded": plan.fields_added,
            "generationMetadata": {
                "durationMs": round((time.perf_counter() - t0) * 1000, 1),
                "stageDurationsMs": timings,
                "hotelbedsCalls": ledger.summary() if ledger else None,
                "hotelbedsEnv": settings.HOTELBEDS_ENV,
                "narrativeSource": narrative_source,
                "serviceVersion": settings.APP_VERSION,
                "correlationId": get_correlation_id(),
                "componentStatus": [{"destination": dp.name, "hotel": r["hotel"]["status"],
                                     "activities": r["activities"].get("status"),
                                     "transfers": r["transfers"].get("status")}
                                    for dp, r in zip(plan.destinations, results)],
            },
        }
        await self.c.store.save_package(
            package_id=package_id, status=status, request=req.model_dump(mode="json"),
            package=_jsonable(response), correlation_id=get_correlation_id(), expires_at=expires.isoformat())
        log.info("fit_generation_completed", package_id=package_id, status=status,
                 duration_ms=response["generationMetadata"]["durationMs"],
                 hotelbeds_calls=(ledger.summary() if ledger else {}).get("total"))
        return response

    async def _phase(self, name: str, timings: dict, jobs: list[Callable[[], Awaitable[dict]]]) -> list[dict]:
        started = time.perf_counter()

        async def guarded(job):
            async with self.sem:
                try:
                    return await job()
                except Exception as exc:  # noqa: BLE001 — a stage bug must not kill the package
                    logger.exception("fit_stage_crashed", stage=name, error=str(exc))
                    return {"status": "INTERNAL_ERROR", "error": {"message": str(exc)}, "selected": None,
                            "alternatives": [], "items": [], "suggested": [], "arrival": None, "departure": None}

        out = await asyncio.gather(*(guarded(j) for j in jobs))
        timings[name] = round((time.perf_counter() - started) * 1000, 1)
        return list(out)

    @staticmethod
    def _status(plan: TripPlan, results: list[dict]) -> str:
        has_any = any(r["hotel"].get("selected") for r in results) or any(
            i.get("status") == "MATCHED" for r in results for i in r["activities"].get("items") or []) or any(
            (r["transfers"].get(k) or {}).get("selected") for r in results for k in ("arrival", "departure"))
        if not has_any:
            return "FAILED"
        if any(a.severity == "CRITICAL" for a in plan.advisories):
            return "PARTIAL"
        for dp, r in zip(plan.destinations, results):
            if dp.accommodation and not r["hotel"].get("selected"):
                return "PARTIAL"
            if any(i.get("status") != "MATCHED" for i in r["activities"].get("items") or []):
                return "PARTIAL"
            for leg in (dp.arrival, dp.departure):
                if leg.required and not (r["transfers"].get(leg.direction.lower()) or {}).get("selected"):
                    return "PARTIAL"
        return "GENERATED"


def _jsonable(obj):
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj
