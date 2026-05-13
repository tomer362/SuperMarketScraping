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
async def test_catalog_status(authenticated_client):
    response = await authenticated_client.get('/api/catalog/status')
    assert response.status_code == 200
    payload = response.json()
    assert payload['last_successful_refresh'] is not None
    assert payload['chains']
