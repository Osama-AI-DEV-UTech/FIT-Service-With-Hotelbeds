"""
Process-wide service container. Built once in the root app's lifespan
and shared by every mounted sub-app (/fit, /hotels, /activities,
/transfers) — Starlette does not run lifespans of mounted apps, so the
sub-apps read their dependencies from here.
"""
from dataclasses import dataclass, field
from typing import Optional

import httpx
import structlog

from app.config import Settings, settings as default_settings
from app.core.exceptions import ProviderNotConfigured
from app.hotelbeds.activities import ActivitiesAPI
from app.hotelbeds.client import HotelbedsClient
from app.hotelbeds.content_cache import ContentCache
from app.hotelbeds.hotels import HotelsAPI
from app.hotelbeds.transfers import TransfersAPI
from app.store.sqlite_store import SQLiteStore

logger = structlog.get_logger("container")


@dataclass
class Container:
    settings: Settings
    store: SQLiteStore
    cache: ContentCache
    _hotels: Optional[HotelsAPI] = None
    _activities: Optional[ActivitiesAPI] = None
    _transfers: Optional[TransfersAPI] = None
    _clients: list = field(default_factory=list)

    @property
    def hotels(self) -> HotelsAPI:
        if self._hotels is None:
            raise ProviderNotConfigured("Hotelbeds Hotels credentials are not configured "
                                        "(HOTELBEDS_HOTELS_API_KEY/SECRET or HOTELBEDS_API_KEY/SECRET)")
        return self._hotels

    @property
    def activities(self) -> ActivitiesAPI:
        if self._activities is None:
            raise ProviderNotConfigured("Hotelbeds Activities credentials are not configured "
                                        "(HOTELBEDS_ACTIVITIES_API_KEY/SECRET or HOTELBEDS_API_KEY/SECRET)")
        return self._activities

    @property
    def transfers(self) -> TransfersAPI:
        if self._transfers is None:
            raise ProviderNotConfigured("Hotelbeds Transfers credentials are not configured "
                                        "(HOTELBEDS_TRANSFERS_API_KEY/SECRET or HOTELBEDS_API_KEY/SECRET)")
        return self._transfers

    def configured(self) -> dict[str, bool]:
        return {"hotels": self._hotels is not None, "activities": self._activities is not None,
                "transfers": self._transfers is not None}

    async def close(self) -> None:
        for client in self._clients:
            await client.aclose()
        await self.store.close()


_container: Optional[Container] = None


async def build_container(settings: Settings = default_settings,
                          transport: Optional[httpx.AsyncBaseTransport] = None) -> Container:
    store = SQLiteStore(settings.DATABASE_PATH)
    await store.connect()
    container = Container(settings=settings, store=store,
                          cache=ContentCache(store, settings.CONTENT_CACHE_TTL_HOURS * 3600))

    def make(product: str) -> Optional[HotelbedsClient]:
        if not settings.hotelbeds_configured(product):  # type: ignore[arg-type]
            logger.warning("hotelbeds_product_not_configured", product=product)
            return None
        key, secret = settings.hotelbeds_credentials(product)  # type: ignore[arg-type]
        client = HotelbedsClient(
            product=product, base_url=settings.HOTELBEDS_BASE_URL, secure_base_url=settings.HOTELBEDS_SECURE_BASE_URL,
            api_key=key, secret=secret, timeout=settings.HOTELBEDS_REQUEST_TIMEOUT_SECONDS,
            connect_timeout=settings.HOTELBEDS_CONNECT_TIMEOUT_SECONDS, max_retries=settings.HOTELBEDS_MAX_RETRIES,
            retry_base_delay=settings.HOTELBEDS_RETRY_BASE_DELAY_SECONDS, transport=transport,
        )
        container._clients.append(client)
        return client

    if (c := make("hotels")) is not None:
        container._hotels = HotelsAPI(c)
    if (c := make("activities")) is not None:
        container._activities = ActivitiesAPI(c)
    if (c := make("transfers")) is not None:
        container._transfers = TransfersAPI(c)
    set_container(container)
    return container


def set_container(container: Optional[Container]) -> None:
    global _container
    _container = container


def get_container() -> Container:
    if _container is None:
        raise RuntimeError("Service container not initialised (app lifespan not started)")
    return _container
