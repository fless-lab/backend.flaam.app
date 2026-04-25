from __future__ import annotations

"""
Event tasks (MàJ 8 + S11 Celery Beat + S14.7 real impl).

Tasks :
- event_reminder()               : push 2h avant l'event aux inscrits
- event_status_updater()         : transition auto published→ongoing→completed
- send_post_event_nudge(event_id): push teaser post-event aux inscrits
- weekly_event_digest()          : push dimanche soir "N events cette semaine"
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.db.session import async_session
from app.models.event import Event
from app.models.event_registration import EventRegistration
from app.models.user import User
from app.services import notification_service, seen_irl_service

log = structlog.get_logger()


# ══════════════════════════════════════════════════════════════════════
# Async implementations
# ══════════════════════════════════════════════════════════════════════


async def _event_reminder_async() -> dict:
    """
    Cherche les events qui commencent dans < 2h et dont le rappel
    n'a pas encore ete envoye. Envoie une push a chaque inscrit.
    """
    now = datetime.now(timezone.utc)
    window = now + timedelta(hours=2)

    async with async_session() as db:
        rows = await db.execute(
            select(Event).where(
                Event.starts_at.between(now, window),
                Event.status.in_(("published", "full")),
                Event.reminder_sent_at.is_(None),
                Event.is_active.is_(True),
            )
        )
        events = rows.scalars().all()
        reminded = 0

        for event in events:
            reg_rows = await db.execute(
                select(EventRegistration).where(
                    EventRegistration.event_id == event.id,
                    EventRegistration.status.in_(("registered", "checked_in")),
                )
            )
            for reg in reg_rows.scalars():
                await notification_service.send_push(
                    reg.user_id,
                    type="notif_event_reminder",
                    data={
                        "event_name": event.title,
                        "event_id": str(event.id),
                    },
                    db=db,
                )
                reminded += 1

            event.reminder_sent_at = now

        await db.commit()

    log.info("event_reminder_done", events=len(events), reminded=reminded)
    return {"events": len(events), "reminded": reminded}


async def _event_status_updater_async() -> dict:
    """
    Transitions automatiques :
    - published/full → ongoing  (starts_at <= now < ends_at)
    - ongoing → completed       (ends_at <= now)
    Quand un event passe a completed, schedule le nudge post-event.
    """
    now = datetime.now(timezone.utc)

    async with async_session() as db:
        # published/full → ongoing
        to_ongoing = await db.execute(
            update(Event)
            .where(
                Event.starts_at <= now,
                Event.status.in_(("published", "full")),
                Event.is_active.is_(True),
            )
            .values(status="ongoing")
            .returning(Event.id)
        )
        ongoing_ids = [r[0] for r in to_ongoing.all()]

        # ongoing → completed (only if ends_at is set)
        to_completed = await db.execute(
            update(Event)
            .where(
                Event.ends_at <= now,
                Event.ends_at.isnot(None),
                Event.status == "ongoing",
            )
            .values(status="completed")
            .returning(Event.id)
        )
        completed_ids = [r[0] for r in to_completed.all()]

        await db.commit()

    # Schedule post-event nudge for newly completed events
    for eid in completed_ids:
        try:
            send_post_event_nudge_task.apply_async(
                args=[str(eid)],
                eta=datetime.now(timezone.utc) + timedelta(hours=12),
            )
        except Exception:
            log.warning("nudge_schedule_failed", event_id=str(eid))

    log.info(
        "event_status_updater_done",
        to_ongoing=len(ongoing_ids),
        to_completed=len(completed_ids),
    )
    return {"to_ongoing": len(ongoing_ids), "to_completed": len(completed_ids)}


async def _send_post_event_nudge_async(event_id_str: str) -> dict:
    """Push teaser post-event aux inscrits."""
    event_id = UUID(event_id_str)

    async with async_session() as db:
        event = await db.get(Event, event_id)
        if event is None:
            return {"status": "event_not_found"}

        reg_rows = await db.execute(
            select(EventRegistration).where(
                EventRegistration.event_id == event_id,
                EventRegistration.status.in_(("registered", "checked_in")),
            )
        )
        nudged = 0
        for reg in reg_rows.scalars():
            await notification_service.send_push(
                reg.user_id,
                type="notif_event_teaser",
                data={
                    "title": event.title,
                    "event_id": str(event.id),
                },
                db=db,
            )
            nudged += 1

    log.info("post_event_nudge_done", event_id=event_id_str, nudged=nudged)
    return {"nudged": nudged}


async def _send_seen_irl_pushes_async() -> dict:
    """
    Push J+1 : pour chaque user qui a check-in vérifié hier, on lui envoie
    une notification "Tu as croisé X à Y. Lance une flamme ?".

    Limites pour éviter le spam :
    - Max 1 push par user par jour (le service retourne 1 paire par target).
    - Skip si target et other sont déjà en Match.
    - Gated par préférence "events" (désactivable côté mobile).
    """
    async with async_session() as db:
        pairs = await seen_irl_service.get_yesterday_pairs(db)

        from app.models.profile import Profile

        sent = 0
        for target_id, other_id, _event_id, event_title in pairs:
            other_name_row = await db.execute(
                select(Profile.display_name).where(
                    Profile.user_id == other_id,
                ),
            )
            other_name = other_name_row.scalar_one_or_none() or "Quelqu'un"

            res = await notification_service.send_push(
                target_id,
                type="notif_seen_irl",
                data={
                    "name": other_name,
                    "event_title": event_title,
                    "user_id": str(other_id),
                },
                db=db,
            )
            if res.get("sent"):
                sent += 1

    log.info("seen_irl_pushes_done", pairs=len(pairs), sent=sent)
    return {"pairs": len(pairs), "sent": sent}


async def _weekly_event_digest_async() -> dict:
    """Push digest des events de la semaine prochaine aux users actifs."""
    now = datetime.now(timezone.utc)
    next_week = now + timedelta(days=7)

    async with async_session() as db:
        rows = await db.execute(
            select(Event)
            .where(
                Event.starts_at.between(now, next_week),
                Event.status.in_(("published", "full")),
                Event.is_active.is_(True),
                Event.is_approved.is_(True),
            )
            .order_by(Event.starts_at)
        )
        events = rows.scalars().all()
        if not events:
            log.info("weekly_event_digest_skip", reason="no_upcoming_events")
            return {"notified": 0}

        user_rows = await db.execute(
            select(User).where(
                User.is_active.is_(True),
                User.is_deleted.is_(False),
            )
        )
        notified = 0
        for user in user_rows.scalars():
            await notification_service.send_push(
                user.id,
                type="notif_weekly_digest",
                data={
                    "count": len(events),
                    "first_title": events[0].title,
                },
                db=db,
            )
            notified += 1

    log.info("weekly_event_digest_done", events=len(events), notified=notified)
    return {"events": len(events), "notified": notified}


# ══════════════════════════════════════════════════════════════════════
# Celery wrappers
# ══════════════════════════════════════════════════════════════════════


@celery_app.task(name="app.tasks.event_tasks.event_reminder")
def event_reminder_task() -> dict:
    return asyncio.run(_event_reminder_async())


@celery_app.task(name="app.tasks.event_tasks.event_status_updater")
def event_status_updater_task() -> dict:
    return asyncio.run(_event_status_updater_async())


@celery_app.task(name="app.tasks.event_tasks.send_post_event_nudge")
def send_post_event_nudge_task(event_id: str) -> dict:
    return asyncio.run(_send_post_event_nudge_async(event_id))


@celery_app.task(name="app.tasks.event_tasks.weekly_event_digest")
def weekly_event_digest_task() -> dict:
    return asyncio.run(_weekly_event_digest_async())


@celery_app.task(name="app.tasks.event_tasks.send_seen_irl_pushes")
def send_seen_irl_pushes_task() -> dict:
    return asyncio.run(_send_seen_irl_pushes_async())


__all__ = [
    "event_reminder_task",
    "event_status_updater_task",
    "send_post_event_nudge_task",
    "weekly_event_digest_task",
    "send_seen_irl_pushes_task",
]
