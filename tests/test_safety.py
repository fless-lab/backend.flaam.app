from __future__ import annotations

"""Tests Safety (§5.11, Session 9)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.account_history import AccountHistory
from app.models.block import Block
from app.models.report import Report
from tests._feed_setup import headers_for, seed_ama_and_kofi

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ══════════════════════════════════════════════════════════════════════
# POST /safety/report
# ══════════════════════════════════════════════════════════════════════


async def test_report_user_creates_report(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    resp = await client.post(
        "/safety/report",
        json={
            "reported_user_id": str(kofi.id),
            "reason": "harassment",
            "description": "Messages insistants",
        },
        headers=headers_for(ama),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Status peut être pending, flagged_for_review ou auto_banned selon
    # le score de scam — dans ce scénario clean, attendu = pending.
    assert body["status"] in (
        "pending",
        "flagged_for_review",
        "auto_banned",
    )

    rows = await db_session.execute(
        select(Report).where(Report.reported_user_id == kofi.id)
    )
    reports = rows.scalars().all()
    assert len(reports) == 1
    assert reports[0].reason == "harassment"


async def test_report_self_forbidden(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    resp = await client.post(
        "/safety/report",
        json={
            "reported_user_id": str(ama.id),
            "reason": "other",
        },
        headers=headers_for(ama),
    )
    assert resp.status_code == 400


# ══════════════════════════════════════════════════════════════════════
# POST /safety/block + DELETE /safety/block/{id}
# ══════════════════════════════════════════════════════════════════════


async def test_block_user_creates_entry_and_updates_history(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    resp = await client.post(
        "/safety/block",
        json={"blocked_user_id": str(kofi.id)},
        headers=headers_for(ama),
    )
    assert resp.status_code == 201, resp.text

    # Block DB
    blocks = await db_session.execute(
        select(Block).where(
            Block.blocker_id == ama.id, Block.blocked_id == kofi.id
        )
    )
    assert blocks.scalar_one() is not None

    # AccountHistory du bloqué
    hist_row = await db_session.execute(
        select(AccountHistory).where(
            AccountHistory.phone_hash == kofi.phone_hash
        )
    )
    hist = hist_row.scalar_one_or_none()
    assert hist is not None
    assert hist.blocked_by_count == 1
    assert ama.phone_hash in hist.blocked_by_hashes


async def test_block_is_idempotent(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    for _ in range(2):
        resp = await client.post(
            "/safety/block",
            json={"blocked_user_id": str(kofi.id)},
            headers=headers_for(ama),
        )
        assert resp.status_code == 201

    blocks = await db_session.execute(
        select(Block).where(
            Block.blocker_id == ama.id, Block.blocked_id == kofi.id
        )
    )
    assert len(blocks.scalars().all()) == 1

    hist_row = await db_session.execute(
        select(AccountHistory).where(
            AccountHistory.phone_hash == kofi.phone_hash
        )
    )
    hist = hist_row.scalar_one()
    assert hist.blocked_by_count == 1  # pas de double incrément


async def test_unblock_user_removes_entry(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]

    await client.post(
        "/safety/block",
        json={"blocked_user_id": str(kofi.id)},
        headers=headers_for(ama),
    )
    resp = await client.delete(
        f"/safety/block/{kofi.id}", headers=headers_for(ama)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "unblocked"

    blocks = await db_session.execute(
        select(Block).where(
            Block.blocker_id == ama.id, Block.blocked_id == kofi.id
        )
    )
    assert blocks.scalar_one_or_none() is None


# ══════════════════════════════════════════════════════════════════════
# POST /safety/share-date (SMS mocké)
# ══════════════════════════════════════════════════════════════════════


async def test_share_date_calls_sms_service(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    mock_send = AsyncMock(
        return_value={"message_id": "sim-123", "provider": "simulated"}
    )
    with patch(
        "app.services.safety_service.sms_service.send_text", mock_send
    ):
        resp = await client.post(
            "/safety/share-date",
            json={
                "contact_phone": "+22899000000",
                "contact_name": "Maman",
                "partner_name": "Kofi",
                "meeting_place": "Café 21",
                "meeting_time": (
                    datetime.now(timezone.utc) + timedelta(hours=3)
                ).isoformat(),
            },
            headers=headers_for(ama),
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "sent"
    assert body["provider_message_id"] == "sim-123"
    mock_send.assert_called_once()
    # Vérifie le canal WhatsApp (primary pour les messages libres §35)
    assert mock_send.call_args.kwargs.get("channel") == "whatsapp"


# ══════════════════════════════════════════════════════════════════════
# Emergency timer
# ══════════════════════════════════════════════════════════════════════


async def test_emergency_timer_armed_sets_redis(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    resp = await client.post(
        "/safety/emergency",
        json={
            "contact_phone": "+22899000000",
            "contact_name": "Maman",
            "hours": 3,
            "latitude": 6.135,
            "longitude": 1.221,
            "meeting_place": "Café 21",
        },
        headers=headers_for(ama),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "armed"

    # La clé Redis existe avec un TTL > 0. Depuis S12 le TTL inclut 24h
    # de grâce pour que le task send_emergency_sms puisse lire la clé
    # APRÈS expiration logique → TTL max = timer_seconds + 86400.
    key = f"safety:timer:{ama.id}"
    ttl = await redis_client.ttl(key)
    assert ttl > 0
    assert ttl <= 3 * 3600 + 86400


async def test_emergency_timer_cancel_clears_redis(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    await client.post(
        "/safety/emergency",
        json={
            "contact_phone": "+22899000000",
            "hours": 3,
        },
        headers=headers_for(ama),
    )
    resp = await client.post(
        "/safety/timer/cancel", headers=headers_for(ama)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    key = f"safety:timer:{ama.id}"
    assert await redis_client.get(key) is None


async def test_emergency_timer_cancel_when_none(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]
    resp = await client.post(
        "/safety/timer/cancel", headers=headers_for(ama)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_active_timer"


# ══════════════════════════════════════════════════════════════════════
# S12.5 — Emergency timer avec contact_ids
# ══════════════════════════════════════════════════════════════════════


async def _create_contact(client, user, name, phone) -> str:
    resp = await client.post(
        "/safety/contacts",
        json={"name": name, "phone": phone},
        headers=headers_for(user),
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_timer_with_contact_ids(client, db_session, redis_client):
    """Timer avec contact_ids → contacts copiés en BD vers Redis."""
    import json

    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    c1 = await _create_contact(client, ama, "K1", "+22890111111")
    c2 = await _create_contact(client, ama, "K2", "+22890222222")

    resp = await client.post(
        "/safety/emergency",
        json={
            "hours": 3,
            "contact_ids": [c1, c2],
            "meeting_place": "Café 21",
        },
        headers=headers_for(ama),
    )
    assert resp.status_code == 201, resp.text

    # Vérifier le JSON Redis : 2 contacts copiés
    raw = await redis_client.get(f"safety:timer:{ama.id}")
    payload = json.loads(raw)
    assert len(payload["contacts"]) == 2
    phones = {c["phone"] for c in payload["contacts"]}
    assert phones == {"+22890111111", "+22890222222"}


async def test_timer_max_2_contacts_per_timer(
    client, db_session, redis_client
):
    """contact_ids avec 3 entrées → max_length Pydantic 422."""
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    c1 = await _create_contact(client, ama, "K1", "+22890111111")
    c2 = await _create_contact(client, ama, "K2", "+22890222222")
    c3 = await _create_contact(client, ama, "K3", "+22890333333")

    resp = await client.post(
        "/safety/emergency",
        json={"hours": 3, "contact_ids": [c1, c2, c3]},
        headers=headers_for(ama),
    )
    # max_length sur contact_ids → 422 côté Pydantic
    assert resp.status_code == 422


async def test_timer_sends_to_multiple_contacts(
    client, db_session, redis_client
):
    """Timer expiré avec 2 contacts → 2 appels send_text."""
    import json
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, patch

    from app.tasks.emergency_tasks import _send_emergency_sms_async

    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    c1 = await _create_contact(client, ama, "K1", "+22890111111")
    c2 = await _create_contact(client, ama, "K2", "+22890222222")

    # Arme le timer avec 2 contacts
    resp = await client.post(
        "/safety/emergency",
        json={"hours": 3, "contact_ids": [c1, c2]},
        headers=headers_for(ama),
    )
    assert resp.status_code == 201

    # Force expires_at_utc dans le passé
    key = f"safety:timer:{ama.id}"
    raw = await redis_client.get(key)
    payload = json.loads(raw)
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    payload["expires_at_utc"] = past.isoformat()
    await redis_client.set(key, json.dumps(payload), ex=3600)

    mock_send = AsyncMock(
        return_value={"message_id": "sim", "provider": "simulated"}
    )
    with patch(
        "app.tasks.emergency_tasks.sms_service.send_text", mock_send
    ):
        result = await _send_emergency_sms_async(db_session, redis_client)

    assert result["sent"] == 2
    assert mock_send.call_count == 2


# ══════════════════════════════════════════════════════════════════════
# PATCH /safety/timer/location
# ══════════════════════════════════════════════════════════════════════


async def test_timer_location_update(client, db_session, redis_client):
    import json

    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    await client.post(
        "/safety/emergency",
        json={"hours": 3, "contact_phone": "+22890111111"},
        headers=headers_for(ama),
    )

    resp = await client.patch(
        "/safety/timer/location",
        json={"latitude": 6.17, "longitude": -1.23},
        headers=headers_for(ama),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "updated"

    raw = await redis_client.get(f"safety:timer:{ama.id}")
    payload = json.loads(raw)
    assert payload["latitude"] == 6.17
    assert payload["longitude"] == -1.23
    assert payload["location_updated_at"] is not None


async def test_timer_location_update_no_timer_404(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    resp = await client.patch(
        "/safety/timer/location",
        json={"latitude": 6.17, "longitude": -1.23},
        headers=headers_for(ama),
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "no_active_timer"


# ══════════════════════════════════════════════════════════════════════
# POST /safety/timer/extend
# ══════════════════════════════════════════════════════════════════════


async def test_timer_extend_adds_hours(client, db_session, redis_client):
    import json
    from datetime import datetime, timezone

    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    await client.post(
        "/safety/emergency",
        json={"hours": 1, "contact_phone": "+22890111111"},
        headers=headers_for(ama),
    )

    raw_before = await redis_client.get(f"safety:timer:{ama.id}")
    exp_before = datetime.fromisoformat(
        json.loads(raw_before)["expires_at_utc"]
    )
    # Simule qu'on était à 14 min de l'expiration → warned posé
    await redis_client.set(
        f"safety:timer:warned:{ama.id}", "1", ex=1800
    )

    resp = await client.post(
        "/safety/timer/extend",
        json={"extra_hours": 1},
        headers=headers_for(ama),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "extended"

    raw_after = await redis_client.get(f"safety:timer:{ama.id}")
    exp_after = datetime.fromisoformat(
        json.loads(raw_after)["expires_at_utc"]
    )
    # +3600s (à ~1s près)
    delta = (exp_after - exp_before).total_seconds()
    assert 3599 <= delta <= 3601

    # Le flag "warned" a été supprimé → réarme la notif 15 min
    assert await redis_client.get(f"safety:timer:warned:{ama.id}") is None


async def test_timer_extend_no_timer_404(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]
    resp = await client.post(
        "/safety/timer/extend",
        json={"extra_hours": 1},
        headers=headers_for(ama),
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "no_active_timer"


# ══════════════════════════════════════════════════════════════════════
# POST /safety/emergency/trigger (bouton panique)
# ══════════════════════════════════════════════════════════════════════


async def test_emergency_trigger_with_active_timer(
    client, db_session, redis_client
):
    """Timer actif + panic → SMS à tous les contacts immédiatement."""
    from unittest.mock import AsyncMock, patch

    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    c1 = await _create_contact(client, ama, "K1", "+22890111111")
    c2 = await _create_contact(client, ama, "K2", "+22890222222")

    await client.post(
        "/safety/emergency",
        json={"hours": 3, "contact_ids": [c1, c2]},
        headers=headers_for(ama),
    )

    mock_send = AsyncMock(
        return_value={"message_id": "sim", "provider": "simulated"}
    )
    with patch(
        "app.services.safety_service.sms_service.send_text", mock_send
    ):
        resp = await client.post(
            "/safety/emergency/trigger",
            json={"latitude": 6.17, "longitude": -1.23},
            headers=headers_for(ama),
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "alert_sent"
    assert body["contacts_notified"] == 2
    assert mock_send.call_count == 2

    # Timer consommé (clé supprimée)
    assert await redis_client.get(f"safety:timer:{ama.id}") is None


async def test_emergency_trigger_without_timer_uses_primary(
    client, db_session, redis_client
):
    """Sans timer actif, le SMS part au contact primary."""
    from unittest.mock import AsyncMock, patch

    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    await _create_contact(client, ama, "Primary", "+22890999999")

    mock_send = AsyncMock(
        return_value={"message_id": "sim", "provider": "simulated"}
    )
    with patch(
        "app.services.safety_service.sms_service.send_text", mock_send
    ):
        resp = await client.post(
            "/safety/emergency/trigger",
            json={"latitude": 6.17, "longitude": -1.23},
            headers=headers_for(ama),
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["contacts_notified"] == 1
    mock_send.assert_called_once()
    # Premier arg positionnel = phone du primary
    assert mock_send.call_args.args[0] == "+22890999999"


# ══════════════════════════════════════════════════════════════════════
# Validations hours min/max
# ══════════════════════════════════════════════════════════════════════


async def test_timer_hours_min_validation(
    client, db_session, redis_client
):
    """hours < 0.5 → 422 côté Pydantic."""
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    resp = await client.post(
        "/safety/emergency",
        json={"hours": 0.1, "contact_phone": "+22890111111"},
        headers=headers_for(ama),
    )
    assert resp.status_code == 422


async def test_timer_hours_max_validation(
    client, db_session, redis_client
):
    """hours > 12 → 422 côté Pydantic."""
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    resp = await client.post(
        "/safety/emergency",
        json={"hours": 24, "contact_phone": "+22890111111"},
        headers=headers_for(ama),
    )
    assert resp.status_code == 422


# ══════════════════════════════════════════════════════════════════════
# SMS content — lien Google Maps (S12.5 BLOC F)
# ══════════════════════════════════════════════════════════════════════


async def test_emergency_sms_contains_maps_link(
    client, db_session, redis_client
):
    """Timer expiré avec lat/lng → le SMS contient l'URL Google Maps."""
    import json
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, patch

    from app.tasks.emergency_tasks import _send_emergency_sms_async

    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    await client.post(
        "/safety/emergency",
        json={
            "hours": 1,
            "contact_phone": "+22890111111",
            "latitude": 6.17,
            "longitude": -1.23,
        },
        headers=headers_for(ama),
    )

    # Force expiration
    key = f"safety:timer:{ama.id}"
    raw = await redis_client.get(key)
    payload = json.loads(raw)
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    payload["expires_at_utc"] = past.isoformat()
    await redis_client.set(key, json.dumps(payload), ex=3600)

    mock_send = AsyncMock(
        return_value={"message_id": "sim", "provider": "simulated"}
    )
    with patch(
        "app.tasks.emergency_tasks.sms_service.send_text", mock_send
    ):
        await _send_emergency_sms_async(db_session, redis_client)

    assert mock_send.call_count == 1
    text = mock_send.call_args.args[1]
    assert "https://maps.google.com/maps?q=6.17,-1.23" in text
    # Age de la position : "il y a X min" (position fraîche)
    assert "il y a" in text
