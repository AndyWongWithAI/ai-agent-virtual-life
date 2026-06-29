"""integration 测试 fixture:真连 docker postgres(5433)+ redis(6380)

跑法:docker compose -f infra/docker-compose.yml up -d
     uv run pytest -m integration -v
"""
import os

import pytest
import pytest_asyncio


@pytest.fixture(scope="session")
def redis_url():
    return os.getenv("REDIS_URL", "redis://localhost:6380/0")


@pytest.fixture(scope="session")
def database_url():
    return os.getenv("DATABASE_URL", "postgresql+asyncpg://town:town_dev_pwd@localhost:5433/town")


@pytest_asyncio.fixture(scope="function")  # async fixture,event loop 一致
async def event_store(database_url):
    from event_memory_system import EventStore
    store = EventStore(database_url)
    await store.init_schema()
    return store