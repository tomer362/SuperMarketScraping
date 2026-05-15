from __future__ import annotations

import logging
from contextlib import asynccontextmanager

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
from db import async_session_factory, create_tables, dispose_engine, get_session
from models import CanonicalProduct, GenericProductGroup, ShoppingList, ShoppingListItem, User
from schemas import (
    AuthPayload,
    CatalogStatusOut,
    ChainOfferOut,
    ChainOut,
    LoginIn,
    MessageOut,
    ProductDetailOut,
    ProductSearchResultOut,
    RefreshTriggerOut,
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
from scraper_runner import run_full_refresh
from scheduler import RefreshScheduler
from seed import seed_demo_catalog
from settings import get_settings


settings = get_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("webapp")


def _parse_chain_filter(chains: str | None) -> list[str] | None:
    if not chains:
        return None
    requested = [value.strip() for value in chains.split(",") if value.strip()]
    if not requested:
        return None
    return requested


async def _refresh_catalog(source: str) -> dict:
    async with async_session_factory() as session:
        result = await run_full_refresh(session, source=source)
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


scheduler = RefreshScheduler(settings.scrape_interval_hours, _refresh_catalog)


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
            if not await catalog_is_fresh(session, settings.scrape_interval_hours):
                await _refresh_catalog("startup")
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
    return AuthPayload(user=UserOut(id=user.id, username=user.username, created_at=user.created_at))


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
    return AuthPayload(user=UserOut(id=user.id, username=user.username, created_at=user.created_at))


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
    return AuthPayload(user=UserOut(id=user.id, username=user.username, created_at=user.created_at))


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
    session: AsyncSession = Depends(get_session),
) -> ProductDetailOut:
    detail = await load_product_detail(session, product_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Product not found")
    return ProductDetailOut(**detail)


@app.get("/api/products/{product_id}/offers", response_model=list[ChainOfferOut], tags=["Products"])
async def product_offers(
    product_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[ChainOfferOut]:
    offers = await load_product_chain_offers(session, product_id)
    if not offers:
        raise HTTPException(status_code=404, detail="Product not found")
    return [ChainOfferOut(**offer) for offer in offers]


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
    return ShoppingListComparisonOut(**(await compare_shopping_list(session, shopping_list)))


@app.get("/api/catalog/status", response_model=CatalogStatusOut, tags=["Catalog"])
async def catalog_status(session: AsyncSession = Depends(get_session)) -> CatalogStatusOut:
    last_refresh = await latest_refresh_run(session)
    last_successful_refresh = await latest_refresh_run(session, successful_only=True)
    return CatalogStatusOut(
        scheduler_running=scheduler.is_running,
        refresh_in_progress=scheduler.refresh_in_progress,
        interval_hours=settings.scrape_interval_hours,
        catalog_fresh=await catalog_is_fresh(session, settings.scrape_interval_hours),
        last_refresh=serialize_refresh_run(last_refresh),
        last_successful_refresh=serialize_refresh_run(last_successful_refresh),
        chains=[ChainOut(**chain) for chain in await public_chain_statuses(session)],
    )


@app.post("/api/catalog/refresh", response_model=RefreshTriggerOut, tags=["Catalog"])
async def trigger_catalog_refresh() -> RefreshTriggerOut:
    result = await scheduler.trigger_now("manual")
    return RefreshTriggerOut(**result)


@app.get("/api/catalog/refresh/cron", response_model=RefreshTriggerOut, tags=["Catalog"])
async def trigger_catalog_refresh_cron(
    authorization: str | None = Header(default=None),
) -> RefreshTriggerOut:
    _require_refresh_auth(authorization)
    result = await scheduler.trigger_now("vercel-cron")
    return RefreshTriggerOut(**result)


@app.get("/api/health", response_model=MessageOut, tags=["Meta"])
async def health() -> MessageOut:
    return MessageOut(detail="ok")
