from __future__ import annotations

"""Tests Feature C — reply reminders (Session 9)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models.match import Match
from app.models.matching_config import MatchingConfig
from app.models.message import Message
from app.models.notification_preference import NotificationPreference
from app.services import reminder_service
from tests._feed_setup import seed_ama_and_kofi

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create_stale_match(db, ama, kofi, hours_ago: int = 30):
    """Match matched + dernier message de Kofi il y a N heures."""
    now = datetime.now(timezone.utc)
    match = Match(
        id=uuid4(),
        user_a_id=ama.id,
        user_b_id=kofi.id,
        status="matched",
        matched_at=now - timedelta(days=3),
    )
    db.add(match)
    await db.flush()
    msg = Message(
        match_id=match.id,
        sender_id=kofi.id,
        message_type="text",
        content="Salut ça va ?",
        created_at=now - timedelta(hours=hours_ago),
    )
    db.add(msg)
    await db.commit()
    return match


async def test_finds_pending_replies(db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match = await _create_stale_match(db_session, ama, kofi, hours_ago=30)

    candidates = await reminder_service.check_pending_replies(
        db_session, redis_client
    )
    match_ids = {c["match_id"] for c in candidates}
    assert match.id in match_ids

    cand = [c for c in candidates if c["match_id"] == match.id][0]
    # Le recipient = Ama (Kofi a écrit en dernier)
    assert cand["recipient_id"] == ama.id
    assert cand["partner_name"] == "Kofi"


async def test_respects_48h_cooldown(db_session, redis_client):
    """Après mark_reminder_sent, le match disparaît des candidats 48h."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match = await _create_stale_match(db_session, ama, kofi, hours_ago=30)

    await reminder_service.mark_reminder_sent(match.id, redis_client)

    candidates = await reminder_service.check_pending_replies(
        db_session, redis_client
    )
    assert match.id not in {c["match_id"] for c in candidates}


async def test_respects_reply_reminders_pref(db_session, redis_client):
    """Si le recipient a reply_reminders=False, pas de candidat."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match = await _create_stale_match(db_session, ama, kofi, hours_ago=30)

    db_session.add(
        NotificationPreference(
            user_id=ama.id,
            reply_reminders=False,
        )
    )
    await db_session.commit()

    candidates = await reminder_service.check_pending_replies(
        db_session, redis_client
    )
    assert match.id not in {c["match_id"] for c in candidates}


async def test_disabled_flag_returns_empty(db_session, redis_client):
    """Feature flag off → retourne [] immédiatement."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    await _create_stale_match(db_session, ama, kofi, hours_ago=30)

    db_session.add(
        MatchingConfig(
            key="flag_reply_reminders_enabled",
            value=0.0,
            category="flags",
        )
    )
    await db_session.commit()
    # Invalide le cache Redis pour que le nouveau 0.0 soit lu
    await redis_client.delete("matching:config:flag_reply_reminders_enabled")

    candidates = await reminder_service.check_pending_replies(
        db_session, redis_client
    )
    assert candidates == []


async def test_fresh_message_no_reminder(db_session, redis_client):
    """Dernier message < 24h → pas de reminder."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match = await _create_stale_match(db_session, ama, kofi, hours_ago=2)

    candidates = await reminder_service.check_pending_replies(
        db_session, redis_client
    )
    assert match.id not in {c["match_id"] for c in candidates}
