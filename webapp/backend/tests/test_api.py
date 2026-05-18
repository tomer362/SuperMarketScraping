from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select


@pytest.mark.asyncio
async def test_register_and_me(client):
    register_response = await client.post(
        '/api/auth/register',
        json={'username': 'mobile_user', 'password': 'secret123'},
    )
    assert register_response.status_code == 200
    payload = register_response.json()
    assert payload['user']['username'] == 'mobile_user'

    me_response = await client.get('/api/auth/me')
    assert me_response.status_code == 200
    assert me_response.json()['user']['username'] == 'mobile_user'
    assert me_response.json()['user']['location_prompt_dismissed'] is False


@pytest.mark.asyncio
async def test_account_location_coordinates_and_prompt(authenticated_client):
    save_response = await authenticated_client.patch(
        '/api/account/location',
        json={
            'mode': 'coordinates',
            'latitude': 32.0853,
            'longitude': 34.7818,
            'label': 'תל אביב',
        },
    )
    assert save_response.status_code == 200
    user = save_response.json()['user']
    assert user['location_label'] == 'תל אביב'
    assert user['location_source'] == 'gps'
    assert user['location_prompt_dismissed'] is True

    prompt_response = await authenticated_client.patch('/api/account/location-prompt', json={'dismissed': False})
    assert prompt_response.status_code == 200
    assert prompt_response.json()['user']['location_prompt_dismissed'] is False

    clear_response = await authenticated_client.delete('/api/account/location')
    assert clear_response.status_code == 200
    assert clear_response.json()['user']['location_lat'] is None


@pytest.mark.asyncio
async def test_account_location_validation_and_manual_geocode(authenticated_client, monkeypatch):
    invalid_response = await authenticated_client.patch(
        '/api/account/location',
        json={'mode': 'coordinates', 'latitude': 120, 'longitude': 34},
    )
    assert invalid_response.status_code == 400

    empty_response = await authenticated_client.patch(
        '/api/account/location',
        json={'mode': 'address', 'query': ''},
    )
    assert empty_response.status_code == 400

    async def fake_geocode_address(query):
        return SimpleNamespace(latitude=32.1, longitude=34.8, label=f'{query} resolved', source='nominatim')

    import main

    monkeypatch.setattr(main, 'geocode_address', fake_geocode_address)
    manual_response = await authenticated_client.patch(
        '/api/account/location',
        json={'mode': 'address', 'query': 'דיזנגוף תל אביב'},
    )
    assert manual_response.status_code == 200
    user = manual_response.json()['user']
    assert user['location_source'] == 'nominatim'
    assert user['location_label'] == 'דיזנגוף תל אביב resolved'


@pytest.mark.asyncio
async def test_store_branch_upsert_deduplicates(authenticated_client):
    from db import async_session_factory
    from location_service import upsert_store_branches
    from models import StoreBranch

    async with async_session_factory() as session:
        rows = [
            {
                'chain': 'carrefour',
                'store_id': '3003',
                'store_name': 'קרפור כפר סבא',
                'city': 'כפר סבא',
                'address': 'ויצמן 1',
                'lat': 32.17,
                'lng': 34.91,
                'geocode_status': 'resolved',
            },
            {
                'chain': 'carrefour',
                'store_id': '3003',
                'store_name': 'קרפור כפר סבא חדש',
                'city': 'כפר סבא',
                'address': 'ויצמן 2',
                'lat': 32.18,
                'lng': 34.92,
                'geocode_status': 'resolved',
            },
        ]
        await upsert_store_branches(session, rows)
        await session.commit()

        branches = (
            await session.execute(
                select(StoreBranch).where(
                    StoreBranch.chain == 'carrefour',
                    StoreBranch.store_id == '3003',
                )
            )
        ).scalars().all()
        assert len(branches) == 1
        assert branches[0].store_name == 'קרפור כפר סבא חדש'


@pytest.mark.asyncio
async def test_comparison_uses_distance_only_after_price_tie(authenticated_client):
    from db import async_session_factory
    from location_service import upsert_store_branches
    from models import CatalogOffer

    await authenticated_client.patch(
        '/api/account/location',
        json={'mode': 'coordinates', 'latitude': 32.0853, 'longitude': 34.7818, 'label': 'תל אביב'},
    )

    async with async_session_factory() as session:
        offers = (
            await session.execute(
                select(CatalogOffer)
                .where(CatalogOffer.is_active.is_(True))
                .where(CatalogOffer.chain == 'carrefour')
                .where(CatalogOffer.name.like('%ביצים%'))
            )
        ).scalars().all()
        assert len(offers) >= 2
        for offer in offers:
            offer.price = 14.0
            offer.regular_price = 14.0
        await upsert_store_branches(session, [
            {
                'chain': 'carrefour',
                'store_id': '3003',
                'store_name': 'קרפור כפר סבא',
                'city': 'כפר סבא',
                'lat': 32.1782,
                'lng': 34.9076,
                'geocode_status': 'resolved',
            },
            {
                'chain': 'carrefour',
                'store_id': '3014',
                'store_name': 'קרפור תל אביב',
                'city': 'תל אביב',
                'lat': 32.0853,
                'lng': 34.7818,
                'geocode_status': 'resolved',
            },
        ])
        await session.commit()

    search_response = await authenticated_client.get('/api/products/search', params={'q': 'ביצים'})
    product = search_response.json()['products'][0]
    list_response = await authenticated_client.post('/api/lists', json={'name': 'מרחק'})
    shopping_list = list_response.json()
    await authenticated_client.post(
        f"/api/lists/{shopping_list['id']}/items",
        json={'canonical_product_id': product['id'], 'quantity': 1},
    )
    comparison_response = await authenticated_client.get(f"/api/lists/{shopping_list['id']}/comparison")
    assert comparison_response.status_code == 200
    carrefour = next(chain for chain in comparison_response.json()['chains'] if chain['chain'] == 'carrefour')
    assert carrefour['store_id'] == '3014'
    assert carrefour['distance_km'] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_login_failure(client):
    response = await client.post(
        '/api/auth/login',
        json={'username': 'missing', 'password': 'wrongpass'},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_suggestions_require_three_characters(authenticated_client):
    response = await authenticated_client.get('/api/search/suggest', params={'q': 'חל'})
    assert response.status_code == 200
    assert response.json()['items'] == []


@pytest.mark.asyncio
async def test_search_returns_products(authenticated_client):
    response = await authenticated_client.get('/api/products/search', params={'q': 'חלב'})
    assert response.status_code == 200
    payload = response.json()
    assert payload['total'] >= 2
    assert any('חלב' in product['name'] for product in payload['products'])


@pytest.mark.asyncio
async def test_search_returns_materialized_generic_groups(authenticated_client):
    response = await authenticated_client.get('/api/products/search', params={'q': 'חלב'})
    assert response.status_code == 200
    payload = response.json()
    milk_group = next(group for group in payload['generic_groups'] if group['family'] == 'milk')
    assert milk_group['label'] == 'חלב 3% 1 ליטר'
    assert milk_group['chain_count'] >= 2
    assert milk_group['offer_count'] >= 2
    assert milk_group['cheapest_price'] is not None


@pytest.mark.asyncio
async def test_generic_group_detail_returns_matched_offers(authenticated_client):
    search_response = await authenticated_client.get('/api/products/search', params={'q': 'חלב'})
    assert search_response.status_code == 200
    group = next(item for item in search_response.json()['generic_groups'] if item['family'] == 'milk')

    response = await authenticated_client.get(f"/api/generic-groups/{group['key']}")
    assert response.status_code == 200
    payload = response.json()
    assert payload['key'] == group['key']
    assert payload['label'] == 'חלב 3% 1 ליטר'
    assert payload['chain_count'] >= 2
    assert payload['offer_count'] >= payload['chain_count']
    assert len(payload['offers']) == payload['chain_count']
    assert payload['offers'] == sorted(payload['offers'], key=lambda offer: offer['price'])
    assert all(offer['name'] for offer in payload['offers'])
    first_offer = payload['offers'][0]
    assert 'unit_dimension' in first_offer
    assert 'unit_qty_si' in first_offer
    assert 'is_weighable' in first_offer
    assert first_offer['product_url']
    assert first_offer['product_url'].startswith('https://')


@pytest.mark.asyncio
async def test_search_handles_hebrew_apostrophe_variants(authenticated_client):
    response = await authenticated_client.get('/api/products/search', params={'q': 'קוטג׳'})
    assert response.status_code == 200
    payload = response.json()
    assert payload['total'] >= 1
    assert any('קוטג' in product['name'] for product in payload['products'])


@pytest.mark.asyncio
async def test_search_fuzzy_fallback_handles_minor_typos(authenticated_client):
    response = await authenticated_client.get('/api/products/search', params={'q': 'קוטז'})
    assert response.status_code == 200
    payload = response.json()
    assert payload['total'] >= 1
    assert any('קוטג' in product['name'] for product in payload['products'])


@pytest.mark.asyncio
async def test_search_respects_chain_filter(authenticated_client):
    response = await authenticated_client.get(
        '/api/products/search',
        params={'q': 'חלב', 'chains': 'carrefour'},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload['products']
    assert all(product['cheapest_chain'] == 'carrefour' for product in payload['products'])


@pytest.mark.asyncio
async def test_yochananof_enabled_in_catalog_data(authenticated_client):
    response = await authenticated_client.get('/api/chains')
    assert response.status_code == 200
    payload = response.json()
    yochananof = next(chain for chain in payload if chain['chain'] == 'yochananof')
    assert yochananof['enabled'] is True
    assert yochananof['status'] == 'active'


@pytest.mark.asyncio
async def test_product_detail_and_lists_flow(authenticated_client):
    search_response = await authenticated_client.get('/api/products/search', params={'q': 'קוטג'})
    product = search_response.json()['products'][0]

    list_response = await authenticated_client.post('/api/lists', json={'name': 'קניות שבועיות'})
    assert list_response.status_code == 200
    shopping_list = list_response.json()

    add_response = await authenticated_client.post(
        f"/api/lists/{shopping_list['id']}/items",
        json={'canonical_product_id': product['id'], 'quantity': 2},
    )
    assert add_response.status_code == 200
    updated_list = add_response.json()
    assert updated_list['items'][0]['quantity'] == 2

    comparison_response = await authenticated_client.get(
        f"/api/lists/{shopping_list['id']}/comparison"
    )
    assert comparison_response.status_code == 200
    comparison = comparison_response.json()
    assert comparison['chains'][0]['total_price'] <= comparison['chains'][-1]['total_price']
    assert comparison['chains'][0]['items'][0]['deal_applied'] is True


@pytest.mark.asyncio
async def test_comparison_matches_equivalent_products_split_by_missing_barcode(authenticated_client):
    search_response = await authenticated_client.get('/api/products/search', params={'q': 'קוטג'})
    product = next(item for item in search_response.json()['products'] if item['barcode'])

    detail_response = await authenticated_client.get(f"/api/products/{product['id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert any(offer['chain'] == 'ybitan' for offer in detail['offers'])

    list_response = await authenticated_client.post('/api/lists', json={'name': 'ברקוד חסר'})
    shopping_list = list_response.json()
    add_response = await authenticated_client.post(
        f"/api/lists/{shopping_list['id']}/items",
        json={'canonical_product_id': product['id'], 'quantity': 1},
    )
    assert add_response.status_code == 200

    comparison_response = await authenticated_client.get(
        f"/api/lists/{shopping_list['id']}/comparison"
    )
    assert comparison_response.status_code == 200
    comparison = comparison_response.json()
    ybitan = next(chain for chain in comparison['chains'] if chain['chain'] == 'ybitan')
    assert ybitan['complete'] is True
    assert ybitan['items'][0]['found'] is True


@pytest.mark.asyncio
async def test_generic_group_list_and_comparison_flow(authenticated_client):
    search_response = await authenticated_client.get('/api/products/search', params={'q': 'חלב'})
    group = next(item for item in search_response.json()['generic_groups'] if item['family'] == 'milk')

    list_response = await authenticated_client.post('/api/lists', json={'name': 'השוואה כללית'})
    shopping_list = list_response.json()

    add_response = await authenticated_client.post(
        f"/api/lists/{shopping_list['id']}/items",
        json={'generic_group_key': group['key'], 'quantity': 2},
    )
    assert add_response.status_code == 200
    list_payload = add_response.json()
    assert list_payload['items'][0]['product'] is None
    assert list_payload['items'][0]['generic_group']['key'] == group['key']

    comparison_response = await authenticated_client.get(
        f"/api/lists/{shopping_list['id']}/comparison"
    )
    assert comparison_response.status_code == 200
    comparison = comparison_response.json()
    first_line = comparison['chains'][0]['items'][0]
    assert first_line['canonical_product_id'] is None
    assert first_line['generic_group_key'] == group['key']
    assert first_line['matched_name']
    assert first_line['found'] is True


@pytest.mark.asyncio
async def test_weighable_quantity_supports_fractional_amount(authenticated_client):
    search_response = await authenticated_client.get('/api/products/search', params={'q': 'עוף'})
    assert search_response.status_code == 200
    products = search_response.json()['products']
    product = next(item for item in products if item['is_weighable'])

    list_response = await authenticated_client.post('/api/lists', json={'name': 'משקלים'})
    shopping_list = list_response.json()

    add_response = await authenticated_client.post(
        f"/api/lists/{shopping_list['id']}/items",
        json={'canonical_product_id': product['id'], 'quantity': 0.5},
    )
    assert add_response.status_code == 200
    list_payload = add_response.json()
    assert list_payload['items'][0]['quantity'] == pytest.approx(0.5)

    comparison_response = await authenticated_client.get(
        f"/api/lists/{shopping_list['id']}/comparison"
    )
    comparison = comparison_response.json()
    first_line = comparison['chains'][0]['items'][0]
    assert first_line['quantity'] == pytest.approx(0.5)
    assert first_line['line_total'] == pytest.approx(first_line['unit_price'] * 0.5, rel=1e-2)
    assert first_line['purchased_quantity'] == pytest.approx(0.5)
    assert first_line['fulfillment_description']


def _offer(**overrides):
    base = {
        "id": 1,
        "price": 30.0,
        "regular_price": 30.0,
        "is_weighable": False,
        "unit_qty_si": 500.0,
        "unit_dimension": "mass",
        "deal": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_fulfillment_combines_packaged_items_for_requested_weight():
    from catalog_service import _choose_best_offer_for_quantity

    one_kg = _offer(id=1, price=70.0, regular_price=70.0, unit_qty_si=1000.0)
    half_kg = _offer(id=2, price=32.0, regular_price=32.0, unit_qty_si=500.0)

    offer, totals = _choose_best_offer_for_quantity([one_kg, half_kg], 1.0, fulfillment_family="salmon")

    assert offer.id == 2
    assert totals["package_count"] == 2
    assert totals["line_total"] == pytest.approx(64.0)
    assert totals["fulfillment_description"] == "2 × 500 גרם"


def test_fulfillment_applies_multibuy_deals_to_package_count():
    from catalog_service import _line_totals_for_offer

    offer = _offer(
        price=40.0,
        regular_price=40.0,
        unit_qty_si=500.0,
        deal={
            "has_deal": True,
            "deal_type": "multi_buy",
            "deal_min_qty": 2,
            "deal_price": 70.0,
            "deal_description": "2 ב-70",
        },
    )

    totals = _line_totals_for_offer(offer, 1.0, fulfillment_family="salmon")

    assert totals["package_count"] == 2
    assert totals["line_total"] == pytest.approx(70.0)
    assert totals["deal_applied"] is True
    assert totals["deal_description"] == "2 ב-70"


@pytest.mark.asyncio
async def test_catalog_status(authenticated_client):
    response = await authenticated_client.get('/api/catalog/status')
    assert response.status_code == 200
    payload = response.json()
    assert payload['last_successful_refresh'] is not None
    assert payload['last_successful_price_refresh'] is not None
    assert 'last_successful_deals_refresh' in payload
    assert payload['price_interval_hours'] == 24
    assert payload['deals_interval_hours'] == 4
    assert payload['chains']


@pytest.mark.asyncio
async def test_catalog_refresh_trigger_returns_while_refresh_runs(authenticated_client, monkeypatch):
    from main import scheduler

    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_refresh(source: str, refresh_kind: str) -> dict:
        started.set()
        await release.wait()
        return {
            "run_id": 999,
            "source": source,
            "refresh_kind": refresh_kind,
            "status": "done",
            "started_at": None,
            "finished_at": None,
            "chains_scraped": [],
            "chains_failed": [],
            "products_upserted": 0,
            "errors": [],
        }

    monkeypatch.setattr(scheduler, "_refresh_callback", fake_refresh)

    response = await authenticated_client.post('/api/catalog/refresh')
    assert response.status_code == 200
    assert response.json() == {
        "accepted": True,
        "status": "started",
        "detail": "Catalog prices refresh started.",
    }
    await asyncio.wait_for(started.wait(), timeout=1)

    duplicate = await authenticated_client.post('/api/catalog/refresh')
    assert duplicate.status_code == 200
    assert duplicate.json()["accepted"] is False

    release.set()
    await asyncio.wait_for(scheduler._refresh_task, timeout=1)


@pytest.mark.asyncio
async def test_catalog_refresh_kind_triggers(authenticated_client, monkeypatch):
    from main import scheduler

    calls: list[tuple[str, str]] = []

    async def fake_refresh(source: str, refresh_kind: str) -> dict:
        calls.append((source, refresh_kind))
        return {
            "run_id": 1000 + len(calls),
            "source": source,
            "refresh_kind": refresh_kind,
            "status": "done",
            "started_at": None,
            "finished_at": None,
            "chains_scraped": [],
            "chains_failed": [],
            "products_upserted": 0,
            "errors": [],
        }

    monkeypatch.setattr(scheduler, "_refresh_callback", fake_refresh)

    price_response = await authenticated_client.post('/api/catalog/refresh/prices')
    assert price_response.status_code == 200
    assert price_response.json()["accepted"] is True
    await asyncio.wait_for(scheduler._refresh_task, timeout=1)

    deals_response = await authenticated_client.post('/api/catalog/refresh/deals')
    assert deals_response.status_code == 200
    assert deals_response.json()["accepted"] is True
    await asyncio.wait_for(scheduler._refresh_task, timeout=1)

    assert calls == [("manual", "prices"), ("manual", "deals")]


@pytest.mark.asyncio
async def test_deals_merge_updates_existing_offers_without_inserting(authenticated_client):
    from catalog_service import (
        build_active_generic_groups,
        merge_deal_staging_into_active,
        stage_existing_offer_deals,
    )
    from db import async_session_factory
    from models import CatalogOffer, CatalogRefreshRun, GenericProductGroup, GenericProductGroupMember

    async with async_session_factory() as session:
        offer = (
            await session.execute(
                select(CatalogOffer)
                .where(CatalogOffer.is_active.is_(True))
                .where(CatalogOffer.chain == "carrefour")
                .limit(1)
            )
        ).scalar_one()
        group_key = (
            await session.execute(
                select(GenericProductGroupMember.group_key)
                .where(GenericProductGroupMember.chain == offer.chain)
                .where(GenericProductGroupMember.store_id == offer.store_id)
                .where(GenericProductGroupMember.product_id == offer.product_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        before_count = (
            await session.execute(
                select(func.count()).select_from(CatalogOffer).where(CatalogOffer.is_active.is_(True))
            )
        ).scalar_one()

        run = CatalogRefreshRun(source="test", refresh_kind="deals", status="running")
        session.add(run)
        await session.flush()
        products = [
            {
                "chain": offer.chain,
                "store_id": offer.store_id,
                "store_name": offer.store_name,
                "product_id": offer.product_id,
                "name": offer.name,
                "barcode": offer.barcode,
                "price": 1.99,
                "regular_price": offer.regular_price,
                "sale_price": 1.99,
                "discount_percent": 50.0,
                "price_per_base_unit": 0.199,
                "deal": {"has_deal": True, "deal_type": "price_reduction", "deal_price": 1.99},
                "scraped_at": "2026-05-18T00:00:00+00:00",
            },
            {
                "chain": "carrefour",
                "store_id": offer.store_id,
                "store_name": offer.store_name,
                "product_id": "new-deal-product",
                "name": "New product",
                "price": 0.99,
                "regular_price": 0.99,
                "scraped_at": "2026-05-18T00:00:00+00:00",
            },
        ]

        staged = await stage_existing_offer_deals(session, products, run.id)
        updated = await merge_deal_staging_into_active(session)
        await build_active_generic_groups(session)
        await session.commit()

        refreshed = await session.get(CatalogOffer, offer.id)
        after_count = (
            await session.execute(
                select(func.count()).select_from(CatalogOffer).where(CatalogOffer.is_active.is_(True))
            )
        ).scalar_one()
        new_product = (
            await session.execute(
                select(CatalogOffer).where(CatalogOffer.product_id == "new-deal-product")
            )
        ).scalar_one_or_none()

        assert staged == 1
        assert updated == 1
        assert after_count == before_count
        assert new_product is None
        assert refreshed.price == pytest.approx(1.99)
        assert refreshed.sale_price == pytest.approx(1.99)
        assert refreshed.deal["deal_price"] == pytest.approx(1.99)

        if group_key:
            group = await session.get(GenericProductGroup, group_key)
            assert group.cheapest_price == pytest.approx(1.99)
