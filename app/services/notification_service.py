from __future__ import annotations

"""
Notification service (§5.10, §26 templates).

Responsabilités :
- Gestion des préférences (read/upsert default)
- Enregistrement FCM token sur le Device
- send_push(user_id, type, data) — abstraction FCM (log-only si
  FCM_ENABLED=false, appel firebase-admin si true)

Respect des préférences : chaque type de push est gaté par un flag dans
NotificationPreference + quiet hours (simple check heure locale UTC).
"""

from datetime import datetime, timezone
from uuid import UUID

import structlog
from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.models.device import Device
from app.models.notification_preference import NotificationPreference
from app.models.user import User

log = structlog.get_logger()
settings = get_settings()


# Templates FR/EN (spec §26). Simples, non manipulateurs, conformes au
# principe produit #3 (zéro notif marketing).
TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    "new_match": {
        "fr": {
            "title": "Nouveau match !",
            "body": "Tu as un nouveau match. Dis-lui bonjour !",
        },
        "en": {
            "title": "New match!",
            "body": "You have a new match. Say hi!",
        },
    },
    "new_message": {
        "fr": {
            "title": "Nouveau message",
            "body": "{sender_name} t'a envoyé un message.",
        },
        "en": {
            "title": "New message",
            "body": "{sender_name} sent you a message.",
        },
    },
    "event_reminder": {
        "fr": {
            "title": "Rappel event",
            "body": "{event_name} commence dans 2h.",
        },
        "en": {
            "title": "Event reminder",
            "body": "{event_name} starts in 2h.",
        },
    },
    "likes_received_count": {
        "fr": {
            "title": "Tu plais",
            "body": "{count} personnes t'ont liké cette semaine.",
        },
        "en": {
            "title": "You're popular",
            "body": "{count} people liked you this week.",
        },
    },
}


# ══════════════════════════════════════════════════════════════════════
# Préférences
# ══════════════════════════════════════════════════════════════════════


async def get_or_create_preferences(
    user: User, db: AsyncSession
) -> NotificationPreference:
    row = await db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user.id
        )
    )
    prefs = row.scalar_one_or_none()
    if prefs is None:
        prefs = NotificationPreference(user_id=user.id)
        db.add(prefs)
        await db.commit()
        await db.refresh(prefs)
    return prefs


async def update_preferences(
    user: User, updates: dict, db: AsyncSession
) -> NotificationPreference:
    prefs = await get_or_create_preferences(user, db)
    for field, value in updates.items():
        if value is not None and hasattr(prefs, field):
            setattr(prefs, field, value)
    await db.commit()
    await db.refresh(prefs)
    return prefs


# ══════════════════════════════════════════════════════════════════════
# FCM token
# ══════════════════════════════════════════════════════════════════════


async def register_fcm_token(
    user: User,
    *,
    fcm_token: str,
    device_fingerprint: str,
    platform: str | None,
    db: AsyncSession,
) -> None:
    """
    Upsert du FCM token sur le Device correspondant au fingerprint.
    Si aucun device n'existe (cas rare — normalement créé à l'OTP verify),
    on en crée un.
    """
    row = await db.execute(
        select(Device).where(
            Device.user_id == user.id,
            Device.device_fingerprint == device_fingerprint,
        )
    )
    dev = row.scalar_one_or_none()
    if dev is None:
        dev = Device(
            user_id=user.id,
            device_fingerprint=device_fingerprint,
            platform=platform or "android",
            fcm_token=fcm_token,
        )
        db.add(dev)
    else:
        dev.fcm_token = fcm_token
        if platform:
            dev.platform = platform
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# Push notifications
# ══════════════════════════════════════════════════════════════════════


_TYPE_TO_PREF_FIELD: dict[str, str | None] = {
    "new_match": "new_match",
    "new_message": "new_message",
    "event_reminder": "events",
    "likes_received_count": None,  # Toujours envoyé (pas de flag dédié)
}


def _in_quiet_hours(prefs: NotificationPreference, now: datetime) -> bool:
    """
    Respect des quiet hours. Approximation simple : on compare à l'heure
    UTC du serveur. Le refinement par timezone user viendra en S11.
    """
    h = now.hour
    start = prefs.quiet_start_hour
    end = prefs.quiet_end_hour
    if start == end:
        return False
    if start < end:
        return start <= h < end
    # Fenêtre qui traverse minuit (ex 23 → 7)
    return h >= start or h < end


async def send_push(
    user_id: UUID,
    *,
    type: str,
    data: dict | None = None,
    db: AsyncSession,
) -> dict:
    """
    Envoie un push à un user.

    Au MVP (FCM_ENABLED=false) : log + noop.
    En prod (FCM_ENABLED=true) : appel firebase-admin SDK (TODO S11).

    Retourne un dict décrivant le résultat (utile pour les tests) :
    {"sent": bool, "reason": str | None, "type": str}
    """
    data = data or {}
    user = await db.get(User, user_id)
    if user is None or user.is_deleted or not user.is_active:
        return {"sent": False, "reason": "user_unavailable", "type": type}

    prefs = await get_or_create_preferences(user, db)

    # Gating par flag de préférence
    flag_field = _TYPE_TO_PREF_FIELD.get(type)
    if flag_field is not None and not getattr(prefs, flag_field, True):
        return {"sent": False, "reason": "pref_disabled", "type": type}

    # Quiet hours (sauf pour les pushs safety — non implémentés ici)
    now = datetime.now(timezone.utc)
    if _in_quiet_hours(prefs, now):
        return {"sent": False, "reason": "quiet_hours", "type": type}

    # Rendu template
    lang = (user.language or "fr") if user.language in ("fr", "en") else "fr"
    tpl_set = TEMPLATES.get(type, {}).get(lang) or TEMPLATES.get(
        type, {}
    ).get("fr")
    if tpl_set is None:
        return {"sent": False, "reason": "unknown_type", "type": type}

    try:
        title = tpl_set["title"].format(**data)
        body = tpl_set["body"].format(**data)
    except KeyError as e:
        log.warning(
            "push_template_missing_var", type=type, missing=str(e)
        )
        return {"sent": False, "reason": "template_missing_var", "type": type}

    if not settings.fcm_enabled:
        log.info(
            "push_logged",
            user_id=str(user_id),
            type=type,
            title=title,
            body=body,
        )
        return {"sent": True, "reason": "logged_mvp", "type": type}

    # ── FCM réel (firebase-admin) ────────────────────────────────────
    # Stub S8 : on log + on signale "fcm_not_cabled" ; le câblage
    # firebase-admin viendra en S11 avec le secret FCM_SERVICE_ACCOUNT_JSON.
    log.info(
        "push_fcm_noop", user_id=str(user_id), type=type,
        note="FCM enabled but firebase-admin SDK not wired yet (S11)",
    )
    return {"sent": False, "reason": "fcm_not_wired", "type": type}


__all__ = [
    "TEMPLATES",
    "get_or_create_preferences",
    "update_preferences",
    "register_fcm_token",
    "send_push",
]
