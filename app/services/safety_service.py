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
from app.models.emergency_session import EmergencySession
from app.models.match import Match
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


async def _resolve_partner_from_match(
    *,
    user: User,
    match_id: UUID,
    partner_user_id: UUID | None,
    db: AsyncSession,
    lang: str,
) -> UUID:
    """
    Résout le partner_user_id à partir d'un match.

    Si `partner_user_id` est fourni explicitement, on lui fait confiance.
    Sinon, on dérive depuis la row Match le user qui n'est PAS celui qui
    arme le timer. 403 si le match n'appartient pas à l'user courant.
    """
    match = await db.get(Match, match_id)
    if match is None:
        raise FlaamError("match_not_found", 404, lang)
    if user.id not in (match.user_a_id, match.user_b_id):
        raise AppException(status.HTTP_403_FORBIDDEN, "not_your_match")

    if partner_user_id is not None:
        # L'user fourni doit être l'autre côté du match, sinon refus.
        if partner_user_id not in (match.user_a_id, match.user_b_id):
            raise AppException(status.HTTP_403_FORBIDDEN, "not_your_match")
        if partner_user_id == user.id:
            raise AppException(status.HTTP_400_BAD_REQUEST, "invalid_partner")
        return partner_user_id

    return match.user_b_id if match.user_a_id == user.id else match.user_a_id


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
    match_id: UUID | None = None,
    partner_user_id: UUID | None = None,
) -> tuple[datetime, UUID]:
    """
    Arme le timer d'urgence — écrit une row EmergencySession en BD
    (source de vérité forensique) puis pousse l'état en Redis pour le
    task Celery.

    TTL Redis = hours*3600 + TIMER_GRACE_SECONDS. Le task Celery lit
    `expires_at_utc` pour décider d'envoyer le SMS d'alerte. Sans la
    grâce, la clé disparaîtrait avant qu'un task 1-minute puisse la
    voir expirer.

    Retourne (expires_at_utc, session_id).
    """
    _validate_hours(hours, lang)

    # SAFETY-6 : un seul timer actif à la fois. Si une clé existe encore en
    # Redis (non expirée), refuse — l'utilisateur doit annuler d'abord. Ça
    # évite les "sessions orphelines" en DB (une nouvelle écrase la clé
    # Redis sans fermer la row emergency_sessions précédente).
    existing = await redis.get(TIMER_KEY.format(user_id=str(user.id)))
    if existing is not None:
        raise FlaamError("timer_already_active", 409, lang)

    contacts = await _resolve_contacts(
        user=user,
        contact_ids=contact_ids,
        contact_phone=contact_phone,
        contact_name=contact_name,
        db=db,
        lang=lang,
    )

    # SAFETY-6 : si un match_id est fourni, dérive / valide le partner.
    resolved_partner_id: UUID | None = partner_user_id
    if match_id is not None:
        resolved_partner_id = await _resolve_partner_from_match(
            user=user,
            match_id=match_id,
            partner_user_id=partner_user_id,
            db=db,
            lang=lang,
        )

    timer_seconds = int(hours * 3600)
    now = datetime.now(timezone.utc)
    expires_at_utc = datetime.fromtimestamp(
        now.timestamp() + timer_seconds, tz=timezone.utc
    )

    # ── DB row FIRST (source de vérité) ──
    session = EmergencySession(
        user_id=user.id,
        partner_user_id=resolved_partner_id,
        match_id=match_id,
        meeting_place=meeting_place,
        latitude=latitude,
        longitude=longitude,
        contacts_snapshot=contacts,
        hours=hours,
        started_at=now,
        expires_at=expires_at_utc,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    # ── Redis payload (cache pour Celery) ──
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
        "session_id": str(session.id),
        "partner_user_id": (
            str(resolved_partner_id) if resolved_partner_id else None
        ),
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
        session_id=str(session.id),
        expires_at=expires_at_utc.isoformat(),
        hours=hours,
        contacts_count=len(contacts),
        match_id=str(match_id) if match_id else None,
    )
    return expires_at_utc, session.id


async def _find_active_session(
    *, user_id: UUID, db: AsyncSession
) -> EmergencySession | None:
    """
    Cherche la session d'urgence active (ended_at IS NULL) la plus
    récente pour un user donné. Utilisée par cancel/panic/expiry pour
    clôturer proprement la row d'audit.
    """
    row = await db.execute(
        select(EmergencySession)
        .where(
            EmergencySession.user_id == user_id,
            EmergencySession.ended_at.is_(None),
        )
        .order_by(EmergencySession.started_at.desc())
        .limit(1)
    )
    return row.scalar_one_or_none()


async def cancel_emergency_timer(
    *, user: User, db: AsyncSession, redis: aioredis.Redis
) -> bool:
    uid = str(user.id)
    deleted = await redis.delete(TIMER_KEY.format(user_id=uid))
    await redis.delete(TIMER_WARNED_KEY.format(user_id=uid))
    cancelled = bool(deleted)

    # SAFETY-6 : clôt la row d'audit même si Redis n'avait plus la clé
    # (cas du redémarrage ou d'un grace élargi). Ne pas créer de row si
    # aucune session active trouvée.
    session = await _find_active_session(user_id=user.id, db=db)
    if session is not None:
        session.ended_at = datetime.now(timezone.utc)
        session.end_reason = "cancelled"
        await db.commit()

    log.info(
        "emergency_timer_cancel",
        user_id=uid,
        cancelled=cancelled,
        session_id=str(session.id) if session else None,
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
    partner_name: str | None = None,
) -> str:
    place = f" a {meeting_place}" if meeting_place else ""
    loc = ""
    if latitude is not None and longitude is not None:
        loc = (
            f"\nPosition : https://maps.google.com/maps?"
            f"q={latitude},{longitude}"
        )
    partner_line = ""
    if partner_name:
        partner_line = f"\nElle/il devait rencontrer {partner_name}."
    return (
        f"ALERTE URGENTE FLAAM : {user_name} a declenche une alerte "
        f"d'urgence{place}.{loc}{partner_line}\n"
        f"Contacte-la/le immediatement. "
        f"Pour signaler : https://flaam.app/safety/contact"
    )


async def _partner_display_name(
    *, partner_user_id: UUID | None, db: AsyncSession
) -> str | None:
    """Charge le display_name via Profile.user (relationship selectin)."""
    if partner_user_id is None:
        return None
    partner = await db.get(User, partner_user_id)
    if partner is None:
        return None
    return partner.profile.display_name if partner.profile else None


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

    SAFETY-6 : met à jour la row EmergencySession avec panic_triggered_at
    et end_reason="panic_triggered". Enrichit le SMS avec le nom du
    partenaire si l'info est connue (via la session).

    Retourne le nombre de contacts notifiés.
    """
    uid = str(user.id)
    key = TIMER_KEY.format(user_id=uid)
    raw = await redis.get(key)

    display_name = _user_display_name(user)

    # SAFETY-6 : récupère la session active pour enrichir l'alerte + la
    # clôturer. Source prioritaire : BD > Redis.
    active_session = await _find_active_session(user_id=user.id, db=db)
    partner_user_id: UUID | None = (
        active_session.partner_user_id if active_session else None
    )
    partner_name = await _partner_display_name(
        partner_user_id=partner_user_id, db=db
    )

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
            # Fallback : si pas de session BD (cas legacy), lis le
            # partner_user_id depuis Redis et recharge le nom.
            if partner_name is None:
                redis_partner_raw = data.get("partner_user_id")
                if redis_partner_raw:
                    try:
                        partner_name = await _partner_display_name(
                            partner_user_id=UUID(redis_partner_raw), db=db
                        )
                    except (ValueError, TypeError):
                        pass
            text = _format_panic_sms(
                user_name=display_name,
                meeting_place=meeting_place,
                latitude=lat,
                longitude=lng,
                partner_name=partner_name,
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

            if active_session is not None:
                now = datetime.now(timezone.utc)
                active_session.panic_triggered_at = now
                active_session.ended_at = now
                active_session.end_reason = "panic_triggered"
                await db.commit()

            log.warning(
                "panic_triggered_with_timer",
                user_id=uid,
                notified=notified,
                session_id=(
                    str(active_session.id) if active_session else None
                ),
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
        partner_name=partner_name,
    )
    await sms_service.send_text(primary.phone, text, channel="whatsapp")

    if active_session is not None:
        now = datetime.now(timezone.utc)
        active_session.panic_triggered_at = now
        active_session.ended_at = now
        active_session.end_reason = "panic_triggered"
        await db.commit()

    log.warning(
        "panic_triggered_no_timer",
        user_id=uid,
        contact=primary.phone[-4:],
    )
    return 1


async def mark_session_expired(
    *, user_id: UUID, db: AsyncSession
) -> EmergencySession | None:
    """
    Appelée par le task Celery après envoi des SMS d'expiration.

    Clôt la row EmergencySession active la plus récente avec
    end_reason="expired_sms_sent". Idempotent : si aucune session
    active n'existe (legacy / corruption), retourne None sans lever.
    """
    session = await _find_active_session(user_id=user_id, db=db)
    if session is None:
        return None
    session.ended_at = datetime.now(timezone.utc)
    session.end_reason = "expired_sms_sent"
    await db.commit()
    return session


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
    "mark_session_expired",
    "TIMER_KEY",
    "TIMER_WARNED_KEY",
    "TIMER_GRACE_SECONDS",
    "MIN_HOURS",
    "MAX_HOURS",
    "MAX_CONTACTS_STORED",
    "MAX_CONTACTS_PER_TIMER",
]
