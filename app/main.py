from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.db.redis import redis_pool
from app.db.session import engine

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

app.include_router(api_router, prefix=settings.api_v1_prefix)

register_exception_handlers(app)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}
