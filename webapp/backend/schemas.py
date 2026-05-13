from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DealOut(BaseModel):
    has_deal: bool
    deal_type: str | None = None
    deal_description: str | None = None
    deal_price: float | None = None
    deal_min_qty: int | None = None
    deal_price_per_unit: float | None = None
    price_per_base_unit: float | None = None
    price_per_base_unit_deal: float | None = None


class UserOut(BaseModel):
    id: int
    username: str
    created_at: datetime


class AuthPayload(BaseModel):
    user: UserOut


class RegisterIn(BaseModel):
    username: str
    password: str


class LoginIn(BaseModel):
    username: str
    password: str


class ChainOut(BaseModel):
    chain: str
    label: str
    enabled: bool
    status: str
    unavailable_reason: str | None = None
    accent: str
    product_count: int = 0


class SuggestItemOut(BaseModel):
    id: int
    name: str
    brand: str | None = None
    unit_description: str | None = None
    image_url: str | None = None
    cheapest_price: float
    cheapest_chain: str
    cheapest_chain_label: str


class SuggestResultOut(BaseModel):
    query: str
    total: int
    items: list[SuggestItemOut]


class ProductPreviewOut(BaseModel):
    id: int
    name: str
    brand: str | None = None
    manufacturer: str | None = None
    barcode: str | None = None
    image_url: str | None = None
    unit_description: str | None = None
    unit_of_measure: str | None = None
    unit_qty: float | None = None
    unit_qty_si: float | None = None
    unit_dimension: str | None = None
    cheapest_price: float
    cheapest_chain: str
    cheapest_chain_label: str
    cheapest_store_name: str
    chain_count: int
    has_deal: bool


class ProductSearchResultOut(BaseModel):
    query: str
    total: int
    products: list[ProductPreviewOut]


class ChainOfferOut(BaseModel):
    id: int
    chain: str
    chain_label: str
    store_id: str
    store_name: str
    product_id: str
    name: str
    price: float
    regular_price: float
    sale_price: float | None = None
    discount_percent: float | None = None
    price_per_base_unit: float | None = None
    brand: str | None = None
    image_url: str | None = None
    deal: DealOut | None = None
    scraped_at: str


class ProductDetailOut(BaseModel):
    id: int
    name: str
    brand: str | None = None
    manufacturer: str | None = None
    barcode: str | None = None
    image_url: str | None = None
    unit_description: str | None = None
    unit_of_measure: str | None = None
    unit_qty: float | None = None
    unit_qty_si: float | None = None
    unit_dimension: str | None = None
    cheapest_price: float
    chain_count: int
    offers: list[ChainOfferOut]


class ShoppingListCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ShoppingListUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ShoppingListItemCreateIn(BaseModel):
    canonical_product_id: int
    quantity: int = Field(default=1, ge=1, le=999)


class ShoppingListItemUpdateIn(BaseModel):
    quantity: int = Field(ge=1, le=999)


class ShoppingListItemOut(BaseModel):
    id: int
    quantity: int
    product: ProductPreviewOut


class ShoppingListSummaryOut(BaseModel):
    id: int
    name: str
    item_count: int
    total_quantity: int
    updated_at: datetime


class ShoppingListDetailOut(ShoppingListSummaryOut):
    items: list[ShoppingListItemOut]


class BasketComparisonLineOut(BaseModel):
    list_item_id: int
    canonical_product_id: int
    product_name: str
    quantity: int
    matched_name: str | None = None
    unit_price: float | None = None
    regular_unit_price: float | None = None
    line_total: float | None = None
    regular_line_total: float | None = None
    deal_applied: bool = False
    deal_description: str | None = None
    image_url: str | None = None
    found: bool


class BasketComparisonChainOut(BaseModel):
    chain: str
    chain_label: str
    store_id: str
    store_name: str
    total_price: float
    regular_total_price: float
    complete: bool
    missing_count: int
    missing_products: list[str]
    applied_deals_count: int
    items: list[BasketComparisonLineOut]


class ShoppingListComparisonOut(BaseModel):
    list_id: int
    list_name: str
    item_count: int
    total_quantity: int
    chains: list[BasketComparisonChainOut]


class RefreshRunOut(BaseModel):
    run_id: int
    source: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    chains_scraped: list[str] = []
    chains_failed: list[str] = []
    products_upserted: int = 0
    errors: list[str] = []


class CatalogStatusOut(BaseModel):
    scheduler_running: bool
    refresh_in_progress: bool
    interval_hours: float
    catalog_fresh: bool
    last_refresh: RefreshRunOut | None = None
    last_successful_refresh: RefreshRunOut | None = None
    chains: list[ChainOut]


class RefreshTriggerOut(BaseModel):
    accepted: bool
    status: str
    detail: str


class MessageOut(BaseModel):
    detail: str
