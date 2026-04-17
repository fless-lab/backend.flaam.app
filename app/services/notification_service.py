from __future__ import annotations

"""
Notification service (§5.10, §26 templates).

Responsabilités :
- Gestion des préférences (read/upsert default)
- Enregistrement FCM token sur le Device
- send_push(user_id, type, data) — abstraction FCM (log-only si
  FCM_ENABLED=false, appel firebase-admin si true)

Respect des préférences : chaque type de push est gaté par un flag dans
NotificationPreference + quiet hours local (timezone de la City du user).

Session 11 — i18n :
- Titres des pushs dans `_NOTIF_TITLES` (FR/EN).
- Body via `app.core.i18n.t(type, lang, **data)`.
- Types préfixés `notif_*` pour les mapper 1-to-1 aux clés MESSAGES.
- Deep-link Flaam dans la payload data (navigation in-app).
"""

from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.i18n import t
from app.models.device import Device
from app.models.notification_preference import NotificationPreference
from app.models.user import User

log = structlog.get_logger()
settings = get_settings()


# Titres des notifications push (FR/EN). Les bodies vivent dans
# app/core/i18n.py sous la même clé (ex : "notif_new_match").
_NOTIF_TITLES: dict[str, dict[str, str]] = {
    "notif_new_match": {
        "fr": "Nouveau match !",
        "en": "New match!",
    },
    "notif_new_message": {
        "fr": "Nouveau message",
        "en": "New message",
    },
    "notif_event_reminder": {
        "fr": "Rappel event",
        "en": "Event reminder",
    },
    "notif_likes_received_count": {
        "fr": "Tu plais",
        "en": "You're popular",
    },
    "notif_selfie_required": {
        "fr": "Vérification à refaire",
        "en": "Re-verification needed",
    },
    "notif_premium_expired": {
        "fr": "Premium expiré",
        "en": "Premium expired",
    },
    "notif_reply_reminder": {
        "fr": "Réponds à ton match",
        "en": "Reply to your match",
    },
    "notif_safety_alert_15min": {
        "fr": "Timer bientôt expiré",
        "en": "Timer expiring soon",
    },
}


# Deep-links Flaam (URI scheme flaam://...). Les placeholders sont
# remplis depuis `data` passé à send_push.
_DEEP_LINKS: dict[str, str] = {
    "notif_new_match": "flaam://matches/{match_id}",
    "notif_new_message": "flaam://chat/{match_id}",
    "notif_event_reminder": "flaam://events/{event_id}",
    "notif_likes_received_count": "flaam://likes",
    "notif_selfie_required": "flaam://profile/selfie",
    "notif_premium_expired": "flaam://subscription",
    "notif_reply_reminder": "flaam://chat/{match_id}",
    "notif_safety_alert_15min": "flaam://safety/timer",
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
    "notif_new_match": "new_match",
    "notif_new_message": "new_message",
    "notif_event_reminder": "events",
    "notif_likes_received_count": None,  # Toujours envoyé (pas de flag dédié)
    "notif_selfie_required": None,  # Safety/compliance — bypass prefs
    "notif_premium_expired": None,  # Info compte — toujours envoyé
    "notif_reply_reminder": "reply_reminders",
    "notif_safety_alert_15min": None,  # Safety — toujours envoyé, jamais désactivable.
}


def _in_quiet_hours(
    prefs: NotificationPreference,
    user: User,
    now_utc: datetime,
) -> bool:
    """
    Respect des quiet hours en heure locale de la ville du user.

    Si le user n'a pas de ville (onboarding incomplet) ou que la tz est
    invalide, on retombe sur UTC pour ne pas spammer.
    """
    tz_name = user.city.timezone if user.city is not None else "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    h = now_utc.astimezone(tz).hour
    start = prefs.quiet_start_hour
    end = prefs.quiet_end_hour
    if start == end:
        return False
    if start < end:
        return start <= h < end
    # Fenêtre qui traverse minuit (ex 23 → 7)
    return h >= start or h < end


def _format_deep_link(type_: str, data: dict) -> str | None:
    tpl = _DEEP_LINKS.get(type_)
    if tpl is None:
        return None
    try:
        return tpl.format(**data)
    except (KeyError, IndexError):
        return tpl


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
    {"sent": bool, "reason": str | None, "type": str, "deep_link": str | None}
    """
    data = data or {}
    user = await db.get(User, user_id)
    if user is None or user.is_deleted or not user.is_active:
        return {"sent": False, "reason": "user_unavailable", "type": type}

    prefs = await get_or_create_preferences(user, db)

    # Gating par flag de préférence
    if type not in _TYPE_TO_PREF_FIELD:
        return {"sent": False, "reason": "unknown_type", "type": type}
    flag_field = _TYPE_TO_PREF_FIELD.get(type)
    if flag_field is not None and not getattr(prefs, flag_field, True):
        return {"sent": False, "reason": "pref_disabled", "type": type}

    # Quiet hours (sauf pour les pushs safety — non implémentés ici)
    now = datetime.now(timezone.utc)
    if _in_quiet_hours(prefs, user, now):
        return {"sent": False, "reason": "quiet_hours", "type": type}

    # Rendu : titre depuis _NOTIF_TITLES, body via t() (cle identique au type).
    lang = user.language if user.language in ("fr", "en") else "fr"
    title_set = _NOTIF_TITLES.get(type)
    if title_set is None:
        return {"sent": False, "reason": "unknown_type", "type": type}
    title = title_set.get(lang) or title_set.get("fr") or type
    body = t(type, lang, **data)
    deep_link = _format_deep_link(type, data)

    if not settings.fcm_enabled:
        log.info(
            "push_logged",
            user_id=str(user_id),
            type=type,
            title=title,
            body=body,
            deep_link=deep_link,
        )
        return {
            "sent": True,
            "reason": "logged_mvp",
            "type": type,
            "deep_link": deep_link,
        }

    # ── FCM réel (firebase-admin) ────────────────────────────────────
    # Stub S8 : on log + on signale "fcm_not_cabled" ; le câblage
    # firebase-admin viendra en S11 avec le secret FCM_SERVICE_ACCOUNT_JSON.
    log.info(
        "push_fcm_noop",
        user_id=str(user_id),
        type=type,
        note="FCM enabled but firebase-admin SDK not wired yet (S11)",
    )
    return {
        "sent": False,
        "reason": "fcm_not_wired",
        "type": type,
        "deep_link": deep_link,
    }


__all__ = [
    "get_or_create_preferences",
    "update_preferences",
    "register_fcm_token",
    "send_push",
]
