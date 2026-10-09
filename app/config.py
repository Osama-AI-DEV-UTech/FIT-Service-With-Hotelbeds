"""
Central configuration — every tunable lives here and is read from the
environment / `.env`. Nothing else in the codebase reads os.environ.

Hotelbeds issues a SEPARATE api-key/secret pair per product line
(Hotels, Activities, Transfers) and per environment. The per-product
values fall back to the shared HOTELBEDS_API_KEY / HOTELBEDS_SECRET when
left blank, so small accounts with one shared pair work unchanged.
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Product = Literal["hotels", "activities", "transfers"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Application ──────────────────────────────────────────────────
    APP_NAME: str = "Ubidtours FIT Service (Hotelbeds)"
    APP_VERSION: str = "2.0.0"
    ENV: Literal["development", "staging", "production", "test"] = "development"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True
    # Swagger UIs (/fit/docs, /hotels/docs, ...) — keep enabled unless
    # you explicitly want them hidden in production.
    ENABLE_DOCS: bool = True
    CORS_ALLOW_ORIGINS: str = "*"  # comma-separated

    # ── Hotelbeds connection ─────────────────────────────────────────
    HOTELBEDS_ENV: Literal["test", "live"] = "test"
    HOTELBEDS_BASE_URL: str = "https://api.test.hotelbeds.com"
    # Card-carrying hotel bookings (paymentData) go to the secure host.
    HOTELBEDS_SECURE_BASE_URL: str = "https://api-secure.test.hotelbeds.com"

    HOTELBEDS_API_KEY: str = ""
    HOTELBEDS_SECRET: str = ""
    HOTELBEDS_HOTELS_API_KEY: str = ""
    HOTELBEDS_HOTELS_SECRET: str = ""
    HOTELBEDS_ACTIVITIES_API_KEY: str = ""
    HOTELBEDS_ACTIVITIES_SECRET: str = ""
    HOTELBEDS_TRANSFERS_API_KEY: str = ""
    HOTELBEDS_TRANSFERS_SECRET: str = ""

    HOTELBEDS_REQUEST_TIMEOUT_SECONDS: float = 60.0  # Hotelbeds asks for >=60s on booking calls
    HOTELBEDS_CONNECT_TIMEOUT_SECONDS: float = 10.0
    HOTELBEDS_MAX_RETRIES: int = Field(default=2, ge=0, le=6)
    HOTELBEDS_RETRY_BASE_DELAY_SECONDS: float = 0.8

    # Path variants that differ between Hotelbeds doc pages — kept
    # configurable so a sandbox call can settle them without a code change.
    HOTELBEDS_TRANSFER_BOOKING_PATH: str = "/transfer-api/1.0/bookings"
    HOTELBEDS_TRANSFER_AMEND_PATH: str = "/transfer-api/1.0/booking"

    # ── Hotelbeds request defaults ───────────────────────────────────
    HOTELBEDS_HOTEL_LANGUAGE: str = "ENG"        # Hotels API uses 3-letter codes
    HOTELBEDS_ACTIVITY_LANGUAGE: str = "en"      # Activities API uses ISO-639-1
    HOTELBEDS_TRANSFER_LANGUAGE: str = "en"      # Transfers API uses ISO-639-1
    DEFAULT_SOURCE_MARKET: str = "PK"            # used when travelerInfo.nationality is absent
    DEFAULT_CURRENCY: str = "EUR"                # Hotelbeds prices in the account currency

    # ── FIT engine tuning ────────────────────────────────────────────
    FIT_MAX_HOTELS_PER_SEARCH: int = Field(default=60, ge=1, le=2000)
    FIT_HOTEL_ALTERNATIVES: int = Field(default=3, ge=0, le=10)
    FIT_HOTEL_CONTENT_ENRICH_TOP_N: int = Field(default=10, ge=0, le=100)
    FIT_ALLOW_PACKAGING_RATES: bool = True       # FIT always sells hotel bundled → opaque rates allowed
    FIT_GEO_FALLBACK_RADIUS_KM: int = Field(default=20, ge=1, le=200)
    FIT_ACTIVITY_ALTERNATIVES: int = Field(default=2, ge=0, le=10)
    FIT_ACTIVITY_TARGETED_SEARCH: bool = True    # extra `text` search when an excursion isn't matched
    FIT_ACTIVITY_MAX_PAGES: int = Field(default=1, ge=1, le=10)  # 100 activities per page
    FIT_ACTIVITY_MIN_MATCH_SCORE: float = 20.0
    FIT_ENABLE_SUGGESTED_ACTIVITIES: bool = True # fill free days with suggestions
    FIT_MAX_SUGGESTED_ACTIVITIES_PER_DESTINATION: int = Field(default=2, ge=0, le=10)
    FIT_ACTIVITY_DEFAULT_ADULT_AGE: int = 30
    FIT_TRANSFER_ROUND_TRIP_OPTIMISATION: bool = True
    FIT_MAX_PARALLEL_DESTINATIONS: int = Field(default=4, ge=1, le=20)

    # Markups (percent) applied on top of NET prices when the supplier
    # does not impose a mandatory selling price (hotelMandatory=false).
    MARKUP_HOTEL_PCT: float = Field(default=0.0, ge=0)
    MARKUP_ACTIVITY_PCT: float = Field(default=0.0, ge=0)
    MARKUP_TRANSFER_PCT: float = Field(default=0.0, ge=0)

    # ── Booking ──────────────────────────────────────────────────────
    PACKAGE_TTL_MINUTES: int = Field(default=30, ge=1)   # rateKeys go stale; regenerate after this
    BOOKING_PRICE_TOLERANCE_PCT: float = Field(default=2.0, ge=0)
    BOOKING_CLIENT_REFERENCE_PREFIX: str = "UBT"

    # ── Content cache ────────────────────────────────────────────────
    CONTENT_CACHE_TTL_HOURS: int = Field(default=168, ge=1)  # Hotelbeds: refresh content at least weekly

    # ── Persistence ──────────────────────────────────────────────────
    DATABASE_PATH: str = "./data/fit_service.db"

    # ── Optional AI narrative (OpenAI) ───────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT_SECONDS: float = 20.0

    @field_validator("HOTELBEDS_BASE_URL", "HOTELBEDS_SECURE_BASE_URL")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    def hotelbeds_credentials(self, product: Product) -> tuple[str, str]:
        p = product.upper()
        key = getattr(self, f"HOTELBEDS_{p}_API_KEY", "") or self.HOTELBEDS_API_KEY
        secret = getattr(self, f"HOTELBEDS_{p}_SECRET", "") or self.HOTELBEDS_SECRET
        return key.strip(), secret.strip()

    def hotelbeds_configured(self, product: Product) -> bool:
        key, secret = self.hotelbeds_credentials(product)
        return bool(key and secret)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ALLOW_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
