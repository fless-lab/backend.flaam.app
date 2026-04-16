from __future__ import annotations

"""
Event tasks (MàJ 8 + S11 Celery Beat).

Celery n'est pas encore câblé (S10). Ces fonctions sont `async def`
directement appelables et logguent leur déclenchement. En S10 elles
seront décorées `@celery_app.task` et planifiées via Celery Beat (S11).

Tasks :
- event_reminder(event_id)        : push 2h avant l'event aux inscrits
- event_status_updater()          : transition auto draft→ongoing→completed
- send_post_event_nudge(event_id) : WhatsApp teaser aux ghost users non
                                     convertis le lendemain matin
- weekly_event_digest()           : push dimanche soir "3 events cette
                                     semaine"
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event
from app.models.event_registration import EventRegistration
from app.models.user import User

log = structlog.get_logger()


async def event_reminder(event_id: UUID, db: AsyncSession | None = None) -> None:
    """
    Push 2h avant l'event aux inscrits.

    STUB S8. Implémentation réelle en S10 : charge les inscrits checked_in
    ou registered, appelle notification_service.send_push(type="event_reminder")
    en respectant les notification_preferences (flag events + quiet hours).
    """
    log.info(
        "event_reminder_scheduled",
        event_id=str(event_id),
        note="stub — push réel en S10 via notification_service",
    )


async def event_status_updater(db: AsyncSession | None = None) -> None:
    """
    Transition automatique du status des events.
    - draft reste draft (published par admin)
    - published → ongoing quand starts_at ≤ now
    - ongoing → completed quand ends_at ≤ now

    STUB S8. Implémentation réelle en S11 (Celery Beat horaire).
    """
    log.info(
        "event_status_updater_scheduled",
        note="stub — transitions auto en S11 Celery Beat",
    )


async def send_post_event_nudge(
    event_id: UUID, db: AsyncSession | None = None
) -> None:
    """
    Lendemain 9h : WhatsApp teaser aux ghost/pre_registered non convertis.

    Message :
    "Tu as croisé N personnes au {event_name} hier. M ont déjà complété
     leur profil Flaam. Télécharge l'app pour les découvrir."

    STUB S8. Appel WhatsApp réel en S11 via Termii template.
    """
    log.info(
        "post_event_nudge_scheduled",
        event_id=str(event_id),
        note="stub — WhatsApp teaser envoyé en S11",
    )


async def weekly_event_digest(db: AsyncSession | None = None) -> None:
    """
    Dimanche soir : push "N events cette semaine" aux users actifs avec
    notification_preferences.events=True et weekly_digest=True.

    STUB S8. Appel réel en S11 via Celery Beat.
    """
    log.info(
        "weekly_event_digest_scheduled",
        note="stub — push dimanche soir en S11 Celery Beat",
    )


__all__ = [
    "event_reminder",
    "event_status_updater",
    "send_post_event_nudge",
    "weekly_event_digest",
]
