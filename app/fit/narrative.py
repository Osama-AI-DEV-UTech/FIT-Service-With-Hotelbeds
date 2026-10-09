"""Package narrative: deterministic template, optionally polished by OpenAI
(OPENAI_API_KEY). The AI only rewrites facts it is given — it never
chooses products or prices — and any failure falls back to the template."""
import json
from typing import Optional

import httpx
import structlog

from app.config import settings
from app.fit.planner import TripPlan

logger = structlog.get_logger("fit.narrative")


def template_narrative(plan: TripPlan, results: list[dict]) -> str:
    who = f"{plan.adults} adult{'s' if plan.adults > 1 else ''}"
    if plan.child_ages:
        who += f" and {len(plan.child_ages)} child{'ren' if len(plan.child_ages) > 1 else ''}"
    parts = [f"A {plan.total_nights}-night {plan.planning_style.replace('_', ' ').lower()} trip for {who}."]
    for dp, res in zip(plan.destinations, results):
        sel = (res["hotel"].get("selected") or {}).get("hotel")
        line = f"{dp.name} ({dp.nights} night{'s' if dp.nights > 1 else ''})"
        if sel:
            line += f": stay at {sel['name']}" + (f" ({sel['stars']}★)" if sel.get("stars") else "")
        acts = [i["activity"]["name"] for i in res["activities"].get("items") or [] if i.get("status") == "MATCHED"]
        if acts:
            line += f"; experiences: {', '.join(acts[:4])}"
        trf = [k for k in ("arrival", "departure") if (res["transfers"].get(k) or {}).get("selected")]
        if trf:
            line += f"; {' and '.join(trf)} transfer{'s' if len(trf) > 1 else ''} included"
        parts.append(line + ".")
    return " ".join(parts)


async def build_narrative(plan: TripPlan, results: list[dict]) -> tuple[str, str]:
    base = template_narrative(plan, results)
    if not settings.OPENAI_API_KEY:
        return base, "template"
    facts = {"summary": base, "travelerType": plan.request.travelerInfo.travelerType,
             "comments": [plan.request.addnlComment, plan.request.refTripComment, plan.request.travelerInfo.comment]}
    try:
        async with httpx.AsyncClient(timeout=settings.OPENAI_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                json={"model": settings.OPENAI_MODEL, "temperature": 0.4, "max_tokens": 350,
                      "messages": [
                          {"role": "system", "content": "You write warm, concise travel package introductions "
                           "(max 120 words). Use ONLY the facts provided. Never invent hotels, prices, dates "
                           "or activities."},
                          {"role": "user", "content": json.dumps(facts, default=str)}]},
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            return (text or base), "openai"
    except Exception as exc:  # noqa: BLE001 — narrative must never break package generation
        logger.warning("narrative_ai_failed", error=str(exc))
        return base, "template"
