from __future__ import annotations

"""
Event pre-registration (MàJ 8 — Porte 3).

Flux :
1. POST /auth/event-preregister : envoie OTP WhatsApp (rate-limited
   via auth_service).
2. POST /auth/event-preregister/verify : vérifie OTP
   - Si nouveau numéro → crée un ghost user (onboarding_step="ghost",
     is_active=false, is_visible=false) + EventRegistration + QR HMAC
   - Si ghost user existant → ajoute une nouvelle EventRegistration
   - Si user Flaam existant → associe l'event à son compte
3. Le QR est présenté à l'entrée de l'event et scanné via
   POST /events/{event_id}/checkin.
4. Quand le ghost télécharge l'app et fait l'OTP classique, le numéro
   est reconnu et verify_otp retourne is_ghost_conversion=true avec
   les données pré-remplies (voir auth_service).
"""

from datetime import datetime, timezone
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import FlaamError
from app.core.exceptions import AppException
from app.core.security import qr_code_hash, sign_event_qr
from app.models.event import Event
from app.models.event_registration import EventRegistration
from app.models.user import User
from app.services import auth_service, event_service
from app.utils.phone import (
    InvalidPhoneError,
    country_code_from_phone,
    hash_phone,
    normalize_phone,
)

log = structlog.get_logger()
settings = get_settings()


async def _load_event_for_preregister(
    event_id: UUID, db: AsyncSession
) -> Event:
    ev = await db.get(Event, event_id)
    if ev is None or not ev.is_active:
        raise AppException(status.HTTP_404_NOT_FOUND, "event_not_found")
    if ev.status not in ("published", "full"):
        raise AppException(status.HTTP_404_NOT_FOUND, "event_not_found")
    return ev


async def request_preregister_otp(
    phone: str, event_id: UUID, db: AsyncSession, redis: aioredis.Redis
) -> dict:
    """Envoie l'OTP (WhatsApp par défaut) et retourne le nom de l'event."""
    ev = await _load_event_for_preregister(event_id, db)

    # Déclenche l'OTP via le flow auth classique (rate-limit partagé)
    otp_result = await auth_service.request_otp(phone, redis, channel="whatsapp")

    return {
        "otp_sent": True,
        "channel": otp_result["channel"],
        "event_name": ev.title,
        "expires_in": otp_result["expires_in"],
    }


async def verify_preregister_otp(
    *,
    phone: str,
    code: str,
    event_id: UUID,
    first_name: str,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> dict:
    """
    Vérifie l'OTP, crée ou associe le user, génère le QR.
    Ne retourne PAS de JWT (la personne n'a pas l'app).
    """
    try:
        normalized = normalize_phone(phone)
    except InvalidPhoneError as e:
        raise AppException(status.HTTP_400_BAD_REQUEST, str(e))

    ev = await _load_event_for_preregister(event_id, db)
    if ev.max_attendees is not None and ev.current_attendees >= ev.max_attendees:
        raise AppException(status.HTTP_409_CONFLICT, "event_full")

    phash = hash_phone(normalized)

    # Vérification OTP (même pattern que auth_service.verify_otp mais
    # sans création de session / device).
    stored = await redis.get(f"otp:{phash}")
    if stored is None:
        raise FlaamError("otp_expired", 401)
    attempts = await redis.incr(f"otp:attempts:{phash}")
    await redis.expire(f"otp:attempts:{phash}", settings.otp_expire_seconds)
    if attempts > settings.otp_max_attempts:
        await redis.delete(f"otp:{phash}")
        raise FlaamError("otp_max_attempts", 429)
    if stored != code:
        remaining = max(0, settings.otp_max_attempts - attempts)
        raise FlaamError("otp_invalid", 401, remaining=remaining)
    await redis.delete(f"otp:{phash}")
    await redis.delete(f"otp:attempts:{phash}")

    # User existant ?
    res = await db.execute(select(User).where(User.phone_hash == phash))
    user = res.scalar_one_or_none()

    is_existing_completed = (
        user is not None
        and user.onboarding_step not in ("ghost", "pre_registered")
    )

    if user is None:
        # Cas 1 : nouveau → ghost user
        user = User(
            phone_hash=phash,
            phone_country_code=country_code_from_phone(normalized),
            is_phone_verified=True,
            first_name=first_name.strip(),
            onboarding_step="ghost",
            onboarding_source="event",
            source_event_id=event_id,
            is_active=False,
            is_visible=False,
        )
        db.add(user)
        await db.flush()

    # Créer l'inscription si pas déjà présente
    existing_reg = (
        await db.execute(
            select(EventRegistration).where(
                EventRegistration.event_id == event_id,
                EventRegistration.user_id == user.id,
            )
        )
    ).scalar_one_or_none()

    qr_token = sign_event_qr(event_id, user.id)
    qhash = qr_code_hash(qr_token)

    if existing_reg is None:
        reg = EventRegistration(
            event_id=event_id,
            user_id=user.id,
            status="registered",
            registered_via="web",
            qr_code_hash=qhash,
            suggested_tags=event_service._category_tags(ev.category),
        )
        db.add(reg)
        ev.current_attendees += 1
        if (
            ev.max_attendees is not None
            and ev.current_attendees >= ev.max_attendees
        ):
            ev.status = "full"

    await db.commit()

    log.info(
        "event_preregister_verified",
        event_id=str(event_id),
        user_id=str(user.id),
        is_ghost=not is_existing_completed,
    )

    status_str = "existing_user" if is_existing_completed else "registered"
    message = (
        f"Tu es inscrit(e) à {ev.title} ! Présente ce QR code à l'entrée."
    )
    if is_existing_completed:
        message = f"Tu es déjà sur Flaam. Tu es inscrit(e) à {ev.title}."

    return {
        "status": status_str,
        "qr_code": qr_token,
        "qr_code_url": (
            f"{settings.frontend_base_url}/qr/{qr_token}"
            if not is_existing_completed
            else None
        ),
        "event_name": ev.title,
        "event_date": ev.starts_at,
        "message": message,
    }


async def build_ghost_conversion_payload(
    user: User, db: AsyncSession
) -> dict | None:
    """
    Si le user est un ghost/pre_registered, construit le payload
    `ghost_data` retourné par verify_otp pour que l'app pré-remplisse
    l'onboarding.
    """
    if user.onboarding_step not in ("ghost", "pre_registered"):
        return None

    event_id = user.source_event_id
    if event_id is None:
        # Ghost sans event connu — on renvoie juste le first_name.
        return {
            "first_name": user.first_name,
            "onboarding_source": "event",
            "event_name": None,
            "event_spot_id": None,
            "suggested_tags": [],
            "attendees_completed": 0,
        }

    ev = await db.get(Event, event_id)
    if ev is None:
        return {
            "first_name": user.first_name,
            "onboarding_source": "event",
            "event_name": None,
            "event_spot_id": None,
            "suggested_tags": [],
            "attendees_completed": 0,
        }

    # Nombre d'inscrits ayant complété leur profil (pour le message
    # motivant dans l'app).
    from sqlalchemy import func

    completed_row = await db.execute(
        select(func.count())
        .select_from(EventRegistration)
        .join(User, User.id == EventRegistration.user_id)
        .where(
            EventRegistration.event_id == event_id,
            User.onboarding_step == "completed",
        )
    )
    attendees_completed = int(completed_row.scalar_one() or 0)

    return {
        "first_name": user.first_name,
        "onboarding_source": "event",
        "event_name": ev.title,
        "event_spot_id": str(ev.spot_id),
        "suggested_tags": event_service._category_tags(ev.category),
        "attendees_completed": attendees_completed,
    }


async def promote_ghost_on_conversion(
    user: User, db: AsyncSession
) -> None:
    """
    Appelé quand un ghost user fait son OTP depuis l'app.
    Transitionne ghost/pre_registered → city_selection et active le compte.
    Ne commit pas — le caller (auth_service.verify_otp) commit.
    """
    if user.onboarding_step not in ("ghost", "pre_registered"):
        return
    user.onboarding_step = "city_selection"
    user.is_active = True
    # is_visible reste False jusqu'à la fin de l'onboarding (pattern
    # existant : on passe à is_visible=True quand l'onboarding est
    # completed et la selfie vérifiée).


__all__ = [
    "request_preregister_otp",
    "verify_preregister_otp",
    "build_ghost_conversion_payload",
    "promote_ghost_on_conversion",
]
