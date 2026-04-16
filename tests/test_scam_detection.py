from __future__ import annotations

"""Tests scam detection service (§39, Session 9)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models.match import Match
from app.models.message import Message
from app.models.photo import Photo
from app.models.report import Report
from app.services import scam_detection_service
from tests._feed_setup import make_user, seed_ama_and_kofi

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_clean_profile_has_low_score(db_session, redis_client):
    """Profil normal (2 mois, pas de reports, pas de messages) = 0."""
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    score = await scam_detection_service.compute_scam_risk(
        ama.id, db_session
    )
    assert score == 0.0


async def test_profile_too_perfect_detected(db_session, redis_client):
    """Compte < 24h + completeness 1.0 + 3 photos → signal actif."""
    base = await seed_ama_and_kofi(db_session)
    city = base["city"]

    # On crée un user "trop parfait" : très récent (1h) + completeness = 1.0
    # + déjà 3 photos — suspect pour un compte si jeune.
    suspect = await make_user(
        db_session,
        phone="+22899500500",
        city_id=city.id,
        display_name="Suspect",
        completeness=1.0,
        photos_count=3,
        account_age_days=0,
    )
    # Force created_at récent (override le -0d qui peut varier)
    suspect.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await db_session.flush()
    await db_session.commit()

    score = await scam_detection_service.compute_scam_risk(
        suspect.id, db_session
    )
    # Au minimum le signal profile_too_perfect (0.20) doit avoir tiré.
    assert score >= scam_detection_service.WEIGHTS["profile_too_perfect"]


async def test_immediate_money_detected(db_session, redis_client):
    """Mot-clé 'envoie' dans un des 3 premiers messages → signal actif."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    match = Match(
        id=uuid4(),
        user_a_id=ama.id,
        user_b_id=kofi.id,
        status="matched",
    )
    db_session.add(match)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add_all([
        Message(
            match_id=match.id,
            sender_id=ama.id,
            message_type="text",
            content="Salut !",
            created_at=now,
        ),
        Message(
            match_id=match.id,
            sender_id=ama.id,
            message_type="text",
            content="Envoie-moi 5000 FCFA urgence hopital",
            created_at=now + timedelta(seconds=5),
        ),
    ])
    await db_session.commit()

    score = await scam_detection_service.compute_scam_risk(
        ama.id, db_session
    )
    assert score >= scam_detection_service.WEIGHTS["immediate_money"]


async def test_report_count_threshold(db_session, redis_client):
    """> 3 reports reçus → signal report_count actif."""
    data = await seed_ama_and_kofi(db_session)
    _ama, kofi = data["ama"], data["kofi"]
    base = {"city": data["city"]}

    # Crée 4 reporters distincts qui reportent kofi
    reporters = []
    for i in range(4):
        u = await make_user(
            db_session,
            phone=f"+2289996000{i}",
            city_id=base["city"].id,
            display_name=f"Reporter{i}",
        )
        reporters.append(u)
    for r in reporters:
        db_session.add(
            Report(
                reporter_id=r.id,
                reported_user_id=kofi.id,
                reason="harassment",
            )
        )
    await db_session.commit()

    score = await scam_detection_service.compute_scam_risk(
        kofi.id, db_session
    )
    assert score >= scam_detection_service.WEIGHTS["report_count"]


async def test_unknown_user_returns_zero(db_session, redis_client):
    score = await scam_detection_service.compute_scam_risk(
        uuid4(), db_session
    )
    assert score == 0.0


async def test_score_capped_at_one(db_session, redis_client):
    """Score toujours borné à 1.0 même si tous les signaux tirent."""
    data = await seed_ama_and_kofi(db_session)
    kofi = data["kofi"]

    # Cumule plusieurs signaux : reports + immediate_money + link_spam
    for i in range(4):
        reporter = await make_user(
            db_session,
            phone=f"+22899601{i:03d}",
            city_id=data["city"].id,
            display_name=f"R{i}",
        )
        db_session.add(
            Report(
                reporter_id=reporter.id,
                reported_user_id=kofi.id,
                reason="scam",
            )
        )

    # Des messages avec money + links
    match = Match(
        id=uuid4(),
        user_a_id=kofi.id,
        user_b_id=data["ama"].id,
        status="matched",
    )
    db_session.add(match)
    await db_session.flush()
    db_session.add_all([
        Message(
            match_id=match.id,
            sender_id=kofi.id,
            message_type="text",
            content="Envoie urgence hopital momo transfert",
        ),
        Message(
            match_id=match.id,
            sender_id=kofi.id,
            message_type="text",
            content="Va sur https://scam.example ou whatsapp moi +22890000099",
        ),
    ])
    await db_session.commit()

    score = await scam_detection_service.compute_scam_risk(
        kofi.id, db_session
    )
    assert 0.0 <= score <= 1.0
