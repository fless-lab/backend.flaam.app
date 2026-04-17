from __future__ import annotations

"""Tests Celery tasks (Session 12)."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.account_history import AccountHistory
from app.models.behavior_log import BehaviorLog
from app.models.match import Match
from app.models.message import Message
from app.models.notification_preference import NotificationPreference
from app.models.subscription import Subscription
from app.services.safety_service import TIMER_KEY
from app.tasks.cleanup_tasks import (
    _cleanup_account_histories_async,
    _purge_expired_matches_async,
    _purge_old_behavior_logs_async,
)
from app.tasks.emergency_tasks import _send_emergency_sms_async
from app.tasks.reminder_tasks import _send_reply_reminders_async
from app.tasks.subscription_tasks import _check_expired_subscriptions_async
from tests._feed_setup import seed_ama_and_kofi

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ══════════════════════════════════════════════════════════════════════
# subscription_tasks.check_expired_subscriptions
# ══════════════════════════════════════════════════════════════════════


async def test_check_expired_subscriptions_downgrades(db_session, redis_client):
    """Sub expirée active → is_active=False + user.is_premium=False + notif."""
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]
    ama.is_premium = True
    now = datetime.now(timezone.utc)
    db_session.add(
        Subscription(
            user_id=ama.id,
            plan="monthly",
            provider="paystack",
            payment_method="mobile_money",
            amount=5000,
            currency="XOF",
            starts_at=now - timedelta(days=31),
            expires_at=now - timedelta(hours=1),
            is_active=True,
        )
    )
    await db_session.commit()

    result = await _check_expired_subscriptions_async(db_session)
    assert result["processed"] == 1

    await db_session.refresh(ama)
    assert ama.is_premium is False

    sub_row = await db_session.execute(
        select(Subscription).where(Subscription.user_id == ama.id)
    )
    assert sub_row.scalar_one().is_active is False


# ══════════════════════════════════════════════════════════════════════
# reminder_tasks.send_reply_reminders
# ══════════════════════════════════════════════════════════════════════


async def test_send_reply_reminders_sends_and_marks_cooldown(
    db_session, redis_client
):
    """Candidate trouvé → push envoyé → cooldown posé."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    now = datetime.now(timezone.utc)
    match = Match(
        id=uuid4(),
        user_a_id=ama.id,
        user_b_id=kofi.id,
        status="matched",
        matched_at=now - timedelta(days=3),
    )
    db_session.add(match)
    await db_session.flush()
    db_session.add(
        Message(
            match_id=match.id,
            sender_id=kofi.id,
            message_type="text",
            content="Salut ?",
            created_at=now - timedelta(hours=30),
        )
    )
    # Désactive les quiet hours pour le recipient (sinon push skippé selon l'heure)
    db_session.add(
        NotificationPreference(
            user_id=ama.id,
            reply_reminders=True,
            quiet_start_hour=0,
            quiet_end_hour=0,
        )
    )
    await db_session.commit()

    result = await _send_reply_reminders_async(db_session, redis_client)
    assert result["candidates"] >= 1
    assert result["sent"] >= 1

    # Cooldown posé
    cd = await redis_client.get(f"reminder:{match.id}")
    assert cd == "1"


async def test_send_reply_reminders_respects_cooldown(
    db_session, redis_client
):
    """Cooldown déjà posé → le match n'est pas re-notifié."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    now = datetime.now(timezone.utc)
    match = Match(
        id=uuid4(),
        user_a_id=ama.id,
        user_b_id=kofi.id,
        status="matched",
        matched_at=now - timedelta(days=3),
    )
    db_session.add(match)
    await db_session.flush()
    db_session.add(
        Message(
            match_id=match.id,
            sender_id=kofi.id,
            message_type="text",
            content="Salut ?",
            created_at=now - timedelta(hours=30),
        )
    )
    await db_session.commit()

    # Cooldown pré-posé
    await redis_client.set(f"reminder:{match.id}", "1", ex=48 * 3600)

    result = await _send_reply_reminders_async(db_session, redis_client)
    assert result["sent"] == 0


# ══════════════════════════════════════════════════════════════════════
# cleanup_tasks
# ══════════════════════════════════════════════════════════════════════


async def test_purge_expired_matches_marks_stale(db_session, redis_client):
    """Match matched avec last_message_at > 7j → status=expired."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    now = datetime.now(timezone.utc)

    stale = Match(
        id=uuid4(),
        user_a_id=ama.id,
        user_b_id=kofi.id,
        status="matched",
        matched_at=now - timedelta(days=30),
        last_message_at=now - timedelta(days=10),
    )
    db_session.add(stale)
    await db_session.commit()

    result = await _purge_expired_matches_async(db_session)
    assert result["count"] >= 1

    await db_session.refresh(stale)
    assert stale.status == "expired"


async def test_purge_old_behavior_logs_deletes_ancient(
    db_session, redis_client
):
    """BehaviorLog > 90j → DELETE."""
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    old_log = BehaviorLog(
        id=uuid4(),
        user_id=ama.id,
        event_type="view",
        target_user_id=data["kofi"].id,
    )
    db_session.add(old_log)
    await db_session.flush()
    # Force created_at dans le passé
    old_log.created_at = datetime.now(timezone.utc) - timedelta(days=100)
    await db_session.commit()

    result = await _purge_old_behavior_logs_async(db_session)
    assert result["count"] >= 1

    row = await db_session.execute(
        select(BehaviorLog).where(BehaviorLog.id == old_log.id)
    )
    assert row.scalar_one_or_none() is None


async def test_cleanup_account_histories_preserves_banned(
    db_session, redis_client
):
    """AccountHistory > 2y + bans > 0 : conservée (anti-récidive)."""
    old = datetime.now(timezone.utc) - timedelta(days=800)
    history_banned = AccountHistory(
        id=uuid4(),
        phone_hash="hash_banned",
        total_bans=1,
        last_account_created_at=old,
    )
    history_clean = AccountHistory(
        id=uuid4(),
        phone_hash="hash_clean",
        total_bans=0,
        last_account_created_at=old,
    )
    db_session.add_all([history_banned, history_clean])
    await db_session.commit()

    result = await _cleanup_account_histories_async(db_session)
    assert result["count"] >= 1

    banned_row = await db_session.execute(
        select(AccountHistory).where(AccountHistory.id == history_banned.id)
    )
    assert banned_row.scalar_one_or_none() is not None
    clean_row = await db_session.execute(
        select(AccountHistory).where(AccountHistory.id == history_clean.id)
    )
    assert clean_row.scalar_one_or_none() is None


# ══════════════════════════════════════════════════════════════════════
# emergency_tasks.send_emergency_sms
# ══════════════════════════════════════════════════════════════════════


async def test_send_emergency_sms_fires_on_expired_timer(
    db_session, redis_client
):
    """Timer expiré (expires_at_utc < now) → SMS envoyé + clé supprimée."""
    user_id = uuid4()
    expired_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    payload = {
        "user_id": str(user_id),
        "user_name": "Ama",
        "contact_phone": "+22899000000",
        "contact_name": "Maman",
        "started_at": (expired_at - timedelta(hours=3)).isoformat(),
        "expires_at": expired_at.isoformat(),
        "expires_at_utc": expired_at.isoformat(),
        "meeting_place": "Café 21",
    }
    key = TIMER_KEY.format(user_id=str(user_id))
    await redis_client.set(key, json.dumps(payload), ex=3600)

    mock_send = AsyncMock(
        return_value={"message_id": "sim-1", "provider": "simulated"}
    )
    with patch(
        "app.tasks.emergency_tasks.sms_service.send_text", mock_send
    ):
        result = await _send_emergency_sms_async(
            db_session, redis_client
        )

    assert result["sent"] == 1
    assert result["errors"] == 0
    mock_send.assert_called_once()
    # Canal WhatsApp
    assert mock_send.call_args.kwargs.get("channel") == "whatsapp"
    # Clé supprimée après envoi
    assert await redis_client.get(key) is None


# ══════════════════════════════════════════════════════════════════════
# beat_schedule
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="session")
async def test_beat_schedule_has_all_tasks():
    """Les tâches planifiées Session 12 sont bien enregistrées."""
    from app import celeryconfig

    schedule = celeryconfig.beat_schedule
    assert len(schedule) >= 15
    expected = {
        "generate-all-feeds",
        "persist-behavior-scores",
        "release-waitlist-batch",
        "event-reminder",
        "event-status-updater",
        "weekly-event-digest",
        "check-expired-subscriptions",
        "send-reply-reminders",
        "send-emergency-sms",
        "compute-daily-kpis",
        "purge-expired-matches",
        "purge-old-behavior-logs",
        "purge-old-feed-caches",
        "cleanup-account-histories",
        "compute-scam-risk-batch",
    }
    assert expected.issubset(set(schedule.keys()))
    # Chaque entrée a bien task + schedule
    for name, entry in schedule.items():
        assert "task" in entry, name
        assert "schedule" in entry, name
