from __future__ import annotations

import pytest


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


@pytest.mark.asyncio
async def test_catalog_status(authenticated_client):
    response = await authenticated_client.get('/api/catalog/status')
    assert response.status_code == 200
    payload = response.json()
    assert payload['last_successful_refresh'] is not None
    assert payload['chains']
