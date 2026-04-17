from __future__ import annotations

"""Tests Emergency Contacts CRUD (§S12.5)."""

import pytest

from tests._feed_setup import headers_for, seed_ama_and_kofi

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ══════════════════════════════════════════════════════════════════════
# POST /safety/contacts
# ══════════════════════════════════════════════════════════════════════


async def test_create_emergency_contact(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    resp = await client.post(
        "/safety/contacts",
        json={"name": "Kokou", "phone": "+22890111222"},
        headers=headers_for(ama),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Kokou"
    assert body["phone"] == "+22890111222"
    assert body["is_primary"] is True  # premier contact → auto-primary


async def test_first_contact_is_primary(client, db_session, redis_client):
    """Le premier contact enregistré est automatiquement marqué primary."""
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    # Créer 2 contacts
    r1 = await client.post(
        "/safety/contacts",
        json={"name": "Kokou", "phone": "+22890111222"},
        headers=headers_for(ama),
    )
    r2 = await client.post(
        "/safety/contacts",
        json={"name": "Akossiwa", "phone": "+22890333444"},
        headers=headers_for(ama),
    )
    assert r1.json()["is_primary"] is True
    assert r2.json()["is_primary"] is False


async def test_max_3_contacts_enforced(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    for i, phone in enumerate(
        ["+22890111111", "+22890222222", "+22890333333"]
    ):
        resp = await client.post(
            "/safety/contacts",
            json={"name": f"C{i}", "phone": phone},
            headers=headers_for(ama),
        )
        assert resp.status_code == 201

    # 4e contact rejeté
    resp = await client.post(
        "/safety/contacts",
        json={"name": "Trop", "phone": "+22890444444"},
        headers=headers_for(ama),
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "max_contacts_reached"


# ══════════════════════════════════════════════════════════════════════
# GET /safety/contacts
# ══════════════════════════════════════════════════════════════════════


async def test_list_contacts(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    await client.post(
        "/safety/contacts",
        json={"name": "K1", "phone": "+22890111111"},
        headers=headers_for(ama),
    )
    await client.post(
        "/safety/contacts",
        json={"name": "K2", "phone": "+22890222222"},
        headers=headers_for(ama),
    )

    resp = await client.get(
        "/safety/contacts", headers=headers_for(ama)
    )
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    names = {c["name"] for c in items}
    assert names == {"K1", "K2"}


# ══════════════════════════════════════════════════════════════════════
# DELETE /safety/contacts/{id}
# ══════════════════════════════════════════════════════════════════════


async def test_delete_contact_reassigns_primary(
    client, db_session, redis_client
):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    r1 = await client.post(
        "/safety/contacts",
        json={"name": "K1", "phone": "+22890111111"},
        headers=headers_for(ama),
    )
    r2 = await client.post(
        "/safety/contacts",
        json={"name": "K2", "phone": "+22890222222"},
        headers=headers_for(ama),
    )
    c1_id = r1.json()["id"]
    c2_id = r2.json()["id"]

    # Delete le primary (c1)
    resp = await client.delete(
        f"/safety/contacts/{c1_id}", headers=headers_for(ama)
    )
    assert resp.status_code == 204

    # c2 doit devenir primary
    resp = await client.get(
        "/safety/contacts", headers=headers_for(ama)
    )
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == c2_id
    assert items[0]["is_primary"] is True


# ══════════════════════════════════════════════════════════════════════
# PATCH /safety/contacts/{id}/primary
# ══════════════════════════════════════════════════════════════════════


async def test_set_primary(client, db_session, redis_client):
    data = await seed_ama_and_kofi(db_session)
    ama = data["ama"]

    r1 = await client.post(
        "/safety/contacts",
        json={"name": "K1", "phone": "+22890111111"},
        headers=headers_for(ama),
    )
    r2 = await client.post(
        "/safety/contacts",
        json={"name": "K2", "phone": "+22890222222"},
        headers=headers_for(ama),
    )
    c2_id = r2.json()["id"]
    # Au départ c1 est primary ; on promeut c2
    resp = await client.patch(
        f"/safety/contacts/{c2_id}/primary", headers=headers_for(ama)
    )
    assert resp.status_code == 200
    assert resp.json()["is_primary"] is True

    all_ = await client.get(
        "/safety/contacts", headers=headers_for(ama)
    )
    items = {c["id"]: c for c in all_.json()}
    assert items[r1.json()["id"]]["is_primary"] is False
    assert items[c2_id]["is_primary"] is True
