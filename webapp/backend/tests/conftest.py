from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///./webapp_test.sqlite3')
os.environ.setdefault('ENABLE_SCHEDULER', '0')
os.environ.setdefault('AUTO_REFRESH_ON_START', '0')
os.environ.setdefault('SEED_TEST_DATA', '1')
os.environ.setdefault('RESET_TEST_DB_ON_START', '1')
os.environ.setdefault('SESSION_COOKIE_SECURE', '0')
os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from main import app  # noqa: E402


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url='http://testserver') as test_client:
            yield test_client


@pytest.fixture
async def authenticated_client(client: AsyncClient) -> AsyncClient:
    response = await client.post(
        '/api/auth/register',
        json={'username': 'tester_one', 'password': 'secret123'},
    )
    assert response.status_code == 200
    return client
