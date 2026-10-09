"""Price lines and package totals.

Hotelbeds pricing model: if a hotel rate has `hotelMandatory = true` the
supplier imposes `sellingRate` as the minimum public price — we must sell
at it. Otherwise the price is NET and the platform markup applies.
"""
from collections import defaultdict
from typing import Optional

from app.config import settings
from app.fit.scoring import money

_MARKUP = {"HOTEL": "MARKUP_HOTEL_PCT", "ACTIVITY": "MARKUP_ACTIVITY_PCT", "TRANSFER": "MARKUP_TRANSFER_PCT"}


def price_line(product: str, net: float, currency: Optional[str], *,
               mandatory_selling: Optional[float] = None) -> dict:
    markup = float(getattr(settings, _MARKUP[product]))
    net = money(net)
    if mandatory_selling:
        selling, basis = money(mandatory_selling), "SUPPLIER_MANDATORY_SELLING_RATE"
    else:
        selling, basis = money(net * (1 + markup / 100)), "NET_PLUS_MARKUP"
    return {"net": net, "selling": selling, "currency": (currency or settings.DEFAULT_CURRENCY).upper(),
            "markupPct": 0.0 if mandatory_selling else markup, "basis": basis}


def summarize(lines: list[dict], budget_total: Optional[float], budget_currency: str) -> dict:
    """lines: [{"category": HOTEL|ACTIVITY|TRANSFER, "label", "price": price_line, ...}]"""
    by_currency: dict[str, dict] = defaultdict(lambda: {"net": 0.0, "selling": 0.0,
                                                        "byCategory": defaultdict(float)})
    for line in lines:
        p = line["price"]
        bucket = by_currency[p["currency"]]
        bucket["net"] += p["net"]
        bucket["selling"] += p["selling"]
        bucket["byCategory"][line["category"]] += p["selling"]

    totals = []
    for cur, b in by_currency.items():
        totals.append({"currency": cur, "net": money(b["net"]), "selling": money(b["selling"]),
                       "margin": money(b["selling"] - b["net"]),
                       "byCategory": {k: money(v) for k, v in b["byCategory"].items()}})

    grand = totals[0] if len(totals) == 1 else None
    budget = None
    if budget_total:
        match = next((t for t in totals if t["currency"] == budget_currency), None)
        if match and len(totals) == 1:
            diff = money(budget_total - match["selling"])
            budget = {"budget": money(budget_total), "currency": budget_currency, "packageTotal": match["selling"],
                      "difference": diff, "withinBudget": diff >= 0,
                      "utilisationPct": round(match["selling"] / budget_total * 100, 1) if budget_total else None}
        else:
            budget = {"budget": money(budget_total), "currency": budget_currency, "comparable": False,
                      "note": "Package priced in a different currency than the budget — no FX conversion applied"}
    return {"lines": lines, "totalsByCurrency": totals,
            "grandTotal": {"amount": grand["selling"], "currency": grand["currency"]} if grand else None,
            "budgetCheck": budget}
