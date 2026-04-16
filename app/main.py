from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.idempotency import IdempotencyMiddleware
from app.db.redis import redis_pool
from app.db.session import engine
from app.ws.chat import router as ws_chat_router

logger = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_pool.initialize()
    logger.info("startup_complete", env=settings.app_env)
    yield
    await engine.dispose()
    await redis_pool.close()
    logger.info("shutdown_complete")


app = FastAPI(
    title="Flaam API",
    version="1.0.0",
    docs_url="/docs" if settings.app_debug else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# X-Idempotency-Key middleware (§34). Applique sur POST/PATCH/PUT/DELETE
# quand l'header est présent. TTL Redis 24h.
app.add_middleware(IdempotencyMiddleware)

app.include_router(api_router, prefix=settings.api_v1_prefix)
# WebSocket chat (§5.8) — pas de préfixe API v1 : ws://host/ws/chat
app.include_router(ws_chat_router)

# Photos MVP : servis depuis STORAGE_ROOT (remplacé par R2 en Session 11).
_uploads_dir = Path(settings.storage_root)
_uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_uploads_dir)), name="uploads")

register_exception_handlers(app)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}
