from typing import Optional

from fastapi import FastAPI

from app.config import settings
from app.container import Container, get_container
from app.core.errors import register_error_handlers


def create_subapp(*, title: str, description: str, openapi_tags: Optional[list] = None) -> FastAPI:
    app = FastAPI(
        title=title,
        description=description,
        version=settings.APP_VERSION,
        docs_url="/docs" if settings.ENABLE_DOCS else None,
        redoc_url="/redoc" if settings.ENABLE_DOCS else None,
        openapi_url="/openapi.json" if settings.ENABLE_DOCS else None,
        openapi_tags=openapi_tags,
        swagger_ui_parameters={"docExpansion": "list", "defaultModelsExpandDepth": 0,
                               "displayRequestDuration": True, "tryItOutEnabled": True},
    )
    register_error_handlers(app)
    return app


def container() -> Container:
    """FastAPI dependency."""
    return get_container()
