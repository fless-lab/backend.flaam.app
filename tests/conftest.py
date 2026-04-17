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
    try:
        await client.flushdb()
    except Exception:  # noqa: BLE001
        pass  # connection may be corrupted by cross-loop WS handler
    try:
        await client.aclose()
    except Exception:  # noqa: BLE001
        pass


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
async def sync_client(engine, redis_client):
    """
    Client synchrone Starlette pour les tests WebSocket.

    Problème loop : le TestClient Starlette exécute l'app dans un
    thread/loop séparé. Les connexions asyncpg sont loop-affines donc
    on ne peut pas partager le pool avec pytest-asyncio.

    Solution : on rebind ``app.db.session.async_session`` sur un engine
    ``NullPool`` (pas de cache de connexion) pour la durée du test,
    et on laisse le lifespan du TestClient (re)initialiser le redis_pool
    dans son propre loop.
    """
    from starlette.testclient import TestClient
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.db import session as session_module
    from app.ws import chat as ws_chat_module

    # Factory qui crée un engine NEUF par session. asyncpg attache des
    # Futures au loop courant à la création du protocole ; un engine
    # partagé entre plusieurs portails (starlette crée un portal par
    # websocket_connect) casse cet invariant.
    #
    # Subtilité : `app.ws.chat` a fait `from app.db.session import
    # async_session`, donc il détient une RÉFÉRENCE au factory original.
    # Monkeypatcher uniquement `session_module.async_session` ne suffit
    # pas : il faut aussi patcher le nom dans chaque module consommateur.
    original_async_session = session_module.async_session
    original_ws_async_session = ws_chat_module.async_session

    def _fresh_session():
        eng = create_async_engine(TEST_DB_URL, poolclass=NullPool)
        factory = async_sessionmaker(
            eng, class_=AsyncSession, expire_on_commit=False
        )
        return factory()

    session_module.async_session = _fresh_session
    ws_chat_module.async_session = _fresh_session

    # The TestClient lifespan calls redis_pool.initialize() → creates a
    # Redis connection on its internal loop. On shutdown redis_pool.close()
    # races with the loop closing → "Event loop is closed". We wrap close()
    # to swallow this race. initialize() runs normally so the WS handler
    # gets a proper same-loop Redis client.
    original_close = redis_module.redis_pool.close

    async def _safe_close() -> None:
        try:
            await original_close()
        except (RuntimeError, Exception):
            # "Event loop is closed" during TestClient shutdown
            redis_module.redis_pool._pool = None

    redis_module.redis_pool.close = _safe_close  # type: ignore[assignment]

    tc = TestClient(app)
    try:
        yield tc
    finally:
        try:
            tc.close()
        except RuntimeError:
            pass  # "Event loop is closed" race in TestClient shutdown
        redis_module.redis_pool.close = original_close  # type: ignore[assignment]
        redis_module.redis_pool._pool = redis_client
        session_module.async_session = original_async_session
        ws_chat_module.async_session = original_ws_async_session
        app.dependency_overrides.clear()
        # Nettoyage : vide les données créées par les WS tests.
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE TABLE messages, matches, user_spots, "
                    "user_quartiers, photos, profiles, users, spots, "
                    "quartier_proximities, quartiers, cities "
                    "RESTART IDENTITY CASCADE"
                )
            )


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


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """
    Redirige STORAGE_ROOT vers un tmp_path par test pour que les photos
    uploadées ne polluent pas le volume persistant entre runs.
    """
    from app.core import config
    from app.services import photo_service

    settings = config.get_settings()
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    monkeypatch.setattr(photo_service.settings, "storage_root", str(tmp_path))
    return tmp_path
