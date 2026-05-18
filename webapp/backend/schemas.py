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
    location_lat: float | None = None
    location_lng: float | None = None
    location_label: str | None = None
    location_source: str | None = None
    location_updated_at: datetime | None = None
    location_prompt_dismissed: bool = False


class AuthPayload(BaseModel):
    user: UserOut


class RegisterIn(BaseModel):
    username: str
    password: str


class LoginIn(BaseModel):
    username: str
    password: str


class LocationUpdateIn(BaseModel):
    mode: str
    latitude: float | None = None
    longitude: float | None = None
    label: str | None = Field(default=None, max_length=255)
    query: str | None = Field(default=None, max_length=255)


class LocationPromptUpdateIn(BaseModel):
    dismissed: bool = True


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
    is_weighable: bool = False
    cheapest_price: float
    cheapest_chain: str
    cheapest_chain_label: str
    cheapest_store_name: str
    chain_count: int
    has_deal: bool


class GenericProductGroupOut(BaseModel):
    key: str
    label: str
    family: str
    offer_count: int
    chain_count: int
    cheapest_price: float | None = None


class ProductSearchResultOut(BaseModel):
    query: str
    total: int
    products: list[ProductPreviewOut]
    generic_groups: list[GenericProductGroupOut] = []


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
    is_weighable: bool = False
    unit_description: str | None = None
    unit_of_measure: str | None = None
    unit_qty: float | None = None
    unit_qty_si: float | None = None
    unit_dimension: str | None = None
    price_per_base_unit: float | None = None
    brand: str | None = None
    image_url: str | None = None
    product_url: str | None = None
    deal: DealOut | None = None
    scraped_at: str
    distance_km: float | None = None


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
    is_weighable: bool = False
    cheapest_price: float
    chain_count: int
    offers: list[ChainOfferOut]


class GenericProductGroupDetailOut(GenericProductGroupOut):
    offers: list[ChainOfferOut]


class ShoppingListCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ShoppingListUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ShoppingListItemCreateIn(BaseModel):
    canonical_product_id: int | None = None
    generic_group_key: str | None = None
    quantity: float = Field(default=1.0, ge=0.1, le=999)


class ShoppingListItemUpdateIn(BaseModel):
    quantity: float = Field(ge=0.1, le=999)


class ShoppingListItemOut(BaseModel):
    id: int
    quantity: float
    product: ProductPreviewOut | None = None
    generic_group: GenericProductGroupOut | None = None


class ShoppingListSummaryOut(BaseModel):
    id: int
    name: str
    item_count: int
    total_quantity: float
    updated_at: datetime


class ShoppingListDetailOut(ShoppingListSummaryOut):
    items: list[ShoppingListItemOut]


class BasketComparisonLineOut(BaseModel):
    list_item_id: int
    canonical_product_id: int | None = None
    generic_group_key: str | None = None
    product_name: str
    quantity: float
    matched_name: str | None = None
    unit_price: float | None = None
    regular_unit_price: float | None = None
    line_total: float | None = None
    regular_line_total: float | None = None
    purchased_quantity: float | None = None
    purchased_quantity_si: float | None = None
    package_count: int | None = None
    package_size_label: str | None = None
    fulfillment_description: str | None = None
    deal_applied: bool = False
    deal_description: str | None = None
    image_url: str | None = None
    product_url: str | None = None
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
    distance_km: float | None = None
    items: list[BasketComparisonLineOut]


class ShoppingListComparisonOut(BaseModel):
    list_id: int
    list_name: str
    item_count: int
    total_quantity: float
    chains: list[BasketComparisonChainOut]


class RefreshRunOut(BaseModel):
    run_id: int
    source: str
    refresh_kind: str = "prices"
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
    price_interval_hours: float
    deals_interval_hours: float
    catalog_fresh: bool
    prices_fresh: bool
    deals_fresh: bool
    last_refresh: RefreshRunOut | None = None
    last_successful_refresh: RefreshRunOut | None = None
    last_price_refresh: RefreshRunOut | None = None
    last_successful_price_refresh: RefreshRunOut | None = None
    last_deals_refresh: RefreshRunOut | None = None
    last_successful_deals_refresh: RefreshRunOut | None = None
    chains: list[ChainOut]


class RefreshTriggerOut(BaseModel):
    accepted: bool
    status: str
    detail: str


class MessageOut(BaseModel):
    detail: str
