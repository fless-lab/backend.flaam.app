from __future__ import annotations

"""
Emergency timer task (§S12, §S12.5, safety §5.11).

send_emergency_sms : toutes les minutes.
- SCAN safety:timer:* dans Redis.
- Pour chaque clé, lit le JSON et fait DEUX vérifications :
    (1) now >= expires_at → envoie un SMS d'alerte à TOUS les
        contacts du timer, supprime la clé.
    (2) sinon, si 0 < (expires_at - now) <= 15 min → envoie un push
        de pré-expiration au user (une seule fois, via un flag
        `safety:timer:warned:{uid}` SET NX EX 1800).

Le TTL Redis côté safety_service inclut une grâce de 24h pour que le
task (qui tourne toutes les minutes) puisse attraper les timers
logiquement expirés avant disparition physique de la clé.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.db.redis import redis_pool
from app.db.session import async_session
from app.services import notification_service, safety_service
from app.utils.sms import sms_service

log = structlog.get_logger()


_SCAN_MATCH = "safety:timer:*"
_SCAN_BATCH = 50
_PRE_EXPIRY_WINDOW = timedelta(minutes=15)


def _format_location_info(data: dict, now: datetime) -> str:
    lat = data.get("latitude")
    lng = data.get("longitude")
    if lat is None or lng is None:
        return ""
    info = (
        f"\nDerniere position : "
        f"https://maps.google.com/maps?q={lat},{lng}"
    )
    updated_raw = data.get("location_updated_at")
    if updated_raw:
        try:
            updated = datetime.fromisoformat(updated_raw)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            delta = now - updated
            minutes = max(int(delta.total_seconds() / 60), 0)
            if minutes < 60:
                info += f" (il y a {minutes} min)"
            else:
                hours = minutes // 60
                info += f" (il y a {hours}h)"
        except ValueError:
            pass
    return info


def _format_alert_sms(
    data: dict, now: datetime, partner_name: str | None = None
) -> str:
    user_name = data.get("user_name") or "Ton contact Flaam"
    meeting_place = data.get("meeting_place") or "un lieu non precise"
    location = _format_location_info(data, now)
    partner_line = ""
    if partner_name:
        partner_line = f"\nElle/il devait rencontrer {partner_name}."
    return (
        f"ALERTE FLAAM : {user_name} avait un rendez-vous a "
        f"{meeting_place} et n'a pas annule son timer de securite. "
        f"Verifie que tout va bien.{location}{partner_line}"
        f"\nPour signaler : https://flaam.app/safety/contact"
    )


def _mask_phone(phone: str) -> str:
    """N'expose que les 4 derniers chiffres dans les logs."""
    if not phone or len(phone) < 4:
        return "****"
    return f"****{phone[-4:]}"


# Filtre les clés de flag `safety:timer:warned:*` qui matchent aussi
# le pattern `safety:timer:*` du SCAN.
def _is_timer_key(key: str) -> bool:
    return ":warned:" not in key


async def _handle_expired(
    redis: aioredis.Redis,
    key: str,
    data: dict,
    now: datetime,
    db: AsyncSession,
) -> tuple[int, int]:
    """
    Envoie les SMS aux contacts, puis supprime la clé + clôt la row
    EmergencySession (SAFETY-6).

    Retourne (sent, errors).
    """
    contacts = data.get("contacts") or []
    if not contacts:
        # Timer sans contact : legacy ou corrompu.
        log.warning("emergency_timer_no_contact", key=str(key))
        await redis.delete(key)
        return (0, 1)

    text = _format_alert_sms(data, now)
    sent = 0
    errors = 0
    failure = False
    for c in contacts:
        phone = c.get("phone")
        if not phone:
            errors += 1
            continue
        try:
            await sms_service.send_text(phone, text, channel="whatsapp")
            sent += 1
            log.warning(
                "emergency_sms_sent",
                user_id=data.get("user_id"),
                contact=_mask_phone(phone),
            )
        except Exception as exc:  # noqa: BLE001
            failure = True
            log.error(
                "emergency_sms_failed",
                user_id=data.get("user_id"),
                error=str(exc),
            )
            errors += 1

    if not failure:
        # Ne supprime la clé QUE si tous les SMS sont partis sans erreur
        # réseau/provider. Sinon le task retry à la prochaine tick.
        await redis.delete(key)

        # SAFETY-6 : clôt la row EmergencySession côté BD.
        from uuid import UUID

        user_id_raw = data.get("user_id")
        if user_id_raw:
            try:
                await safety_service.mark_session_expired(
                    user_id=UUID(user_id_raw), db=db
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "emergency_session_close_failed",
                    user_id=user_id_raw,
                    error=str(exc),
                )
    return (sent, errors)


async def _handle_pre_expiry(
    redis: aioredis.Redis,
    data: dict,
    user_id: str,
    db: AsyncSession,
) -> bool:
    """
    Push notification 15 min avant expiration — une seule fois.

    Utilise `safety:timer:warned:{uid}` SET NX EX 1800 pour déduper.
    Retourne True si la notif a été envoyée.
    """
    warn_key = safety_service.TIMER_WARNED_KEY.format(user_id=user_id)
    # NX → n'écrit que si absent. EX 1800 → 30 min de fenêtre (au-delà
    # la clé safety:timer:* aura disparu de toute façon).
    acquired = await redis.set(warn_key, "1", nx=True, ex=1800)
    if not acquired:
        return False

    try:
        from uuid import UUID

        await notification_service.send_push(
            UUID(user_id),
            type="notif_safety_alert_15min",
            data={},
            db=db,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("pre_expiry_push_failed", user_id=user_id, error=str(exc))
        # On laisse le flag posé : pas de re-tentative (on évite le
        # bruit sur le user si FCM est down).
    return True


async def _send_emergency_sms_async(
    db: AsyncSession,
    redis: aioredis.Redis,
    *,
    now: datetime | None = None,
) -> dict:
    """
    Scan Redis, envoie SMS pour les timers expirés + push 15 min avant.

    Retourne : {"scanned", "sent", "errors", "warned"}.
    """
    now_ = now or datetime.now(timezone.utc)
    cursor = 0
    scanned = 0
    sent = 0
    errors = 0
    warned = 0

    while True:
        cursor, keys = await redis.scan(
            cursor, match=_SCAN_MATCH, count=_SCAN_BATCH
        )
        for key in keys:
            # Le pattern safety:timer:* capture aussi les flags
            # safety:timer:warned:{uid} — on les ignore.
            if not _is_timer_key(key):
                continue
            scanned += 1
            raw = await redis.get(key)
            if raw is None:
                continue
            try:
                data = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                log.warning("emergency_timer_corrupted", key=str(key))
                await redis.delete(key)
                errors += 1
                continue

            exp_raw = data.get("expires_at_utc") or data.get("expires_at")
            if not exp_raw:
                log.warning("emergency_timer_no_expiry", key=str(key))
                await redis.delete(key)
                errors += 1
                continue

            try:
                expires_at = datetime.fromisoformat(exp_raw)
            except ValueError:
                log.warning("emergency_timer_bad_expiry", key=str(key))
                await redis.delete(key)
                errors += 1
                continue
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if expires_at <= now_:
                s, e = await _handle_expired(redis, key, data, now_, db)
                sent += s
                errors += e
                continue

            # Pas encore expiré — check fenêtre pré-expiration 15 min.
            if expires_at - now_ <= _PRE_EXPIRY_WINDOW:
                user_id = data.get("user_id")
                if user_id:
                    if await _handle_pre_expiry(redis, data, user_id, db):
                        warned += 1

        if cursor == 0:
            break

    return {
        "scanned": scanned,
        "sent": sent,
        "errors": errors,
        "warned": warned,
    }


@celery_app.task(name="app.tasks.emergency_tasks.send_emergency_sms")
def send_emergency_sms() -> dict:
    async def _run():
        # FastAPI's lifespan (main.py) initialises redis_pool, but the Celery
        # worker runs in a separate process that never boots FastAPI, so we
        # lazily initialise the pool on the first task invocation. The pool
        # is idempotent (RedisPool.initialize just sets the attribute).
        if redis_pool._pool is None:  # noqa: SLF001 — intentional private use
            await redis_pool.initialize()
        async with async_session() as db:
            return await _send_emergency_sms_async(db, redis_pool.client)

    return asyncio.run(_run())


# ══════════════════════════════════════════════════════════════════════
# Scheduled timers
# ══════════════════════════════════════════════════════════════════════


async def _activate_scheduled_timers_async(db, redis) -> dict:
    """
    Active les timers scheduled dont scheduled_for <= now.

    Pour chaque timer prêt :
      - Re-calcule started_at = now, expires_at = now + hours
      - Set la clé Redis safety:timer:{user_id}
      - Push notif_timer_started
      - Clear scheduled_for (timer devient un "vrai" actif normal)

    Retourne {"activated": int}.
    """
    import json
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select

    from app.models.emergency_session import EmergencySession
    from app.models.user import User as _User
    from app.services import notification_service
    from app.services.safety_service import (
        TIMER_KEY,
        TIMER_GRACE_SECONDS,
        TIMER_WARNED_KEY,
        _user_display_name,
    )

    now = datetime.now(timezone.utc)
    rows = await db.execute(
        select(EmergencySession).where(
            EmergencySession.scheduled_for.isnot(None),
            EmergencySession.scheduled_for <= now,
            EmergencySession.ended_at.is_(None),
        ),
    )
    sessions = list(rows.scalars())
    activated = 0

    for s in sessions:
        # Si l'user a déjà un timer actif → on skip (pas d'overlap).
        # Le scheduled reste en attente jusqu'à ce que l'autre se termine
        # (au prochain run de la task, on retentera).
        existing = await redis.get(TIMER_KEY.format(user_id=str(s.user_id)))
        if existing is not None:
            continue

        timer_seconds = int(s.hours * 3600)
        new_started = now
        new_expires = datetime.fromtimestamp(
            now.timestamp() + timer_seconds, tz=timezone.utc,
        )

        # Charge user pour le payload Redis (display_name).
        user_row = await db.execute(
            select(_User).where(_User.id == s.user_id),
        )
        user = user_row.scalar_one_or_none()
        if user is None or user.is_deleted or user.is_banned:
            # User inactif → annule le scheduled.
            s.ended_at = now
            s.end_reason = "user_unavailable"
            continue

        s.started_at = new_started
        s.expires_at = new_expires
        s.scheduled_for = None  # Bascule en actif "normal"

        payload = {
            "user_id": str(s.user_id),
            "user_name": _user_display_name(user),
            "contacts": s.contacts_snapshot,
            "started_at": new_started.isoformat(),
            "expires_at_utc": new_expires.isoformat(),
            "latitude": s.latitude,
            "longitude": s.longitude,
            "location_updated_at": None,
            "meeting_place": s.meeting_place,
            "session_id": str(s.id),
            "partner_user_id": (
                str(s.partner_user_id) if s.partner_user_id else None
            ),
        }
        await redis.set(
            TIMER_KEY.format(user_id=str(s.user_id)),
            json.dumps(payload),
            ex=timer_seconds + TIMER_GRACE_SECONDS,
        )
        await redis.delete(TIMER_WARNED_KEY.format(user_id=str(s.user_id)))

        # Push "ton timer a démarré"
        await notification_service.send_push(
            s.user_id, type="notif_timer_started", db=db,
        )
        activated += 1

    await db.commit()
    log.info("scheduled_timers_activated", count=activated)
    return {"activated": activated}


async def _warn_scheduled_timers_30min_async(db, redis) -> dict:
    """Push T-30 min avant l'activation pour donner une chance d'annuler."""
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select

    from app.models.emergency_session import EmergencySession
    from app.services import notification_service

    now = datetime.now(timezone.utc)
    window_start = now + timedelta(minutes=25)
    window_end = now + timedelta(minutes=35)

    rows = await db.execute(
        select(EmergencySession).where(
            EmergencySession.scheduled_for.isnot(None),
            EmergencySession.scheduled_for >= window_start,
            EmergencySession.scheduled_for <= window_end,
            EmergencySession.ended_at.is_(None),
        ),
    )
    sessions = list(rows.scalars())
    warned = 0

    for s in sessions:
        # Idempotence : flag Redis 1 push par session.
        flag_key = f"safety:timer:warn30:{s.id}"
        if await redis.exists(flag_key):
            continue
        res = await notification_service.send_push(
            s.user_id, type="notif_timer_starting_30min", db=db,
        )
        if res.get("sent"):
            await redis.set(flag_key, "1", ex=3600)
            warned += 1

    log.info("scheduled_timers_warned", count=warned)
    return {"warned": warned}


@celery_app.task(name="app.tasks.emergency_tasks.activate_scheduled_timers")
def activate_scheduled_timers() -> dict:
    async def _run():
        if redis_pool._pool is None:  # noqa: SLF001
            await redis_pool.initialize()
        async with async_session() as db:
            return await _activate_scheduled_timers_async(
                db, redis_pool.client,
            )
    return asyncio.run(_run())


@celery_app.task(name="app.tasks.emergency_tasks.warn_scheduled_timers_30min")
def warn_scheduled_timers_30min() -> dict:
    async def _run():
        if redis_pool._pool is None:  # noqa: SLF001
            await redis_pool.initialize()
        async with async_session() as db:
            return await _warn_scheduled_timers_30min_async(
                db, redis_pool.client,
            )
    return asyncio.run(_run())


__all__ = [
    "_send_emergency_sms_async",
    "send_emergency_sms",
    "_activate_scheduled_timers_async",
    "activate_scheduled_timers",
    "_warn_scheduled_timers_30min_async",
    "warn_scheduled_timers_30min",
]
