"""
Root ASGI app. Mounts four independently-documented APIs:

    /fit         → FIT package engine        (docs: /fit/docs, form: /fit/form)
    /hotels      → Hotelbeds Hotels API       (docs: /hotels/docs)
    /activities  → Hotelbeds Activities API   (docs: /activities/docs)
    /transfers   → Hotelbeds Transfers API    (docs: /transfers/docs)

Run:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.apis import activities_app, fit_app, hotels_app, transfers_app
from app.config import settings
from app.container import build_container, get_container, set_container
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import CorrelationAndLoggingMiddleware
from app.utils.countries import pycountry_available

logger = structlog.get_logger("main")


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging(settings.LOG_LEVEL, settings.LOG_JSON)
    if not pycountry_available():
        logger.error("PYCOUNTRY_NOT_INSTALLED — country names will not resolve; pip install -r requirements.txt")
    container = await build_container(settings)
    logger.info("fit_service_started", env=settings.ENV, hotelbeds_env=settings.HOTELBEDS_ENV,
                hotelbeds_base_url=settings.HOTELBEDS_BASE_URL, products=container.configured(),
                database=settings.DATABASE_PATH)
    try:
        yield
    finally:
        await container.close()
        set_container(None)
        logger.info("fit_service_stopped")


def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Correlation-Id"])
    app.add_middleware(CorrelationAndLoggingMiddleware)
    register_error_handlers(app)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def landing():
        return _LANDING

    @app.get("/health", tags=["Health"])
    async def health():
        return {"status": "ok", "service": settings.APP_NAME, "version": settings.APP_VERSION}

    @app.get("/health/ready", tags=["Health"])
    async def ready():
        c = get_container()
        products = c.configured()
        return {"status": "ready" if all(products.values()) else "degraded", "hotelbedsEnv": settings.HOTELBEDS_ENV,
                "hotelbedsBaseUrl": settings.HOTELBEDS_BASE_URL, "productsConfigured": products}

    app.mount("/fit", fit_app.app)
    app.mount("/hotels", hotels_app.app)
    app.mount("/activities", activities_app.app)
    app.mount("/transfers", transfers_app.app)
    return app


_LANDING = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>FIT Service</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:760px;margin:48px auto;
padding:0 16px;color:#1d2433}}h1{{font-size:24px}}a.card{{display:block;border:1px solid #e3e7ef;border-radius:10px;
padding:16px 18px;margin:12px 0;text-decoration:none;color:inherit}}a.card:hover{{border-color:#2f6fed}}
b{{color:#2f6fed}}small{{color:#5b6475}}</style></head><body>
<h1>{settings.APP_NAME} <small>v{settings.APP_VERSION}</small></h1>
<a class="card" href="/fit/form"><b>FIT form</b><br><small>Fill the trip request and generate a package</small></a>
<a class="card" href="/fit/docs"><b>/fit/docs</b><br><small>FIT package API — generate, prebook, confirm, cancel</small></a>
<a class="card" href="/hotels/docs"><b>/hotels/docs</b><br><small>Hotelbeds Hotels — availability, checkrates, bookings, content</small></a>
<a class="card" href="/activities/docs"><b>/activities/docs</b><br><small>Hotelbeds Activities — search, details, bookings, content, cache</small></a>
<a class="card" href="/transfers/docs"><b>/transfers/docs</b><br><small>Hotelbeds Transfers — availability, bookings, amendments, cache</small></a>
<a class="card" href="/health/ready"><b>/health/ready</b><br><small>Which Hotelbeds products are configured</small></a>
</body></html>"""

app = create_app()
