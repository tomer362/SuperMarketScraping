from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class User(TimestampMixin, Base):
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    username_normalized: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    shopping_lists: Mapped[list[ShoppingList]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="sessions")


class CatalogRefreshRun(Base):
    __tablename__ = "catalog_refresh_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduler")
    refresh_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="prices")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chains_scraped: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    chains_failed: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    products_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class CanonicalProduct(TimestampMixin, Base):
    __tablename__ = "canonical_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_key: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    brand: Mapped[str | None] = mapped_column(String(255))
    normalized_brand: Mapped[str] = mapped_column(Text, nullable=False, default="")
    barcode: Mapped[str | None] = mapped_column(String(32), index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(255))
    image_url: Mapped[str | None] = mapped_column(Text)
    unit_description: Mapped[str | None] = mapped_column(String(64))
    unit_of_measure: Mapped[str | None] = mapped_column(String(32))
    unit_qty: Mapped[float | None] = mapped_column(Float)
    unit_qty_si: Mapped[float | None] = mapped_column(Float)
    unit_dimension: Mapped[str | None] = mapped_column(String(32))
    search_text: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    offers: Mapped[list[CatalogOffer]] = relationship(back_populates="canonical_product")
    list_items: Mapped[list[ShoppingListItem]] = relationship(back_populates="canonical_product")


class CatalogOffer(TimestampMixin, Base):
    __tablename__ = "catalog_offers"
    __table_args__ = (
        UniqueConstraint("chain", "store_id", "product_id", name="uq_offer_store_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_product_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_products.id"), index=True, nullable=False
    )
    refresh_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalog_refresh_runs.id"), index=True
    )
    chain: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    store_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    store_name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    barcode: Mapped[str | None] = mapped_column(String(32), index=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    regular_price: Mapped[float] = mapped_column(Float, nullable=False)
    sale_price: Mapped[float | None] = mapped_column(Float)
    discount_percent: Mapped[float | None] = mapped_column(Float)
    is_weighable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    unit_description: Mapped[str | None] = mapped_column(String(64))
    unit_of_measure: Mapped[str | None] = mapped_column(String(32))
    unit_qty: Mapped[float | None] = mapped_column(Float)
    unit_qty_si: Mapped[float | None] = mapped_column(Float)
    unit_dimension: Mapped[str | None] = mapped_column(String(32))
    price_per_base_unit: Mapped[float | None] = mapped_column(Float)
    image_url: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(String(255))
    manufacturer: Mapped[str | None] = mapped_column(String(255))
    category_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    deal: Mapped[dict | None] = mapped_column(JSON)
    scraped_at: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    canonical_product: Mapped[CanonicalProduct] = relationship(back_populates="offers")


class CatalogOfferStaging(TimestampMixin, Base):
    __tablename__ = "catalog_offers_staging"
    __table_args__ = (
        UniqueConstraint("chain", "store_id", "product_id", name="uq_stage_offer_store_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_product_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_products.id"), index=True, nullable=False
    )
    refresh_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalog_refresh_runs.id"), index=True
    )
    chain: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    store_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    store_name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    barcode: Mapped[str | None] = mapped_column(String(32), index=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    regular_price: Mapped[float] = mapped_column(Float, nullable=False)
    sale_price: Mapped[float | None] = mapped_column(Float)
    discount_percent: Mapped[float | None] = mapped_column(Float)
    is_weighable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    unit_description: Mapped[str | None] = mapped_column(String(64))
    unit_of_measure: Mapped[str | None] = mapped_column(String(32))
    unit_qty: Mapped[float | None] = mapped_column(Float)
    unit_qty_si: Mapped[float | None] = mapped_column(Float)
    unit_dimension: Mapped[str | None] = mapped_column(String(32))
    price_per_base_unit: Mapped[float | None] = mapped_column(Float)
    image_url: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(String(255))
    manufacturer: Mapped[str | None] = mapped_column(String(255))
    category_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    deal: Mapped[dict | None] = mapped_column(JSON)
    scraped_at: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class GenericProductGroup(TimestampMixin, Base):
    __tablename__ = "generic_product_groups"

    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    family: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    offer_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chain_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cheapest_price: Mapped[float | None] = mapped_column(Float)


class GenericProductGroupStaging(TimestampMixin, Base):
    __tablename__ = "generic_product_groups_staging"

    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    family: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    offer_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chain_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cheapest_price: Mapped[float | None] = mapped_column(Float)


class GenericProductGroupMember(Base):
    __tablename__ = "generic_product_group_members"
    __table_args__ = (
        UniqueConstraint("group_key", "chain", "store_id", "product_id", name="uq_generic_member_offer"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_key: Mapped[str] = mapped_column(String(512), index=True, nullable=False)
    chain: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    store_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    product_id: Mapped[str] = mapped_column(String(128), nullable=False)


class GenericProductGroupMemberStaging(Base):
    __tablename__ = "generic_product_group_members_staging"
    __table_args__ = (
        UniqueConstraint("group_key", "chain", "store_id", "product_id", name="uq_stage_generic_member_offer"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_key: Mapped[str] = mapped_column(String(512), index=True, nullable=False)
    chain: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    store_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    product_id: Mapped[str] = mapped_column(String(128), nullable=False)


class ShoppingList(TimestampMixin, Base):
    __tablename__ = "shopping_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    user: Mapped[User] = relationship(back_populates="shopping_lists")
    items: Mapped[list[ShoppingListItem]] = relationship(
        back_populates="shopping_list", cascade="all, delete-orphan"
    )


class ShoppingListItem(Base):
    __tablename__ = "shopping_list_items"
    __table_args__ = (
        UniqueConstraint(
            "shopping_list_id",
            "canonical_product_id",
            name="uq_list_product",
        ),
        UniqueConstraint(
            "shopping_list_id",
            "generic_group_key",
            name="uq_list_generic_group",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shopping_list_id: Mapped[int] = mapped_column(
        ForeignKey("shopping_lists.id"), index=True, nullable=False
    )
    canonical_product_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_products.id"), index=True, nullable=True
    )
    generic_group_key: Mapped[str | None] = mapped_column(String(512), index=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    shopping_list: Mapped[ShoppingList] = relationship(back_populates="items")
    canonical_product: Mapped[CanonicalProduct] = relationship(back_populates="list_items")
