from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import register_flaam_error_handler
from app.core.logging import setup_logging
from app.core.exceptions import register_exception_handlers
from app.core.idempotency import IdempotencyMiddleware
from app.core.middleware import (
    RequestIDMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
)
from app.db.redis import redis_pool
from app.db.session import engine
from app.ws.chat import router as ws_chat_router

setup_logging()
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

# Ordre requête (externe → interne) :
#   RequestID → SecurityHeaders → RequestLogging → CORS → Idempotency
# Starlette applique le DERNIER add_middleware en tant que PLUS EXTERNE,
# donc on ajoute ici dans l'ordre INVERSE : le plus interne d'abord.

# 1) Idempotency (innermost) — X-Idempotency-Key §34, TTL Redis 24h
app.add_middleware(IdempotencyMiddleware)

# 2) CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3) RequestLogging — log structuré method/path/status/duration
app.add_middleware(RequestLoggingMiddleware)

# 4) SecurityHeaders — §35.4
app.add_middleware(SecurityHeadersMiddleware)

# 5) RequestID (outermost) — bind X-Request-ID dans structlog contextvars
app.add_middleware(RequestIDMiddleware)

app.include_router(api_router, prefix=settings.api_v1_prefix)
# WebSocket chat (§5.8) — pas de préfixe API v1 : ws://host/ws/chat
app.include_router(ws_chat_router)

# Photos MVP : servis depuis STORAGE_ROOT (remplacé par R2 en Session 11).
_uploads_dir = Path(settings.storage_root)
_uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_uploads_dir)), name="uploads")

register_exception_handlers(app)
register_flaam_error_handler(app)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}
