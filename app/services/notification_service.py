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

import asyncio
import os
from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.i18n import t
from app.models.device import Device
from app.models.notification_preference import NotificationPreference
from app.models.user import User

log = structlog.get_logger()
settings = get_settings()


# ── Firebase Admin SDK (lazy init) ──────────────────────────────────
#
# L'init se fait au premier envoi reel pour eviter de bloquer le
# demarrage de l'app si le JSON n'est pas encore present (ex : dev
# local sans creds). Une fois initialise, l'app firebase est
# memorisee dans `_firebase_app` et reutilisee.
#
# Etats possibles :
#   _firebase_app == "uninitialized"  → pas encore tente
#   _firebase_app is None             → tentative ratee (creds absents
#                                       ou invalides) → on log + skip
#   _firebase_app == <App>            → pret a envoyer
_firebase_app: object | None | str = "uninitialized"
_firebase_init_lock = asyncio.Lock()


async def _get_firebase_app() -> object | None:
    """Retourne l'instance firebase_admin.App ou None si indisponible."""
    global _firebase_app
    if _firebase_app != "uninitialized":
        return _firebase_app  # type: ignore[return-value]
    async with _firebase_init_lock:
        if _firebase_app != "uninitialized":
            return _firebase_app  # type: ignore[return-value]
        creds_path = (settings.fcm_service_account_json or "").strip()
        if not creds_path:
            log.info("fcm_init_skipped", reason="no_credentials_path")
            _firebase_app = None
            return None
        if not os.path.isfile(creds_path):
            log.warning(
                "fcm_init_skipped",
                reason="credentials_file_not_found",
                path=creds_path,
            )
            _firebase_app = None
            return None
        try:
            import firebase_admin
            from firebase_admin import credentials

            cred = credentials.Certificate(creds_path)
            app = firebase_admin.initialize_app(cred, name="flaam-fcm")
            _firebase_app = app
            log.info(
                "fcm_init_ok",
                project_id=settings.firebase_project_id or "from_creds",
            )
            return app
        except Exception as exc:  # noqa: BLE001
            log.warning("fcm_init_failed", reason=str(exc))
            _firebase_app = None
            return None


async def _send_fcm_to_tokens(
    *,
    tokens: list[str],
    title: str,
    body: str,
    deep_link: str | None,
    type_: str,
    data: dict,
    db: AsyncSession,
) -> dict:
    """
    Envoie le push a chaque token en parallele. Supprime les tokens
    invalides (UnregisteredError = app desinstallee, token revoque) en
    nettoyant la colonne Device.fcm_token.

    Retourne {"success": int, "failure": int, "invalid_tokens": int}.
    """
    app = await _get_firebase_app()
    if app is None:
        return {
            "success": 0,
            "failure": 0,
            "invalid_tokens": 0,
            "skipped_no_sdk": True,
        }

    from firebase_admin import messaging

    # Construit le payload data : on stringifie tout (FCM exige des str).
    data_payload = {k: str(v) for k, v in data.items()}
    data_payload["type"] = type_
    if deep_link:
        data_payload["deep_link"] = deep_link

    notification = messaging.Notification(title=title, body=body)

    success = 0
    failure = 0
    invalid: list[str] = []

    def _send_one(token: str) -> None:
        nonlocal success, failure
        try:
            message = messaging.Message(
                notification=notification,
                data=data_payload,
                token=token,
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        click_action="FLUTTER_NOTIFICATION_CLICK",
                        channel_id="flaam_general",
                    ),
                ),
            )
            messaging.send(message, app=app)
            success += 1
        except messaging.UnregisteredError:
            invalid.append(token)
            failure += 1
        except Exception as exc:  # noqa: BLE001
            failure += 1
            log.info("fcm_send_failed", token_prefix=token[:12], reason=str(exc))

    # Le SDK est sync ; on offload pour ne pas bloquer l'event loop.
    await asyncio.gather(
        *(asyncio.to_thread(_send_one, tok) for tok in tokens)
    )

    if invalid:
        # Cleanup : on retire les tokens invalides pour ne pas reessayer.
        await db.execute(
            update(Device)
            .where(Device.fcm_token.in_(invalid))
            .values(fcm_token=None)
        )
        await db.commit()
        log.info("fcm_tokens_cleaned", count=len(invalid))

    return {
        "success": success,
        "failure": failure,
        "invalid_tokens": len(invalid),
    }


async def _collect_fcm_tokens(user_id: UUID, db: AsyncSession) -> list[str]:
    """Retourne tous les fcm_token actifs du user (un par device)."""
    rows = await db.execute(
        select(Device.fcm_token).where(
            Device.user_id == user_id,
            Device.fcm_token.is_not(None),
        )
    )
    return [t for t in rows.scalars().all() if t]


# Titres des notifications push (FR/EN). Les bodies vivent dans
# app/core/i18n.py sous la même clé (ex : "notif_new_match").
_NOTIF_TITLES: dict[str, dict[str, str]] = {
    "notif_new_match": {
        "fr": "Nouveau match ! 🔥",
        "en": "New match! 🔥",
    },
    "notif_new_message": {
        "fr": "Nouveau message",
        "en": "New message",
    },
    "notif_new_like": {
        "fr": "Une flamme reçue",
        "en": "New flame received",
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
    "notif_event_teaser": {
        "fr": "Event terminé",
        "en": "Event over",
    },
    "notif_weekly_digest": {
        "fr": "Events de la semaine",
        "en": "This week's events",
    },
    "notif_seen_irl": {
        "fr": "Vous vous êtes croisés",
        "en": "You crossed paths",
    },
    "notif_daily_feed": {
        "fr": "Tes profils du jour",
        "en": "Your profiles of the day",
    },
    "notif_timer_starting_30min": {
        "fr": "Ton timer démarre dans 30 min",
        "en": "Your timer starts in 30 min",
    },
    "notif_timer_started": {
        "fr": "Ton timer a démarré",
        "en": "Your timer has started",
    },
}


# Deep-links Flaam (URI scheme flaam://...). Les placeholders sont
# remplis depuis `data` passé à send_push.
_DEEP_LINKS: dict[str, str] = {
    "notif_new_match": "flaam://matches/{match_id}",
    "notif_new_message": "flaam://chat/{match_id}",
    "notif_new_like": "flaam://feed",
    "notif_event_reminder": "flaam://events/{event_id}",
    "notif_likes_received_count": "flaam://likes",
    "notif_selfie_required": "flaam://profile/selfie",
    "notif_premium_expired": "flaam://subscription",
    "notif_reply_reminder": "flaam://chat/{match_id}",
    "notif_safety_alert_15min": "flaam://safety/timer",
    "notif_event_teaser": "flaam://events/{event_id}",
    "notif_weekly_digest": "flaam://events",
    # Le push J+1 ouvre directement le profil de la personne croisée — la
    # CTA "lance une flamme" est sur l'écran profile avec le bouton like.
    "notif_seen_irl": "flaam://profile/{user_id}",
    "notif_daily_feed": "flaam://feed",
    "notif_timer_starting_30min": "flaam://safety/timer",
    "notif_timer_started": "flaam://safety/timer",
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
    "notif_new_like": None,  # Toujours envoyé (pas de flag dédié)
    "notif_event_reminder": "events",
    "notif_likes_received_count": None,  # Toujours envoyé (pas de flag dédié)
    "notif_selfie_required": None,  # Safety/compliance — bypass prefs
    "notif_premium_expired": None,  # Info compte — toujours envoyé
    "notif_reply_reminder": "reply_reminders",
    "notif_safety_alert_15min": None,  # Safety — toujours envoyé, jamais désactivable.
    "notif_event_teaser": "events",
    "notif_weekly_digest": "events",
    # Push contextuel post-event "Tu as croisé X" — gated par la même
    # préférence "events" car c'est event-related. Désactivable.
    "notif_seen_irl": "events",
    # Push quotidien "Tes profils du jour" — gated par daily_feed pref.
    # Heure d'envoi pilotée par daily_feed_hour (0..23 dans la TZ ville).
    "notif_daily_feed": "daily_feed",
    # Safety scheduled — toujours envoyé, jamais désactivable
    "notif_timer_starting_30min": None,
    "notif_timer_started": None,
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
    from sqlalchemy.orm import selectinload

    data = data or {}
    result = await db.execute(
        select(User).options(selectinload(User.city)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
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
    # Recupere les fcm_token actifs du user (un par device). Si aucun
    # → on log + skip (pas une erreur, l'user n'a juste pas accorde
    # la permission notifications ou pas encore enregistre son token).
    tokens = await _collect_fcm_tokens(user_id, db)
    if not tokens:
        log.info(
            "push_skipped_no_token",
            user_id=str(user_id),
            type=type,
        )
        return {
            "sent": False,
            "reason": "no_fcm_token",
            "type": type,
            "deep_link": deep_link,
        }

    result = await _send_fcm_to_tokens(
        tokens=tokens,
        title=title,
        body=body,
        deep_link=deep_link,
        type_=type,
        data=data,
        db=db,
    )

    if result.get("skipped_no_sdk"):
        log.info(
            "push_skipped_no_sdk",
            user_id=str(user_id),
            type=type,
            reason="firebase_admin_not_initialized",
        )
        return {
            "sent": False,
            "reason": "fcm_credentials_missing",
            "type": type,
            "deep_link": deep_link,
        }

    log.info(
        "push_sent",
        user_id=str(user_id),
        type=type,
        success=result["success"],
        failure=result["failure"],
        invalid_tokens=result["invalid_tokens"],
    )
    return {
        "sent": result["success"] > 0,
        "reason": "fcm_delivered" if result["success"] > 0 else "fcm_all_failed",
        "type": type,
        "deep_link": deep_link,
        "fcm_success": result["success"],
        "fcm_failure": result["failure"],
        "fcm_invalid_tokens": result["invalid_tokens"],
    }


__all__ = [
    "get_or_create_preferences",
    "update_preferences",
    "register_fcm_token",
    "send_push",
]
