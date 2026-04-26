"""Service du mode voyage.

Règles métier (validées produit) :
- Durées proposées : 3, 7 (default), 14, 30 jours.
- Une seule destination active à la fois.
- Max 2 activations sur fenêtre glissante 30 jours (anti-city-hopping).
- Prolongation +7 jours, 1× par session de voyage.
- Auto-expiration via Celery (`expire_travel_modes`).
- Au-delà → l'user doit changer sa ville principale (limit 1×/30j).

Transparence : `effective_city_id(user)` renvoie la ville à utiliser
pour le feed/discovery (travel si actif, sinon city_id). Les autres
users voient un badge "En visite" sur le profil avec destination + durée.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import FlaamError
from app.models.city import City
from app.models.user import User
from app.schemas.travel import (
    CityChangeResponse,
    TravelDuration,
    TravelStatusResponse,
)


MAX_ACTIVATIONS_PER_30D = 2
EXTENSION_DAYS = 7
HOME_CITY_COOLDOWN_DAYS = 30

# Rayon de validation GPS autour du centre de la ville de destination.
# 30km couvre les agglomérations africaines (Lomé, Abidjan, Dakar) sans
# accepter une confirmation depuis une autre ville voisine.
GPS_CONFIRMATION_RADIUS_KM = 30.0
# Le badge "Confirmé" reste affiché 24h après la dernière détection.
GPS_CONFIRMATION_VALID_HOURS = 24


def _duration_to_delta(duration: TravelDuration) -> timedelta:
    return {
        "3d": timedelta(days=3),
        "7d": timedelta(days=7),
        "14d": timedelta(days=14),
        "30d": timedelta(days=30),
    }[duration]


def is_traveling(user: User, now: datetime | None = None) -> bool:
    """True si l'user a un voyage actif (city set + until dans le futur)."""
    if user.travel_city_id is None or user.travel_until is None:
        return False
    n = now or datetime.now(timezone.utc)
    return user.travel_until > n


def is_travel_gps_confirmed(user: User, now: datetime | None = None) -> bool:
    """True si la confirmation GPS date de < GPS_CONFIRMATION_VALID_HOURS."""
    if user.travel_gps_confirmed_at is None:
        return False
    n = now or datetime.now(timezone.utc)
    return (n - user.travel_gps_confirmed_at) < timedelta(
        hours=GPS_CONFIRMATION_VALID_HOURS,
    )


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance en km entre 2 points (formule haversine, R=6371 km)."""
    import math
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return 6371.0 * c


async def try_confirm_travel(
    user: User, lat: float, lng: float, db: AsyncSession
) -> bool:
    """Tentative de confirmation passive du voyage à partir d'un GPS.

    Appelé depuis les endpoints qui reçoivent déjà du lat/lng (check-in
    spot, scan flame). Idempotent et silencieux : si l'user n'est pas
    en voyage ou est trop loin, no-op. Retourne True si la confirmation
    a été posée/rafraîchie.
    """
    if not is_traveling(user) or user.travel_city_id is None:
        return False
    city = await db.get(City, user.travel_city_id)
    if city is None or city.latitude is None or city.longitude is None:
        return False
    distance = _haversine_km(lat, lng, city.latitude, city.longitude)
    if distance > GPS_CONFIRMATION_RADIUS_KM:
        return False
    user.travel_gps_confirmed_at = datetime.now(timezone.utc)
    await db.commit()
    return True


def effective_city_id(user: User, now: datetime | None = None) -> UUID | None:
    """Ville à utiliser pour le feed/discovery (travel si actif)."""
    if is_traveling(user, now):
        return user.travel_city_id
    return user.city_id


def _activations_remaining(user: User, now: datetime) -> int:
    """Combien d'activations restent sur la fenêtre 30j courante."""
    if user.travel_window_start is None:
        return MAX_ACTIVATIONS_PER_30D
    window_end = user.travel_window_start + timedelta(days=30)
    if window_end <= now:
        return MAX_ACTIVATIONS_PER_30D
    used = user.travel_activations_count_30d or 0
    return max(0, MAX_ACTIVATIONS_PER_30D - used)


async def get_status(
    user: User, db: AsyncSession, lang: str = "fr"
) -> TravelStatusResponse:
    """Retourne l'état du mode voyage pour cet user."""
    now = datetime.now(timezone.utc)
    active = is_traveling(user, now)
    travel_city_name: str | None = None
    if user.travel_city_id is not None:
        city = await db.get(City, user.travel_city_id)
        travel_city_name = city.name if city else None

    remaining = _activations_remaining(user, now)
    can_activate = (not active) and remaining > 0
    can_extend = active and not bool(user.travel_extension_used)

    return TravelStatusResponse(
        is_active=active,
        travel_city_id=user.travel_city_id if active else None,
        travel_city_name=travel_city_name if active else None,
        travel_started_at=user.travel_started_at if active else None,
        travel_until=user.travel_until if active else None,
        extension_used=bool(user.travel_extension_used),
        gps_confirmed=is_travel_gps_confirmed(user, now) if active else False,
        can_extend=can_extend,
        activations_remaining=remaining,
        can_activate=can_activate,
    )


async def activate(
    user: User,
    city_id: UUID,
    duration: TravelDuration,
    db: AsyncSession,
    lang: str = "fr",
) -> TravelStatusResponse:
    """Active le mode voyage. Vérifie quota, ville différente, etc."""
    now = datetime.now(timezone.utc)
    if is_traveling(user, now):
        raise FlaamError("travel_already_active", 409, lang)

    # Reset fenêtre 30j si périmée
    if (
        user.travel_window_start is None
        or user.travel_window_start + timedelta(days=30) <= now
    ):
        user.travel_window_start = now
        user.travel_activations_count_30d = 0

    if (user.travel_activations_count_30d or 0) >= MAX_ACTIVATIONS_PER_30D:
        # Date à laquelle l'user pourra réactiver = window_start + 30j
        next_date = (
            user.travel_window_start + timedelta(days=30)
        ).strftime("%d/%m/%Y")
        raise FlaamError(
            "travel_max_activations", 429, lang, next_date=next_date,
        )

    city = await db.get(City, city_id)
    if city is None:
        raise FlaamError("travel_invalid_city", 404, lang)
    if user.city_id == city_id:
        raise FlaamError("travel_same_city", 400, lang)

    user.travel_city_id = city_id
    user.travel_started_at = now
    user.travel_until = now + _duration_to_delta(duration)
    user.travel_extension_used = False
    user.travel_gps_confirmed_at = None
    user.travel_activations_count_30d = (
        user.travel_activations_count_30d or 0
    ) + 1
    await db.commit()
    await db.refresh(user)
    return await get_status(user, db, lang)


async def extend(
    user: User, db: AsyncSession, lang: str = "fr"
) -> TravelStatusResponse:
    """Prolonge le voyage actif de 7 jours, 1× par session.

    Garde-fou : si l'extension pousse au-delà de 30j depuis le début,
    on refuse → l'user doit changer sa ville principale.
    """
    now = datetime.now(timezone.utc)
    if not is_traveling(user, now):
        raise FlaamError("travel_not_active", 400, lang)
    if user.travel_extension_used:
        raise FlaamError("travel_extension_used", 409, lang)

    new_until = (user.travel_until or now) + timedelta(days=EXTENSION_DAYS)
    started = user.travel_started_at or now
    if (new_until - started) > timedelta(days=30):
        raise FlaamError("travel_max_duration_reached", 409, lang)

    user.travel_until = new_until
    user.travel_extension_used = True
    await db.commit()
    await db.refresh(user)
    return await get_status(user, db, lang)


async def deactivate(
    user: User, db: AsyncSession, lang: str = "fr"
) -> TravelStatusResponse:
    """Termine le mode voyage manuellement.

    Le compteur d'activations 30j n'est PAS reset (anti-abuse). La
    désactivation libère seulement le slot "voyage actif".
    """
    now = datetime.now(timezone.utc)
    if not is_traveling(user, now):
        raise FlaamError("travel_not_active", 400, lang)

    user.travel_city_id = None
    user.travel_started_at = None
    user.travel_until = None
    user.travel_extension_used = False
    user.travel_gps_confirmed_at = None
    await db.commit()
    await db.refresh(user)
    return await get_status(user, db, lang)


async def expire_due_travels(db: AsyncSession) -> int:
    """Tâche Celery périodique : nettoie les voyages expirés.

    Le compteur d'activations 30j n'est PAS touché (rolling window).
    Retourne le nombre d'users mis à jour.
    """
    now = datetime.now(timezone.utc)
    rows = await db.execute(
        select(User).where(
            User.travel_city_id.isnot(None),
            User.travel_until.isnot(None),
            User.travel_until <= now,
        )
    )
    users = rows.scalars().all()
    for u in users:
        u.travel_city_id = None
        u.travel_started_at = None
        u.travel_until = None
        u.travel_extension_used = False
        u.travel_gps_confirmed_at = None
    if users:
        await db.commit()
    return len(users)


# ── Home city change ────────────────────────────────────────────────


async def change_home_city(
    user: User, city_id: UUID, db: AsyncSession, lang: str = "fr"
) -> CityChangeResponse:
    """Change la ville principale. Cooldown 30 jours.

    NB: pas de check "travel actif" ici — l'user peut changer sa ville
    principale même en voyage (cas "déménagement").
    """
    now = datetime.now(timezone.utc)
    if user.city_changed_at is not None:
        next_allowed = user.city_changed_at + timedelta(
            days=HOME_CITY_COOLDOWN_DAYS,
        )
        if next_allowed > now:
            raise FlaamError(
                "city_change_cooldown",
                429,
                lang,
                next_date=next_allowed.strftime("%d/%m/%Y"),
            )

    city = await db.get(City, city_id)
    if city is None:
        raise FlaamError("city_not_found", 404, lang)
    if city.phase not in ("launch", "growth", "stable"):
        raise FlaamError("city_not_available", 400, lang)

    user.city_id = city_id
    user.city_changed_at = now
    await db.commit()
    await db.refresh(user)

    return CityChangeResponse(
        city_id=city_id,
        city_changed_at=now,
        next_change_allowed_at=now + timedelta(days=HOME_CITY_COOLDOWN_DAYS),
    )
