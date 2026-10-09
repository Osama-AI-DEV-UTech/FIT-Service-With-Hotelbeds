"""
Request-scoped context: correlation id + a per-request Hotelbeds call
ledger. The ledger matters because evaluation keys are capped at 50
requests/day per product — every FIT response reports exactly how many
upstream calls it cost.
"""
import contextvars
import uuid
from dataclasses import dataclass, field
from typing import Optional

_correlation_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("correlation_id", default=None)
_call_ledger: contextvars.ContextVar[Optional["CallLedger"]] = contextvars.ContextVar("call_ledger", default=None)


@dataclass
class CallLedger:
    calls: list[dict] = field(default_factory=list)

    def record(self, product: str, method: str, path: str, status: int, ms: float, cached: bool = False) -> None:
        self.calls.append({"product": product, "method": method, "path": path, "status": status,
                           "ms": round(ms, 1), "cached": cached})

    def summary(self) -> dict:
        live = [c for c in self.calls if not c["cached"]]
        by_product: dict[str, int] = {}
        for c in live:
            by_product[c["product"]] = by_product.get(c["product"], 0) + 1
        return {"total": len(live), "byProduct": by_product, "cacheHits": len(self.calls) - len(live)}


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def set_correlation_id(value: str) -> contextvars.Token:
    return _correlation_id.set(value)


def get_correlation_id() -> Optional[str]:
    return _correlation_id.get()


def start_ledger() -> CallLedger:
    ledger = CallLedger()
    _call_ledger.set(ledger)
    return ledger


def current_ledger() -> Optional[CallLedger]:
    return _call_ledger.get()
