from __future__ import annotations

"""
Fixtures communes pytest.

Stratégie :
- DB : une base dédiée `flaam_test` créée à la session, puis schéma
  via `Base.metadata.create_all()` (pas d'alembic dans les tests pour
  la vitesse). Rollback par transaction savepoint à chaque test.
- Redis : DB numérique dédiée (15) flushée au setup de chaque test.
- HTTP : httpx.AsyncClient avec ASGITransport — pas de server réel.
"""

import os
from typing import AsyncIterator

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Force la config avant d'importer l'app
os.environ.setdefault("SMS_SIMULATE", "true")
os.environ.setdefault("SECRET_KEY", "test-secret-key-64-chars-aaaaaaaaaaaaaaaaaaaaaaaa")
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://flaam:password@db:5432/flaam_test",
)
TEST_REDIS_URL = os.environ.get(
    "TEST_REDIS_URL", "redis://redis:6379/15"
)
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["REDIS_URL"] = TEST_REDIS_URL

from app.core import dependencies as core_deps  # noqa: E402
from app.db.base import Base  # noqa: E402
import app.models  # noqa: F401,E402  — force registration
from app.db import redis as redis_module  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine():
    eng = create_async_engine(TEST_DB_URL, future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture()
async def db_session(engine) -> AsyncIterator[AsyncSession]:
    """Session isolée : chaque test tourne dans une transaction rollbacked."""
    connection = await engine.connect()
    trans = await connection.begin()
    factory = async_sessionmaker(bind=connection, expire_on_commit=False)
    session = factory()

    # Nested savepoint : `session.commit()` dans le code prod ne ferme
    # que le savepoint, la transaction externe est rollbackée après.
    await connection.begin_nested()

    from sqlalchemy import event

    @event.listens_for(session.sync_session, "after_transaction_end")
    def restart_savepoint(sess, transaction):
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    yield session

    await session.close()
    await trans.rollback()
    await connection.close()


@pytest_asyncio.fixture()
async def redis_client() -> AsyncIterator[aioredis.Redis]:
    client = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    await client.flushdb()
    # Patch le pool global pour que get_redis() retourne notre client
    redis_module.redis_pool._pool = client
    yield client
    await client.flushdb()
    await client.aclose()


@pytest_asyncio.fixture()
async def client(db_session, redis_client) -> AsyncIterator[AsyncClient]:
    async def _get_db():
        yield db_session

    app.dependency_overrides[core_deps.get_db] = _get_db
    app.dependency_overrides[core_deps.get_redis] = lambda: redis_client

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver/api/v1"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture()
async def test_user(db_session):
    """Utilisateur authentifié prêt pour les tests qui nécessitent un JWT."""
    from app.models.user import User
    from app.utils.phone import hash_phone

    user = User(
        phone_hash=hash_phone("+22899999999"),
        phone_country_code="228",
        is_phone_verified=True,
        onboarding_step="city_selection",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture()
def auth_headers(test_user):
    from app.core.security import create_access_token

    token = create_access_token(test_user.id)
    return {"Authorization": f"Bearer {token}"}
