from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.cities import router as cities_router
from app.api.v1.events import router as events_router
from app.api.v1.feed import router as feed_router
from app.api.v1.invites import router as invites_router
from app.api.v1.matches import router as matches_router
from app.api.v1.messages import router as messages_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.photos import router as photos_router
from app.api.v1.profiles import router as profiles_router
from app.api.v1.quartiers import router as quartiers_router
from app.api.v1.spots import router as spots_router
from app.api.v1.subscriptions import router as subscriptions_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(profiles_router)
api_router.include_router(photos_router)
api_router.include_router(quartiers_router)
api_router.include_router(spots_router)
api_router.include_router(cities_router)
api_router.include_router(invites_router)
api_router.include_router(feed_router)
api_router.include_router(matches_router)
api_router.include_router(messages_router)
api_router.include_router(events_router)
api_router.include_router(notifications_router)
api_router.include_router(subscriptions_router)
