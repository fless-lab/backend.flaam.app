from __future__ import annotations

"""
Safety service (spec §5.11, §18, §30, S12.5).

Expose :
- report_user()           : crée Report + appelle scam_detection
- block_user()            : Block bidirectionnel + update AccountHistory
- unblock_user()          : retire le Block (ne désarme PAS les blocked_by_hashes)
- share_date()            : SMS (ou WhatsApp) via sms_service

Emergency contacts (S12.5) :
- list_emergency_contacts / create_emergency_contact / update_emergency_contact
- delete_emergency_contact / set_primary_contact

Emergency timer :
- start_emergency_timer()  : Redis key, TTL = hours*3600 + 86400 grâce
- update_timer_location()  : patch lat/lng en conservant le TTL
- extend_timer()           : décale expires_at + réarme la notif 15min
- cancel_emergency_timer() : DEL de la key
- trigger_panic()          : alerte immédiate (avec ou sans timer actif)

Timer d'urgence — pattern de stockage :

Le task Celery `emergency_tasks.send_emergency_sms` tourne toutes les
minutes et SCAN `safety:timer:*`. Il doit pouvoir lire la clé APRÈS
que le timer logique ait expiré, sinon il ne verra jamais rien (Redis
TTL = disparition immédiate de la clé).

Donc le TTL Redis = `hours*3600 + 86400` (24h de grâce). Le task
compare `expires_at_utc` au wall-clock pour décider d'envoyer le SMS,
puis supprime la clé.

Le JSON stocké est enrichi (S12.5) :
    {
      "contacts": [{"phone": "+228...", "name": "..."}, ...],  # 1 ou 2
      "user_name": "Ama",
      "user_id": "uuid",
      "meeting_place": "Café 21",
      "latitude": 6.17, "longitude": -1.23,
      "location_updated_at": "2026-04-17T19:50:00+00:00",
      "expires_at_utc": "2026-04-17T22:50:00+00:00",
      "started_at": "2026-04-17T19:50:00+00:00"
    }
"""

import json
from datetime import datetime, timezone
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import FlaamError
from app.core.exceptions import AppException
from app.models.account_history import AccountHistory
from app.models.block import Block
from app.models.emergency_contact import EmergencyContact
from app.models.report import Report
from app.models.user import User
from app.services import scam_detection_service
from app.utils.sms import sms_service

log = structlog.get_logger()


# ── Redis keys ────────────────────────────────────────────────────────

TIMER_KEY = "safety:timer:{user_id}"
TIMER_WARNED_KEY = "safety:timer:warned:{user_id}"
# Grâce supplémentaire au-delà du timer logique pour que le task Celery
# puisse encore lire la clé expirée (task tourne toutes les minutes).
TIMER_GRACE_SECONDS = 24 * 3600

# Bornes métier
MIN_HOURS = 0.5
MAX_HOURS = 12
MAX_CONTACTS_STORED = 3
MAX_CONTACTS_PER_TIMER = 2


# ══════════════════════════════════════════════════════════════════════
# Report
# ══════════════════════════════════════════════════════════════════════


async def report_user(
    *,
    reporter: User,
    reported_user_id: UUID,
    reason: str,
    description: str | None,
    evidence_message_ids: list[UUID] | None,
    db: AsyncSession,
    lang: str = "fr",
) -> Report:
    """Crée un Report + déclenche scam_detection sur le reported (synchrone)."""
    if reported_user_id == reporter.id:
        raise AppException(
            status.HTTP_400_BAD_REQUEST, "cannot_report_self"
        )

    reported = await db.get(User, reported_user_id)
    if reported is None:
        raise FlaamError("user_not_found", 404, lang)

    report = Report(
        reporter_id=reporter.id,
        reported_user_id=reported_user_id,
        reason=reason,
        description=description,
        evidence_message_ids=(
            [str(mid) for mid in evidence_message_ids]
            if evidence_message_ids
            else None
        ),
        status="pending",
    )
    db.add(report)
    await db.flush()

    # Scam detection synchrone — appelée à chaque report reçu (§39).
    risk = await scam_detection_service.compute_scam_risk(
        reported_user_id, db
    )
    if risk > scam_detection_service.AUTO_BAN_THRESHOLD:
        reported.is_banned = True
        reported.ban_reason = f"auto_ban_scam_risk:{risk:.2f}"
        report.status = "auto_banned"
        log.warning(
            "scam_auto_ban",
            user_id=str(reported_user_id),
            risk=risk,
        )
    elif risk > scam_detection_service.REVIEW_THRESHOLD:
        report.status = "flagged_for_review"

    await db.commit()
    await db.refresh(report)
    return report


# ══════════════════════════════════════════════════════════════════════
# Block / Unblock
# ══════════════════════════════════════════════════════════════════════


async def block_user(
    *,
    blocker: User,
    blocked_user_id: UUID,
    db: AsyncSession,
    lang: str = "fr",
) -> Block:
    """
    Crée un Block (idempotent) + met à jour l'AccountHistory du bloqué.

    Le filtre matching exclut déjà les blocks bidirectionnels
    (app/services/matching_engine/hard_filters.py). L'effet est donc
    immédiat au prochain calcul de feed.
    """
    if blocked_user_id == blocker.id:
        raise FlaamError("cannot_block_self", 400, lang)

    blocked = await db.get(User, blocked_user_id)
    if blocked is None:
        raise FlaamError("user_not_found", 404, lang)

    existing = await db.execute(
        select(Block).where(
            Block.blocker_id == blocker.id,
            Block.blocked_id == blocked_user_id,
        )
    )
    block = existing.scalar_one_or_none()
    if block is None:
        block = Block(blocker_id=blocker.id, blocked_id=blocked_user_id)
        db.add(block)

    history_row = await db.execute(
        select(AccountHistory).where(
            AccountHistory.phone_hash == blocked.phone_hash
        )
    )
    history = history_row.scalar_one_or_none()
    if history is None:
        history = AccountHistory(
            phone_hash=blocked.phone_hash,
            device_fingerprints=[],
            total_accounts_created=blocked.account_created_count or 1,
            first_account_created_at=blocked.created_at,
            blocked_by_hashes=[blocker.phone_hash],
            blocked_by_count=1,
        )
        db.add(history)
    else:
        if blocker.phone_hash not in (history.blocked_by_hashes or []):
            history.blocked_by_hashes = [
                *(history.blocked_by_hashes or []),
                blocker.phone_hash,
            ]
            history.blocked_by_count = (history.blocked_by_count or 0) + 1

    await db.commit()
    await db.refresh(block)
    return block


async def unblock_user(
    *,
    blocker: User,
    blocked_user_id: UUID,
    db: AsyncSession,
) -> bool:
    """
    Retire le Block. Ne décrémente PAS blocked_by_count de l'historique :
    la spec §30.7 conserve les blocks survivants à la suppression.
    """
    result = await db.execute(
        select(Block).where(
            Block.blocker_id == blocker.id,
            Block.blocked_id == blocked_user_id,
        )
    )
    block = result.scalar_one_or_none()
    if block is None:
        return False
    await db.delete(block)
    await db.commit()
    return True


# ══════════════════════════════════════════════════════════════════════
# Share date (SMS/WhatsApp au contact de confiance)
# ══════════════════════════════════════════════════════════════════════


def _format_share_date_message(
    *,
    user_name: str,
    partner_name: str,
    meeting_place: str,
    meeting_time: datetime,
) -> str:
    when = meeting_time.strftime("%d/%m/%Y à %Hh%M")
    return (
        f"[Flaam] {user_name} a un rendez-vous avec {partner_name} "
        f"le {when} à {meeting_place}. "
        f"Message automatique de sécurité."
    )


async def share_date(
    *,
    user: User,
    contact_phone: str,
    contact_name: str | None,
    partner_name: str,
    meeting_place: str,
    meeting_time: datetime,
) -> dict:
    display_name = _user_display_name(user)
    text = _format_share_date_message(
        user_name=display_name,
        partner_name=partner_name,
        meeting_place=meeting_place,
        meeting_time=meeting_time,
    )
    result = await sms_service.send_text(
        contact_phone, text, channel="whatsapp"
    )
    log.info(
        "share_date_sent",
        user_id=str(user.id),
        contact_name=contact_name,
        provider=result.get("provider"),
    )
    return result


# ══════════════════════════════════════════════════════════════════════
# Emergency contacts (CRUD)
# ══════════════════════════════════════════════════════════════════════


async def list_emergency_contacts(
    *, user: User, db: AsyncSession
) -> list[EmergencyContact]:
    result = await db.execute(
        select(EmergencyContact)
        .where(EmergencyContact.user_id == user.id)
        .order_by(EmergencyContact.created_at)
    )
    return list(result.scalars().all())


async def create_emergency_contact(
    *,
    user: User,
    name: str,
    phone: str,
    db: AsyncSession,
    lang: str = "fr",
) -> EmergencyContact:
    existing = await list_emergency_contacts(user=user, db=db)
    if len(existing) >= MAX_CONTACTS_STORED:
        raise FlaamError("max_contacts_reached", 400, lang)

    is_primary = len(existing) == 0  # le premier contact est auto-primary
    contact = EmergencyContact(
        user_id=user.id,
        name=name,
        phone=phone,
        is_primary=is_primary,
    )
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return contact


async def update_emergency_contact(
    *,
    user: User,
    contact_id: UUID,
    name: str | None,
    phone: str | None,
    db: AsyncSession,
    lang: str = "fr",
) -> EmergencyContact:
    contact = await _get_owned_contact(user, contact_id, db, lang)
    if name is not None:
        contact.name = name
    if phone is not None:
        contact.phone = phone
    await db.commit()
    await db.refresh(contact)
    return contact


async def delete_emergency_contact(
    *,
    user: User,
    contact_id: UUID,
    db: AsyncSession,
    lang: str = "fr",
) -> None:
    contact = await _get_owned_contact(user, contact_id, db, lang)
    was_primary = contact.is_primary
    await db.delete(contact)
    await db.flush()

    if was_primary:
        # Reassigne le primary au plus ancien contact restant.
        remaining = await list_emergency_contacts(user=user, db=db)
        if remaining:
            remaining[0].is_primary = True
    await db.commit()


async def set_primary_contact(
    *,
    user: User,
    contact_id: UUID,
    db: AsyncSession,
    lang: str = "fr",
) -> EmergencyContact:
    contact = await _get_owned_contact(user, contact_id, db, lang)
    all_contacts = await list_emergency_contacts(user=user, db=db)
    for c in all_contacts:
        c.is_primary = (c.id == contact.id)
    await db.commit()
    await db.refresh(contact)
    return contact


async def _get_owned_contact(
    user: User, contact_id: UUID, db: AsyncSession, lang: str
) -> EmergencyContact:
    contact = await db.get(EmergencyContact, contact_id)
    if contact is None or contact.user_id != user.id:
        raise FlaamError("contact_not_found", 404, lang)
    return contact


# ══════════════════════════════════════════════════════════════════════
# Emergency timer
# ══════════════════════════════════════════════════════════════════════


def _user_display_name(user: User) -> str:
    """Même heuristique que share_date — first_name > display_name > fallback."""
    return (
        user.first_name
        or (user.profile.display_name if user.profile else None)
        or "Ton contact Flaam"
    )


def _validate_hours(hours: float, lang: str) -> None:
    if hours < MIN_HOURS:
        raise FlaamError("timer_too_short", 400, lang)
    if hours > MAX_HOURS:
        raise FlaamError("timer_too_long", 400, lang)


async def _resolve_contacts(
    *,
    user: User,
    contact_ids: list[UUID] | None,
    contact_phone: str | None,
    contact_name: str | None,
    db: AsyncSession,
    lang: str,
) -> list[dict]:
    """
    Résout la liste de contacts à copier dans Redis.

    Priorité :
      1) contact_ids (nouveau mode S12.5) — lire en BD, copier {phone,name}
      2) contact_phone ad-hoc (fallback rétro compat 1 seul contact)
    Max 2 contacts par timer. Au moins 1 requis.
    """
    if contact_ids:
        if len(contact_ids) > MAX_CONTACTS_PER_TIMER:
            raise FlaamError("max_2_contacts_per_timer", 400, lang)
        result = await db.execute(
            select(EmergencyContact).where(
                EmergencyContact.user_id == user.id,
                EmergencyContact.id.in_(contact_ids),
            )
        )
        rows = list(result.scalars().all())
        if len(rows) != len(contact_ids):
            # Au moins un UUID n'appartient pas au user.
            raise FlaamError("contact_not_found", 404, lang)
        return [{"phone": c.phone, "name": c.name} for c in rows]

    if contact_phone:
        return [{"phone": contact_phone, "name": contact_name}]

    raise FlaamError("contact_required", 400, lang)


async def start_emergency_timer(
    *,
    user: User,
    hours: float,
    contact_ids: list[UUID] | None,
    contact_phone: str | None,
    contact_name: str | None,
    latitude: float | None,
    longitude: float | None,
    meeting_place: str | None,
    db: AsyncSession,
    redis: aioredis.Redis,
    lang: str = "fr",
) -> datetime:
    """
    Arme le timer d'urgence — stocke l'état en Redis.

    TTL = hours*3600 + TIMER_GRACE_SECONDS. Le task Celery lit
    `expires_at_utc` pour décider d'envoyer le SMS d'alerte. Sans la
    grâce, la clé disparaîtrait avant qu'un task 1-minute puisse la
    voir expirer.
    """
    _validate_hours(hours, lang)
    contacts = await _resolve_contacts(
        user=user,
        contact_ids=contact_ids,
        contact_phone=contact_phone,
        contact_name=contact_name,
        db=db,
        lang=lang,
    )

    timer_seconds = int(hours * 3600)
    now = datetime.now(timezone.utc)
    expires_at_utc = datetime.fromtimestamp(
        now.timestamp() + timer_seconds, tz=timezone.utc
    )
    payload = {
        "user_id": str(user.id),
        "user_name": _user_display_name(user),
        "contacts": contacts,
        "started_at": now.isoformat(),
        "expires_at_utc": expires_at_utc.isoformat(),
        "latitude": latitude,
        "longitude": longitude,
        "location_updated_at": now.isoformat() if latitude is not None else None,
        "meeting_place": meeting_place,
    }
    await redis.set(
        TIMER_KEY.format(user_id=str(user.id)),
        json.dumps(payload),
        ex=timer_seconds + TIMER_GRACE_SECONDS,
    )
    # Un nouveau timer efface tout ancien flag "warned" pour que la
    # notif 15 min soit réarmée.
    await redis.delete(TIMER_WARNED_KEY.format(user_id=str(user.id)))

    log.info(
        "emergency_timer_armed",
        user_id=str(user.id),
        expires_at=expires_at_utc.isoformat(),
        hours=hours,
        contacts_count=len(contacts),
    )
    return expires_at_utc


async def cancel_emergency_timer(
    *, user: User, redis: aioredis.Redis
) -> bool:
    uid = str(user.id)
    deleted = await redis.delete(TIMER_KEY.format(user_id=uid))
    await redis.delete(TIMER_WARNED_KEY.format(user_id=uid))
    cancelled = bool(deleted)
    log.info(
        "emergency_timer_cancel",
        user_id=uid,
        cancelled=cancelled,
    )
    return cancelled


async def update_timer_location(
    *,
    user: User,
    latitude: float,
    longitude: float,
    redis: aioredis.Redis,
    lang: str = "fr",
) -> None:
    """
    Met à jour la position GPS pendant que le timer est actif.

    Conserve le TTL restant exact (READ ttl → SET ex=ttl).
    """
    key = TIMER_KEY.format(user_id=str(user.id))
    raw = await redis.get(key)
    if raw is None:
        raise FlaamError("no_active_timer", 404, lang)
    ttl = await redis.ttl(key)
    if ttl <= 0:
        raise FlaamError("no_active_timer", 404, lang)

    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        raise FlaamError("no_active_timer", 404, lang)

    data["latitude"] = latitude
    data["longitude"] = longitude
    data["location_updated_at"] = datetime.now(timezone.utc).isoformat()
    await redis.set(key, json.dumps(data), ex=ttl)


async def extend_timer(
    *,
    user: User,
    extra_hours: float,
    redis: aioredis.Redis,
    lang: str = "fr",
) -> datetime:
    """
    Prolonge un timer actif de `extra_hours`.

    Recalcule expires_at_utc et le TTL Redis. Supprime le flag "warned"
    pour que la notification 15 min se re-déclenche avant la nouvelle
    expiration.
    """
    key = TIMER_KEY.format(user_id=str(user.id))
    raw = await redis.get(key)
    if raw is None:
        raise FlaamError("no_active_timer", 404, lang)
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        raise FlaamError("no_active_timer", 404, lang)

    exp_raw = data.get("expires_at_utc")
    if not exp_raw:
        raise FlaamError("no_active_timer", 404, lang)
    try:
        current_exp = datetime.fromisoformat(exp_raw)
    except ValueError:
        raise FlaamError("no_active_timer", 404, lang)
    if current_exp.tzinfo is None:
        current_exp = current_exp.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    new_exp = datetime.fromtimestamp(
        current_exp.timestamp() + extra_hours * 3600, tz=timezone.utc
    )
    data["expires_at_utc"] = new_exp.isoformat()

    new_ttl = int((new_exp - now).total_seconds()) + TIMER_GRACE_SECONDS
    if new_ttl <= 0:
        # timer déjà expiré logiquement — on refuse l'extension plutôt
        # que de ressusciter un timer mort.
        raise FlaamError("no_active_timer", 404, lang)

    await redis.set(key, json.dumps(data), ex=new_ttl)
    await redis.delete(TIMER_WARNED_KEY.format(user_id=str(user.id)))

    log.info(
        "emergency_timer_extended",
        user_id=str(user.id),
        extra_hours=extra_hours,
        new_expires_at=new_exp.isoformat(),
    )
    return new_exp


def _format_panic_sms(
    *,
    user_name: str,
    meeting_place: str | None,
    latitude: float | None,
    longitude: float | None,
) -> str:
    place = f" a {meeting_place}" if meeting_place else ""
    loc = ""
    if latitude is not None and longitude is not None:
        loc = (
            f"\nPosition : https://maps.google.com/maps?"
            f"q={latitude},{longitude}"
        )
    return (
        f"ALERTE URGENTE FLAAM : {user_name} a declenche une alerte "
        f"d'urgence{place}.{loc}\n"
        f"Contacte-la/le immediatement."
    )


async def trigger_panic(
    *,
    user: User,
    latitude: float | None,
    longitude: float | None,
    db: AsyncSession,
    redis: aioredis.Redis,
    lang: str = "fr",
) -> int:
    """
    Bouton panique : envoie un SMS d'alerte IMMÉDIAT.

    - Si un timer est actif : lit la liste des contacts depuis Redis,
      met à jour la position, envoie, puis supprime la clé (timer
      consommé).
    - Sinon : utilise le contact `is_primary=True` en BD. 400 si
      aucun contact enregistré.

    Retourne le nombre de contacts notifiés.
    """
    uid = str(user.id)
    key = TIMER_KEY.format(user_id=uid)
    raw = await redis.get(key)

    display_name = _user_display_name(user)

    if raw is not None:
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            data = None
        if data:
            contacts = data.get("contacts") or []
            meeting_place = data.get("meeting_place")
            lat = latitude if latitude is not None else data.get("latitude")
            lng = longitude if longitude is not None else data.get("longitude")
            text = _format_panic_sms(
                user_name=display_name,
                meeting_place=meeting_place,
                latitude=lat,
                longitude=lng,
            )
            notified = 0
            for c in contacts:
                phone = c.get("phone")
                if not phone:
                    continue
                await sms_service.send_text(
                    phone, text, channel="whatsapp"
                )
                notified += 1
            # Timer consommé.
            await redis.delete(key)
            await redis.delete(TIMER_WARNED_KEY.format(user_id=uid))
            log.warning(
                "panic_triggered_with_timer",
                user_id=uid,
                notified=notified,
            )
            return notified

    # Pas de timer actif → primary contact en BD.
    result = await db.execute(
        select(EmergencyContact).where(
            EmergencyContact.user_id == user.id,
            EmergencyContact.is_primary.is_(True),
        )
    )
    primary = result.scalar_one_or_none()
    if primary is None:
        raise FlaamError("contact_required", 400, lang)

    text = _format_panic_sms(
        user_name=display_name,
        meeting_place=None,
        latitude=latitude,
        longitude=longitude,
    )
    await sms_service.send_text(primary.phone, text, channel="whatsapp")
    log.warning(
        "panic_triggered_no_timer",
        user_id=uid,
        contact=primary.phone[-4:],
    )
    return 1


__all__ = [
    "report_user",
    "block_user",
    "unblock_user",
    "share_date",
    # Contacts CRUD
    "list_emergency_contacts",
    "create_emergency_contact",
    "update_emergency_contact",
    "delete_emergency_contact",
    "set_primary_contact",
    # Timer
    "start_emergency_timer",
    "cancel_emergency_timer",
    "update_timer_location",
    "extend_timer",
    "trigger_panic",
    "TIMER_KEY",
    "TIMER_WARNED_KEY",
    "TIMER_GRACE_SECONDS",
    "MIN_HOURS",
    "MAX_HOURS",
    "MAX_CONTACTS_STORED",
    "MAX_CONTACTS_PER_TIMER",
]
