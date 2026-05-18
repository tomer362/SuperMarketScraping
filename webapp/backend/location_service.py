from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import StoreBranch

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "SuperMarketScraping/1.0 location geocoder"
_nominatim_lock = asyncio.Lock()
_last_nominatim_call = 0.0


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    label: str
    source: str = "nominatim"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def validate_coordinates(latitude: float, longitude: float) -> tuple[float, float]:
    lat = float(latitude)
    lng = float(longitude)
    if not -90 <= lat <= 90:
        raise ValueError("Latitude must be between -90 and 90")
    if not -180 <= lng <= 180:
        raise ValueError("Longitude must be between -180 and 180")
    return lat, lng


def haversine_km(
    latitude_a: float | None,
    longitude_a: float | None,
    latitude_b: float | None,
    longitude_b: float | None,
) -> float | None:
    if None in (latitude_a, longitude_a, latitude_b, longitude_b):
        return None
    lat1, lng1 = math.radians(float(latitude_a)), math.radians(float(longitude_a))
    lat2, lng2 = math.radians(float(latitude_b)), math.radians(float(longitude_b))
    delta_lat = lat2 - lat1
    delta_lng = lng2 - lng1
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    )
    return 6371.0088 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def geocode_address(query: str) -> GeocodeResult | None:
    cleaned_query = " ".join(query.split())
    if not cleaned_query:
        return None
    search_query = cleaned_query if "ישראל" in cleaned_query else f"{cleaned_query}, ישראל"

    global _last_nominatim_call
    async with _nominatim_lock:
        elapsed = time.monotonic() - _last_nominatim_call
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                NOMINATIM_URL,
                params={
                    "q": search_query,
                    "format": "jsonv2",
                    "limit": 1,
                    "countrycodes": "il",
                    "accept-language": "he,en",
                },
                headers={"User-Agent": NOMINATIM_USER_AGENT},
            )
        _last_nominatim_call = time.monotonic()

    response.raise_for_status()
    items = response.json()
    if not items:
        return None
    first = items[0]
    lat, lng = validate_coordinates(float(first["lat"]), float(first["lon"]))
    label = first.get("display_name") or cleaned_query
    return GeocodeResult(latitude=lat, longitude=lng, label=label)


def normalize_branch_payload(chain: str, branch: dict[str, Any]) -> dict[str, Any] | None:
    store_id = branch.get("store_id", branch.get("id", branch.get("store_code")))
    if store_id is None:
        return None
    store_name = branch.get("store_name", branch.get("name", ""))
    city = branch.get("city") or None
    address = branch.get("address", branch.get("location")) or None
    lat = branch.get("lat", branch.get("latitude"))
    lng = branch.get("lng", branch.get("lon", branch.get("longitude")))
    try:
        lat_value, lng_value = validate_coordinates(float(lat), float(lng)) if lat is not None and lng is not None else (None, None)
    except (TypeError, ValueError):
        lat_value, lng_value = None, None
    return {
        "chain": chain,
        "store_id": str(store_id),
        "store_name": str(store_name or store_id),
        "city": str(city).strip() if city else None,
        "address": str(address).strip() if address else None,
        "lat": lat_value,
        "lng": lng_value,
        "geocode_source": "scraper" if lat_value is not None and lng_value is not None else None,
        "geocode_status": "resolved" if lat_value is not None and lng_value is not None else None,
        "geocoded_at": now_utc() if lat_value is not None and lng_value is not None else None,
    }


async def upsert_store_branches(session: AsyncSession, branches: list[dict[str, Any]]) -> int:
    normalized = [branch for branch in branches if branch.get("chain") and branch.get("store_id")]
    if not normalized:
        return 0

    count = 0
    for branch in normalized:
        existing = (
            await session.execute(
                select(StoreBranch).where(
                    StoreBranch.chain == branch["chain"],
                    StoreBranch.store_id == str(branch["store_id"]),
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(StoreBranch(**branch))
            count += 1
            continue
        existing.store_name = branch.get("store_name") or existing.store_name
        existing.city = branch.get("city") or existing.city
        existing.address = branch.get("address") or existing.address
        if branch.get("lat") is not None and branch.get("lng") is not None:
            existing.lat = branch["lat"]
            existing.lng = branch["lng"]
            existing.geocode_source = branch.get("geocode_source") or existing.geocode_source
            existing.geocode_status = branch.get("geocode_status") or existing.geocode_status
            existing.geocoded_at = branch.get("geocoded_at") or existing.geocoded_at
        count += 1
    return count


async def geocode_missing_store_branches(session: AsyncSession, *, limit: int = 25) -> int:
    branches = list(
        (
            await session.execute(
                select(StoreBranch)
                .where(StoreBranch.lat.is_(None), StoreBranch.lng.is_(None))
                .where(or_(StoreBranch.geocode_status.is_(None), StoreBranch.geocode_status != "not_found"))
                .limit(limit)
            )
        ).scalars()
    )
    resolved = 0
    for branch in branches:
        query_parts = [branch.address, branch.city, branch.store_name]
        query = " ".join(part for part in query_parts if part)
        result = await geocode_address(query)
        branch.geocoded_at = now_utc()
        branch.geocode_source = "nominatim"
        if result is None:
            branch.geocode_status = "not_found"
            continue
        branch.lat = result.latitude
        branch.lng = result.longitude
        branch.geocode_status = "resolved"
        resolved += 1
    return resolved
