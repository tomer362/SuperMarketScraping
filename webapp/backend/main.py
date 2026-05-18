from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import (
    create_default_list_for_user,
    create_session_cookie,
    destroy_session_by_request,
    get_current_user,
    hash_password,
    normalize_username,
    validate_password,
    validate_username,
    verify_password,
)
from catalog_service import (
    catalog_is_fresh,
    compare_shopping_list,
    get_user_list,
    get_user_list_with_items,
    get_user_lists,
    load_generic_group_detail,
    latest_refresh_run,
    load_product_chain_offers,
    load_product_detail,
    public_chain_statuses,
    search_products,
    serialize_refresh_run,
    serialize_shopping_list_detail,
    serialize_shopping_list_summary,
    suggest_products,
)
from chains import iter_active_chains
from db import async_session_factory, create_tables, dispose_engine, get_session
from models import CatalogRefreshRun, CanonicalProduct, GenericProductGroup, ShoppingList, ShoppingListItem, User
from schemas import (
    AuthPayload,
    CatalogStatusOut,
    ChainOfferOut,
    ChainOut,
    GenericProductGroupDetailOut,
    LocationPromptUpdateIn,
    LocationUpdateIn,
    LoginIn,
    MessageOut,
    ProductDetailOut,
    ProductSearchResultOut,
    RefreshTriggerOut,
    RefreshProgressOut,
    RegisterIn,
    ShoppingListComparisonOut,
    ShoppingListCreateIn,
    ShoppingListDetailOut,
    ShoppingListItemCreateIn,
    ShoppingListItemUpdateIn,
    ShoppingListSummaryOut,
    ShoppingListUpdateIn,
    SuggestResultOut,
    UserOut,
)
from location_service import geocode_address, now_utc, validate_coordinates
from scraper_runner import get_active_refresh_progress, run_deals_refresh, run_full_refresh
from scheduler import RefreshScheduler
from seed import seed_demo_catalog
from settings import get_settings


settings = get_settings()
PROCESS_STARTED_AT = now_utc()

logging.basicConfig(
    level=logging.DEBUG if settings.catalog_debug else getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("webapp")
if settings.catalog_debug:
    logging.getLogger("webapp").setLevel(logging.DEBUG)
    logging.getLogger("webapp.catalog").setLevel(logging.DEBUG)


def _parse_chain_filter(chains: str | None) -> list[str] | None:
    if not chains:
        return None
    requested = [value.strip() for value in chains.split(",") if value.strip()]
    if not requested:
        return None
    return requested


async def _refresh_catalog(source: str, refresh_kind: str = "prices") -> dict:
    async with async_session_factory() as session:
        if refresh_kind == "prices":
            result = await run_full_refresh(session, source=source)
        elif refresh_kind == "deals":
            result = await run_deals_refresh(session, source=source)
        else:
            raise ValueError(f"Unsupported refresh kind: {refresh_kind}")
        await session.commit()
        return result


async def _load_list_detail_for_user(
    session: AsyncSession,
    user: User,
    shopping_list_id: int,
) -> ShoppingListDetailOut:
    shopping_list = await get_user_list(session, user.id, shopping_list_id)
    if not shopping_list:
        raise HTTPException(status_code=404, detail="Shopping list not found")
    return ShoppingListDetailOut(
        **(await serialize_shopping_list_detail(session, shopping_list))
    )


def _require_refresh_auth(authorization: str | None) -> None:
    if not settings.refresh_auth_token:
        return
    expected = f"Bearer {settings.refresh_auth_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


def _build_refresh_progress(run: dict | None, *, refresh_in_progress: bool) -> RefreshProgressOut | None:
    if not refresh_in_progress or not run:
        return None
    total_chains = len(iter_active_chains())
    chains_scraped = list(run.get("chains_scraped") or [])
    chains_failed = list(run.get("chains_failed") or [])
    live_progress = get_active_refresh_progress(int(run.get("run_id") or 0))
    chains_started = list((live_progress or {}).get("chains_started") or [])
    chains_running = list((live_progress or {}).get("chains_running") or [])
    chains_fetched = list((live_progress or {}).get("chains_fetched") or [])
    current_chain = (live_progress or {}).get("current_chain")
    status_hint = (live_progress or {}).get("current_status_hint")
    products_fetched = int((live_progress or {}).get("products_fetched") or 0)
    products_reported = int((live_progress or {}).get("products_reported") or 0)
    products_fetched = max(products_fetched, products_reported)
    products_upserted = max(
        int(run.get("products_upserted") or 0),
        int((live_progress or {}).get("products_upserted") or 0),
    )
    chain_labels = {chain.key: chain.label for chain in iter_active_chains()}
    completed_chains = min(total_chains, len(set(chains_scraped + chains_failed)))
    started_chains = min(total_chains, len(set(chains_started)))
    fetched_chains = min(total_chains, len(set(chains_fetched)))
    progress_percent = 0
    if total_chains > 0:
        weighted_progress = max(completed_chains, fetched_chains * 0.7, started_chains * 0.25)
        progress_percent = round((weighted_progress / total_chains) * 100)
    if run.get("status") in {"done", "failed"}:
        progress_percent = 100
    progress_percent = max(0, min(100, progress_percent))
    started_at_raw = run.get("started_at")
    elapsed_seconds = 0
    if started_at_raw:
        try:
            started_at = started_at_raw if hasattr(started_at_raw, "tzinfo") else None
            if started_at and started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=now_utc().tzinfo)
            if started_at:
                elapsed_seconds = int((now_utc() - started_at).total_seconds())
        except Exception:
            elapsed_seconds = 0

    if isinstance(status_hint, str) and status_hint.startswith("persisting:"):
        _, chain_key, product_count = (status_hint.split(":", 2) + ["", ""])[:3]
        chain_label = chain_labels.get(chain_key, chain_key)
        status_label = f"שומר {product_count} מוצרים מ-{chain_label}"
    elif len(chains_running) > 1:
        status_label = f"סורק {len(chains_running)} רשתות במקביל"
    elif current_chain:
        status_label = f"סורק עכשיו: {chain_labels.get(str(current_chain), str(current_chain))}"
    elif started_chains > completed_chains:
        status_label = f"ממתין לתוצאות מ-{started_chains} רשתות"
    elif elapsed_seconds >= 60 and products_fetched <= 0 and len(chains_running) > 0:
        status_label = f"סריקה כבדה ({elapsed_seconds} שניות) - עדיין אוספים נתונים"
    elif completed_chains == 0:
        status_label = "מתחיל רענון קטלוג..."
    elif progress_percent >= 100:
        status_label = "מסיים רענון קטלוג..."
    else:
        status_label = f"נסרקו {completed_chains} מתוך {total_chains} רשתות"

    return RefreshProgressOut(
        run_id=int(run.get("run_id") or 0),
        source=str(run.get("source") or "manual"),
        refresh_kind=str(run.get("refresh_kind") or "prices"),
        status=str(run.get("status") or "running"),
        started_at=run.get("started_at"),
        completed_chains=completed_chains,
        total_chains=total_chains,
        progress_percent=progress_percent,
        current_status_label=status_label,
        current_chain=str(current_chain) if current_chain else None,
        chains_started=chains_started,
        chains_running=chains_running,
        chains_fetched=chains_fetched,
        chains_scraped=chains_scraped,
        chains_failed=chains_failed,
        products_fetched=products_fetched,
        products_upserted=products_upserted,
        errors=list(run.get("errors") or []),
    )


def _is_stale_running_refresh(run: CatalogRefreshRun) -> bool:
    started_at = run.started_at
    if started_at is None:
        return False
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=now_utc().tzinfo)
    stale_after = timedelta(minutes=settings.catalog_refresh_stale_after_minutes)
    return started_at < PROCESS_STARTED_AT or now_utc() - started_at > stale_after


async def _fail_stale_running_refresh(session: AsyncSession, run: CatalogRefreshRun | None) -> CatalogRefreshRun | None:
    if not run or scheduler.refresh_in_progress or not _is_stale_running_refresh(run):
        return run
    errors = list(run.errors or [])
    errors.append(
        "stale_refresh: marked failed because no active refresh task owns this run "
        "in the current backend process or the run exceeded "
        f"{settings.catalog_refresh_stale_after_minutes} minutes"
    )
    run.status = "failed"
    run.finished_at = now_utc()
    run.errors = errors
    await session.commit()
    logger.warning("Marked stale catalog refresh run %s as failed", run.id)
    return None


scheduler = RefreshScheduler(
    settings.price_refresh_interval_hours,
    settings.deals_refresh_interval_hours,
    _refresh_catalog,
)


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        created_at=user.created_at,
        location_lat=user.location_lat,
        location_lng=user.location_lng,
        location_label=user.location_label,
        location_source=user.location_source,
        location_updated_at=user.location_updated_at,
        location_prompt_dismissed=bool(user.location_prompt_dismissed),
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    await create_tables(drop_existing=settings.reset_test_db_on_start)
    if settings.seed_test_data:
        async with async_session_factory() as session:
            await seed_demo_catalog(session)
            await session.commit()
    if settings.enable_scheduler:
        await scheduler.start()
    if settings.auto_refresh_on_start and not settings.seed_test_data:
        async with async_session_factory() as session:
            if not await catalog_is_fresh(
                session,
                settings.price_refresh_interval_hours,
                refresh_kind="prices",
            ):
                await _refresh_catalog("startup", "prices")
    try:
        yield
    finally:
        if settings.enable_scheduler:
            await scheduler.stop()
        await dispose_engine()


app = FastAPI(
    title="SuperMarket Price Searcher",
    description="Mobile-first grocery comparison web app backend.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    if not settings.catalog_debug:
        return await call_next(request)

    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.debug(
        "request method=%s path=%s query=%s status=%s duration_ms=%.1f",
        request.method,
        request.url.path,
        request.url.query,
        response.status_code,
        elapsed_ms,
    )
    return response


@app.post("/api/auth/register", response_model=AuthPayload, tags=["Auth"])
async def register(
    payload: RegisterIn,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> AuthPayload:
    username = validate_username(payload.username)
    validate_password(payload.password)
    normalized_username = normalize_username(username)
    existing = (
        await session.execute(
            select(User).where(User.username_normalized == normalized_username)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")

    user = User(
        username=username,
        username_normalized=normalized_username,
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    await session.flush()
    await create_default_list_for_user(session, user)
    await create_session_cookie(response, session, user)
    await session.commit()
    return AuthPayload(user=_user_out(user))


@app.post("/api/auth/login", response_model=AuthPayload, tags=["Auth"])
async def login(
    payload: LoginIn,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> AuthPayload:
    normalized_username = normalize_username(payload.username)
    user = (
        await session.execute(
            select(User).where(User.username_normalized == normalized_username)
        )
    ).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    await create_session_cookie(response, session, user)
    await session.commit()
    return AuthPayload(user=_user_out(user))


@app.post("/api/auth/logout", response_model=MessageOut, tags=["Auth"])
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> MessageOut:
    await destroy_session_by_request(request, session, response)
    await session.commit()
    return MessageOut(detail="Logged out")


@app.get("/api/auth/me", response_model=AuthPayload, tags=["Auth"])
async def me(user: User = Depends(get_current_user)) -> AuthPayload:
    return AuthPayload(user=_user_out(user))


@app.patch("/api/account/location", response_model=AuthPayload, tags=["Account"])
async def update_account_location(
    payload: LocationUpdateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AuthPayload:
    mode = payload.mode.strip().lower()
    if mode == "coordinates":
        if payload.latitude is None or payload.longitude is None:
            raise HTTPException(status_code=400, detail="Latitude and longitude are required")
        try:
            latitude, longitude = validate_coordinates(payload.latitude, payload.longitude)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        user.location_lat = latitude
        user.location_lng = longitude
        user.location_label = (payload.label or "המיקום הנוכחי").strip()
        user.location_source = "gps"
    elif mode == "address":
        query = (payload.query or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="Address query is required")
        result = await geocode_address(query)
        if result is None:
            raise HTTPException(status_code=404, detail="Location not found")
        user.location_lat = result.latitude
        user.location_lng = result.longitude
        user.location_label = result.label
        user.location_source = result.source
    else:
        raise HTTPException(status_code=400, detail="Unsupported location mode")
    user.location_updated_at = now_utc()
    user.location_prompt_dismissed = True
    await session.commit()
    return AuthPayload(user=_user_out(user))


@app.delete("/api/account/location", response_model=AuthPayload, tags=["Account"])
async def clear_account_location(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AuthPayload:
    user.location_lat = None
    user.location_lng = None
    user.location_label = None
    user.location_source = None
    user.location_updated_at = None
    await session.commit()
    return AuthPayload(user=_user_out(user))


@app.patch("/api/account/location-prompt", response_model=AuthPayload, tags=["Account"])
async def update_location_prompt(
    payload: LocationPromptUpdateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AuthPayload:
    user.location_prompt_dismissed = payload.dismissed
    await session.commit()
    return AuthPayload(user=_user_out(user))


@app.get("/api/chains", response_model=list[ChainOut], tags=["Meta"])
async def chains(session: AsyncSession = Depends(get_session)) -> list[ChainOut]:
    return [ChainOut(**chain) for chain in await public_chain_statuses(session)]


@app.get("/api/search/suggest", response_model=SuggestResultOut, tags=["Search"])
async def search_suggest(
    q: str = Query("", min_length=0),
    limit: int = Query(8, ge=1, le=20),
    chains: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> SuggestResultOut:
    return SuggestResultOut(
        **(
            await suggest_products(
                session,
                q,
                limit=limit,
                chain_filter=_parse_chain_filter(chains),
            )
        )
    )


@app.get("/api/products/search", response_model=ProductSearchResultOut, tags=["Search"])
async def product_search(
    q: str = Query("", min_length=0),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    chains: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> ProductSearchResultOut:
    return ProductSearchResultOut(
        **(
            await search_products(
                session,
                q,
                limit=limit,
                offset=offset,
                chain_filter=_parse_chain_filter(chains),
            )
        )
    )


@app.get("/api/products/{product_id}", response_model=ProductDetailOut, tags=["Products"])
async def product_detail(
    product_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProductDetailOut:
    detail = await load_product_detail(session, product_id, user)
    if not detail:
        raise HTTPException(status_code=404, detail="Product not found")
    return ProductDetailOut(**detail)


@app.get("/api/products/{product_id}/offers", response_model=list[ChainOfferOut], tags=["Products"])
async def product_offers(
    product_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ChainOfferOut]:
    offers = await load_product_chain_offers(session, product_id, user)
    if not offers:
        raise HTTPException(status_code=404, detail="Product not found")
    return [ChainOfferOut(**offer) for offer in offers]


@app.get("/api/generic-groups/{group_key}", response_model=GenericProductGroupDetailOut, tags=["Products"])
async def generic_group_detail(
    group_key: str,
    chains: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GenericProductGroupDetailOut:
    detail = await load_generic_group_detail(
        session,
        group_key,
        chain_filter=_parse_chain_filter(chains),
        user=user,
    )
    if not detail:
        raise HTTPException(status_code=404, detail="Generic group not found")
    return GenericProductGroupDetailOut(**detail)


@app.get("/api/lists", response_model=list[ShoppingListSummaryOut], tags=["Lists"])
async def list_lists(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ShoppingListSummaryOut]:
    lists = await get_user_lists(session, user.id)
    return [
        ShoppingListSummaryOut(**(await serialize_shopping_list_summary(session, shopping_list)))
        for shopping_list in lists
    ]


@app.post("/api/lists", response_model=ShoppingListDetailOut, tags=["Lists"])
async def create_list(
    payload: ShoppingListCreateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ShoppingListDetailOut:
    shopping_list = ShoppingList(user_id=user.id, name=payload.name.strip())
    session.add(shopping_list)
    await session.flush()
    shopping_list_id = shopping_list.id
    await session.commit()
    return await _load_list_detail_for_user(session, user, shopping_list_id)


@app.get("/api/lists/{shopping_list_id}", response_model=ShoppingListDetailOut, tags=["Lists"])
async def get_list(
    shopping_list_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ShoppingListDetailOut:
    return await _load_list_detail_for_user(session, user, shopping_list_id)


@app.patch("/api/lists/{shopping_list_id}", response_model=ShoppingListDetailOut, tags=["Lists"])
async def update_list(
    shopping_list_id: int,
    payload: ShoppingListUpdateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ShoppingListDetailOut:
    shopping_list = await get_user_list(session, user.id, shopping_list_id)
    if not shopping_list:
        raise HTTPException(status_code=404, detail="Shopping list not found")
    shopping_list.name = payload.name.strip()
    await session.commit()
    return await _load_list_detail_for_user(session, user, shopping_list_id)


@app.delete("/api/lists/{shopping_list_id}", response_model=MessageOut, tags=["Lists"])
async def delete_list(
    shopping_list_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MessageOut:
    shopping_list = await get_user_list(session, user.id, shopping_list_id)
    if not shopping_list:
        raise HTTPException(status_code=404, detail="Shopping list not found")
    await session.delete(shopping_list)
    await session.commit()
    return MessageOut(detail="Shopping list deleted")


@app.post("/api/lists/{shopping_list_id}/items", response_model=ShoppingListDetailOut, tags=["Lists"])
async def add_list_item(
    shopping_list_id: int,
    payload: ShoppingListItemCreateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ShoppingListDetailOut:
    shopping_list = await get_user_list(session, user.id, shopping_list_id)
    if not shopping_list:
        raise HTTPException(status_code=404, detail="Shopping list not found")
    if bool(payload.canonical_product_id) == bool(payload.generic_group_key):
        raise HTTPException(status_code=400, detail="Provide exactly one product or generic group")
    if payload.canonical_product_id:
        product = await session.get(CanonicalProduct, payload.canonical_product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
    else:
        group = await session.get(GenericProductGroup, payload.generic_group_key)
        if not group:
            raise HTTPException(status_code=404, detail="Generic group not found")

    existing = (
        await session.execute(
            select(ShoppingListItem).where(
                ShoppingListItem.shopping_list_id == shopping_list_id,
                ShoppingListItem.canonical_product_id == payload.canonical_product_id,
                ShoppingListItem.generic_group_key == payload.generic_group_key,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.quantity = payload.quantity
    else:
        item = ShoppingListItem(
            shopping_list_id=shopping_list_id,
            canonical_product_id=payload.canonical_product_id,
            generic_group_key=payload.generic_group_key,
            quantity=payload.quantity,
        )
        session.add(item)
    await session.commit()
    return await _load_list_detail_for_user(session, user, shopping_list_id)


@app.patch(
    "/api/lists/{shopping_list_id}/items/{item_id}",
    response_model=ShoppingListDetailOut,
    tags=["Lists"],
)
async def update_list_item(
    shopping_list_id: int,
    item_id: int,
    payload: ShoppingListItemUpdateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ShoppingListDetailOut:
    shopping_list = await get_user_list(session, user.id, shopping_list_id)
    if not shopping_list:
        raise HTTPException(status_code=404, detail="Shopping list not found")
    item = (
        await session.execute(
            select(ShoppingListItem).where(
                ShoppingListItem.id == item_id,
                ShoppingListItem.shopping_list_id == shopping_list_id,
            )
        )
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="List item not found")
    item.quantity = payload.quantity
    await session.commit()
    return await _load_list_detail_for_user(session, user, shopping_list_id)


@app.delete(
    "/api/lists/{shopping_list_id}/items/{item_id}",
    response_model=ShoppingListDetailOut,
    tags=["Lists"],
)
async def delete_list_item(
    shopping_list_id: int,
    item_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ShoppingListDetailOut:
    shopping_list = await get_user_list(session, user.id, shopping_list_id)
    if not shopping_list:
        raise HTTPException(status_code=404, detail="Shopping list not found")
    item = (
        await session.execute(
            select(ShoppingListItem).where(
                ShoppingListItem.id == item_id,
                ShoppingListItem.shopping_list_id == shopping_list_id,
            )
        )
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="List item not found")
    await session.delete(item)
    await session.commit()
    return await _load_list_detail_for_user(session, user, shopping_list_id)


@app.get(
    "/api/lists/{shopping_list_id}/comparison",
    response_model=ShoppingListComparisonOut,
    tags=["Lists"],
)
async def compare_list(
    shopping_list_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ShoppingListComparisonOut:
    shopping_list = await get_user_list_with_items(session, user.id, shopping_list_id)
    if not shopping_list:
        raise HTTPException(status_code=404, detail="Shopping list not found")
    return ShoppingListComparisonOut(**(await compare_shopping_list(session, shopping_list, user)))


@app.get("/api/catalog/status", response_model=CatalogStatusOut, tags=["Catalog"])
async def catalog_status(session: AsyncSession = Depends(get_session)) -> CatalogStatusOut:
    running_refresh = (
        await session.execute(
            select(CatalogRefreshRun)
            .where(CatalogRefreshRun.status == "running")
            .order_by(CatalogRefreshRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    running_refresh = await _fail_stale_running_refresh(session, running_refresh)
    last_refresh = await latest_refresh_run(session)
    last_successful_refresh = await latest_refresh_run(session, successful_only=True)
    last_price_refresh = await latest_refresh_run(session, refresh_kind="prices")
    last_successful_price_refresh = await latest_refresh_run(
        session,
        successful_only=True,
        refresh_kind="prices",
    )
    last_deals_refresh = await latest_refresh_run(session, refresh_kind="deals")
    last_successful_deals_refresh = await latest_refresh_run(
        session,
        successful_only=True,
        refresh_kind="deals",
    )
    prices_fresh = await catalog_is_fresh(
        session,
        settings.price_refresh_interval_hours,
        refresh_kind="prices",
    )
    deals_fresh = await catalog_is_fresh(
        session,
        settings.deals_refresh_interval_hours,
        refresh_kind="deals",
    )
    refresh_in_progress = scheduler.refresh_in_progress or running_refresh is not None
    active_refresh = serialize_refresh_run(running_refresh) if running_refresh else scheduler.active_run
    return CatalogStatusOut(
        scheduler_running=scheduler.is_running,
        refresh_in_progress=refresh_in_progress,
        active_refresh=_build_refresh_progress(
            active_refresh,
            refresh_in_progress=refresh_in_progress,
        ),
        interval_hours=settings.price_refresh_interval_hours,
        price_interval_hours=settings.price_refresh_interval_hours,
        deals_interval_hours=settings.deals_refresh_interval_hours,
        catalog_fresh=prices_fresh,
        prices_fresh=prices_fresh,
        deals_fresh=deals_fresh,
        last_refresh=serialize_refresh_run(last_refresh),
        last_successful_refresh=serialize_refresh_run(last_successful_refresh),
        last_price_refresh=serialize_refresh_run(last_price_refresh),
        last_successful_price_refresh=serialize_refresh_run(last_successful_price_refresh),
        last_deals_refresh=serialize_refresh_run(last_deals_refresh),
        last_successful_deals_refresh=serialize_refresh_run(last_successful_deals_refresh),
        chains=[ChainOut(**chain) for chain in await public_chain_statuses(session)],
    )


@app.post("/api/catalog/refresh", response_model=RefreshTriggerOut, tags=["Catalog"])
async def trigger_catalog_refresh() -> RefreshTriggerOut:
    result = await scheduler.trigger_now("prices", "manual")
    return RefreshTriggerOut(**result)


@app.post("/api/catalog/refresh/prices", response_model=RefreshTriggerOut, tags=["Catalog"])
async def trigger_catalog_prices_refresh() -> RefreshTriggerOut:
    result = await scheduler.trigger_now("prices", "manual")
    return RefreshTriggerOut(**result)


@app.post("/api/catalog/refresh/deals", response_model=RefreshTriggerOut, tags=["Catalog"])
async def trigger_catalog_deals_refresh() -> RefreshTriggerOut:
    result = await scheduler.trigger_now("deals", "manual")
    return RefreshTriggerOut(**result)


@app.post("/api/catalog/refresh/cancel", response_model=RefreshTriggerOut, tags=["Catalog"])
async def cancel_catalog_refresh() -> RefreshTriggerOut:
    result = await scheduler.cancel_current()
    return RefreshTriggerOut(**result)


@app.get("/api/catalog/refresh/cron", response_model=RefreshTriggerOut, tags=["Catalog"])
async def trigger_catalog_refresh_cron(
    authorization: str | None = Header(default=None),
) -> RefreshTriggerOut:
    _require_refresh_auth(authorization)
    result = await scheduler.trigger_now("prices", "vercel-cron")
    return RefreshTriggerOut(**result)


@app.get("/api/catalog/refresh/prices/cron", response_model=RefreshTriggerOut, tags=["Catalog"])
async def trigger_catalog_prices_refresh_cron(
    authorization: str | None = Header(default=None),
) -> RefreshTriggerOut:
    _require_refresh_auth(authorization)
    result = await scheduler.trigger_now("prices", "vercel-cron")
    return RefreshTriggerOut(**result)


@app.get("/api/catalog/refresh/deals/cron", response_model=RefreshTriggerOut, tags=["Catalog"])
async def trigger_catalog_deals_refresh_cron(
    authorization: str | None = Header(default=None),
) -> RefreshTriggerOut:
    _require_refresh_auth(authorization)
    result = await scheduler.trigger_now("deals", "vercel-cron")
    return RefreshTriggerOut(**result)


@app.get("/api/health", response_model=MessageOut, tags=["Meta"])
async def health() -> MessageOut:
    return MessageOut(detail="ok")
