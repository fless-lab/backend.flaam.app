from __future__ import annotations

"""
Tests Messages REST (§5.8, S7).

Utilise les helpers existants de tests/_feed_setup.py (Ama/Kofi + match).
"""

import io
from datetime import date, timedelta, time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.match import Match
from app.models.message import Message
from app.services.matching_engine import geo_scorer
from tests._feed_setup import (
    attach_quartier,
    attach_spot,
    headers_for,
    make_user,
    seed_ama_and_kofi,
    seed_city_lome,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture(autouse=True)
def _reset_geo_cache():
    geo_scorer.reset_proximity_cache()
    yield
    geo_scorer.reset_proximity_cache()


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════


async def _mutual_match(client, ama, kofi) -> str:
    await client.post(f"/feed/{kofi.id}/like", json={}, headers=headers_for(ama))
    r = await client.post(f"/feed/{ama.id}/like", json={}, headers=headers_for(kofi))
    assert r.status_code == 200, r.text
    return r.json()["match_id"]


# ══════════════════════════════════════════════════════════════════════
# POST /messages/{match_id}
# ══════════════════════════════════════════════════════════════════════


async def test_send_message_success(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match_id = await _mutual_match(client, ama, kofi)

    resp = await client.post(
        f"/messages/{match_id}",
        json={"content": "Salut Kofi", "client_message_id": "cmid-1"},
        headers=headers_for(ama),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["content"] == "Salut Kofi"
    assert body["message_type"] == "text"
    assert body["status"] in ("sent", "delivered")
    assert body["client_message_id"] == "cmid-1"
    assert body["sender_id"] == str(ama.id)


async def test_send_message_deduplicate(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match_id = await _mutual_match(client, ama, kofi)

    payload = {"content": "Coucou", "client_message_id": "cmid-dup"}
    responses = []
    for _ in range(3):
        r = await client.post(
            f"/messages/{match_id}", json=payload, headers=headers_for(ama)
        )
        assert r.status_code == 201
        responses.append(r.json())

    # Tous renvoient le même message_id
    ids = {r["id"] for r in responses}
    assert len(ids) == 1

    # DB : 1 seule row pour ce client_message_id
    db_session.expire_all()
    rows = await db_session.execute(
        select(Message).where(Message.client_message_id == "cmid-dup")
    )
    assert len(rows.scalars().all()) == 1


async def test_send_message_blocks_insult(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match_id = await _mutual_match(client, ama, kofi)

    resp = await client.post(
        f"/messages/{match_id}",
        json={"content": "T'es une salope", "client_message_id": "cmid-x"},
        headers=headers_for(ama),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"] == "insult"
    assert body.get("user_message_fr")


async def test_send_message_blocks_link_first_message(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match_id = await _mutual_match(client, ama, kofi)

    resp = await client.post(
        f"/messages/{match_id}",
        json={
            "content": "Clique ici https://evil.xyz/promo",
            "client_message_id": "cmid-link",
        },
        headers=headers_for(ama),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"].startswith("suspicious_link")


async def test_send_message_flags_money_request(client, db_session, redis_client):
    """Le message passe (201) mais is_flagged=True en DB."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match_id = await _mutual_match(client, ama, kofi)

    resp = await client.post(
        f"/messages/{match_id}",
        json={
            "content": "Envoie moi 5000 FCFA sur orange money stp",
            "client_message_id": "cmid-m",
        },
        headers=headers_for(ama),
    )
    assert resp.status_code == 201

    # En DB, le message est flaggé
    db_session.expire_all()
    row = await db_session.execute(
        select(Message).where(Message.client_message_id == "cmid-m")
    )
    msg = row.scalar_one()
    assert msg.is_flagged is True
    assert msg.flag_reason == "potential_scam"


async def test_user_cannot_read_other_match(client, db_session, redis_client):
    """Un user non-participant reçoit 404 (pas 403) sur GET /messages/{match_id}."""
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match_id = await _mutual_match(client, ama, kofi)

    # Crée un 3e user (Mia) non participante.
    base = {
        "city_id": ama.city_id,
    }
    mia = await make_user(
        db_session,
        phone="+22890000099",
        city_id=base["city_id"],
        display_name="Mia",
        gender="woman",
        seeking="men",
        birth_year=2000,
    )
    await db_session.commit()

    resp = await client.get(f"/messages/{match_id}", headers=headers_for(mia))
    assert resp.status_code == 404

    resp2 = await client.post(
        f"/messages/{match_id}",
        json={"content": "hack", "client_message_id": "cmid-hack"},
        headers=headers_for(mia),
    )
    assert resp2.status_code == 404


async def test_header_idempotency_must_match_body(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match_id = await _mutual_match(client, ama, kofi)

    resp = await client.post(
        f"/messages/{match_id}",
        json={"content": "Hi", "client_message_id": "cmid-a"},
        headers={
            **headers_for(ama),
            "X-Idempotency-Key": "cmid-b",  # mismatch
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "idempotency_key_mismatch"


# ══════════════════════════════════════════════════════════════════════
# GET /messages/{match_id} (pagination cursor)
# ══════════════════════════════════════════════════════════════════════


async def test_pagination_cursor(client, db_session, redis_client):
    import asyncio

    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match_id = await _mutual_match(client, ama, kofi)

    # Envoie 10 messages. Petit sleep pour garantir des created_at distincts
    # (résolution postgres = microseconde, un tri stable avec id DESC reste
    # fragile pour des UUIDs aléatoires).
    for i in range(10):
        r = await client.post(
            f"/messages/{match_id}",
            json={"content": f"m{i}", "client_message_id": f"cmid-{i}"},
            headers=headers_for(ama),
        )
        assert r.status_code == 201
        await asyncio.sleep(0.01)

    # Page 1 : 5 plus récents
    r1 = await client.get(
        f"/messages/{match_id}?limit=5", headers=headers_for(ama)
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert len(body1["messages"]) == 5
    assert body1["has_more"] is True
    assert body1["next_cursor"] is not None
    # DESC : le 1er est m9
    assert body1["messages"][0]["content"] == "m9"
    assert body1["messages"][4]["content"] == "m5"

    # Page 2 avec cursor — passer via params pour URL-encoder le "+" du
    # timezone offset ISO-8601.
    r2 = await client.get(
        f"/messages/{match_id}",
        params={"limit": 5, "cursor": body1["next_cursor"]},
        headers=headers_for(ama),
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert len(body2["messages"]) == 5
    # m4 ... m0
    contents = [m["content"] for m in body2["messages"]]
    assert contents == ["m4", "m3", "m2", "m1", "m0"]
    assert body2["has_more"] is False


# ══════════════════════════════════════════════════════════════════════
# PATCH /messages/{match_id}/read + unread count
# ══════════════════════════════════════════════════════════════════════


async def test_mark_read_updates_status(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match_id = await _mutual_match(client, ama, kofi)

    # Kofi envoie 3 messages à Ama
    ids = []
    for i in range(3):
        r = await client.post(
            f"/messages/{match_id}",
            json={"content": f"k{i}", "client_message_id": f"kc-{i}"},
            headers=headers_for(kofi),
        )
        assert r.status_code == 201
        ids.append(r.json()["id"])

    # Ama marque lu jusqu'au 2e
    r = await client.patch(
        f"/messages/{match_id}/read",
        json={"last_read_message_id": ids[1]},
        headers=headers_for(ama),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["updated_count"] == 2

    # unread_count = 1
    r2 = await client.get(
        f"/messages/{match_id}/unread-count", headers=headers_for(ama)
    )
    assert r2.status_code == 200
    assert r2.json()["unread_count"] == 1

    # En DB, status="read" sur les 2 premiers
    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(Message).where(Message.id.in_(ids[:2]))
        )
    ).scalars().all()
    assert all(m.status == "read" for m in rows)
    assert all(m.read_at is not None for m in rows)


async def test_unread_count_zero_after_send_and_read(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match_id = await _mutual_match(client, ama, kofi)

    # Aucun message → 0
    r = await client.get(
        f"/messages/{match_id}/unread-count", headers=headers_for(ama)
    )
    assert r.status_code == 200
    assert r.json()["unread_count"] == 0


# ══════════════════════════════════════════════════════════════════════
# Meetup
# ══════════════════════════════════════════════════════════════════════


async def test_meetup_proposal_and_accept(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    cafe = data["spots"]["cafe21"]
    match_id = await _mutual_match(client, ama, kofi)

    # Ama propose
    tomorrow = (date.today() + timedelta(days=2)).isoformat()
    r = await client.post(
        f"/messages/{match_id}/meetup",
        json={
            "spot_id": str(cafe.id),
            "proposed_date": tomorrow,
            "proposed_time": "18:30",
            "note": "Ça te dit ?",
            "client_message_id": "meet-1",
        },
        headers=headers_for(ama),
    )
    assert r.status_code == 201, r.text
    msg = r.json()
    assert msg["message_type"] == "meetup"
    assert msg["meetup_data"]["status"] == "proposed"
    assert msg["meetup_data"]["spot_id"] == str(cafe.id)

    # Kofi accepte
    r2 = await client.patch(
        f"/messages/{msg['id']}/meetup",
        json={"action": "accept"},
        headers=headers_for(kofi),
    )
    assert r2.status_code == 200
    assert r2.json()["meetup_data"]["status"] == "accepted"


async def test_meetup_proposal_refuse(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    cafe = data["spots"]["cafe21"]
    match_id = await _mutual_match(client, ama, kofi)

    tomorrow = (date.today() + timedelta(days=3)).isoformat()
    r = await client.post(
        f"/messages/{match_id}/meetup",
        json={
            "spot_id": str(cafe.id),
            "proposed_date": tomorrow,
            "proposed_time": "19:00",
            "note": None,
            "client_message_id": "meet-2",
        },
        headers=headers_for(ama),
    )
    assert r.status_code == 201
    msg = r.json()

    # Kofi refuse
    r2 = await client.patch(
        f"/messages/{msg['id']}/meetup",
        json={"action": "refuse"},
        headers=headers_for(kofi),
    )
    assert r2.status_code == 200
    assert r2.json()["meetup_data"]["status"] == "refused"


async def test_meetup_proposer_cannot_respond_own(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    cafe = data["spots"]["cafe21"]
    match_id = await _mutual_match(client, ama, kofi)

    tomorrow = (date.today() + timedelta(days=2)).isoformat()
    r = await client.post(
        f"/messages/{match_id}/meetup",
        json={
            "spot_id": str(cafe.id),
            "proposed_date": tomorrow,
            "proposed_time": "18:30",
            "note": None,
            "client_message_id": "meet-3",
        },
        headers=headers_for(ama),
    )
    msg = r.json()

    # Ama essaie de s'accepter elle-même
    r2 = await client.patch(
        f"/messages/{msg['id']}/meetup",
        json={"action": "accept"},
        headers=headers_for(ama),
    )
    assert r2.status_code == 403


# ══════════════════════════════════════════════════════════════════════
# Voice
# ══════════════════════════════════════════════════════════════════════


async def test_voice_upload(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama, kofi = data["ama"], data["kofi"]
    match_id = await _mutual_match(client, ama, kofi)

    fake_audio = b"OggS\x00\x02" + b"\x00" * 128
    files = {"file": ("voice.webm", io.BytesIO(fake_audio), "audio/webm")}
    data_form = {"client_message_id": "voice-1"}
    r = await client.post(
        f"/messages/{match_id}/voice",
        data=data_form,
        files=files,
        headers=headers_for(ama),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["message_type"] == "voice"
    assert body["media_url"] is not None
    assert body["client_message_id"] == "voice-1"
