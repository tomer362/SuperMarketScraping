from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _get_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_list(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def _normalize_database_url(value: str) -> str:
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+asyncpg://", 1)
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    host: str
    port: int
    secret_key: str
    scrape_interval_hours: float
    enable_scheduler: bool
    auto_refresh_on_start: bool
    session_cookie_name: str
    session_max_age_days: int
    session_cookie_secure: bool
    cors_origins: list[str]
    refresh_auth_token: str | None
    is_vercel: bool
    seed_test_data: bool
    reset_test_db_on_start: bool


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    is_vercel = _get_bool("VERCEL", False)
    return Settings(
        database_url=_normalize_database_url(
            os.environ.get(
                "DATABASE_URL",
                "postgresql+asyncpg://postgres:postgres@localhost:5432/supermarket",
            )
        ),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        secret_key=os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32),
        scrape_interval_hours=float(os.environ.get("SCRAPE_INTERVAL_HOURS", "6")),
        enable_scheduler=_get_bool("ENABLE_SCHEDULER", not is_vercel),
        auto_refresh_on_start=_get_bool("AUTO_REFRESH_ON_START", not is_vercel),
        session_cookie_name=os.environ.get(
            "SESSION_COOKIE_NAME", "supermarket_session"
        ),
        session_max_age_days=int(os.environ.get("SESSION_MAX_AGE_DAYS", "30")),
        session_cookie_secure=_get_bool("SESSION_COOKIE_SECURE", is_vercel),
        cors_origins=_get_list(
            "CORS_ORIGINS",
            [
                "http://localhost:5173",
                "http://localhost:5174",
                "http://127.0.0.1:5173",
                "http://127.0.0.1:5174",
                "http://localhost:4173",
                "http://127.0.0.1:4173",
            ],
        ),
        refresh_auth_token=os.environ.get("CATALOG_REFRESH_TOKEN")
        or os.environ.get("CRON_SECRET"),
        is_vercel=is_vercel,
        seed_test_data=_get_bool("SEED_TEST_DATA", False),
        reset_test_db_on_start=_get_bool("RESET_TEST_DB_ON_START", False),
    )


def clear_settings_cache() -> None:
    get_settings.cache_clear()
