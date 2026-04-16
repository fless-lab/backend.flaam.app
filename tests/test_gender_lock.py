from __future__ import annotations

"""
Tests gender lock (§CLAUDE.md sécurité).

Le genre est déclaré à l'onboarding, puis verrouillé. Seul un admin
peut le changer via PATCH /admin/users/{id}/gender (ce qui invalide
le selfie). Ce test couvre uniquement le lock user-side : l'admin path
est testé dans test_admin.py::test_admin_change_user_gender_resets_selfie.
"""

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _base_payload() -> dict:
    return {
        "display_name": "Ama",
        "birth_date": "2000-03-15",
        "gender": "woman",
        "seeking_gender": "men",
        "intention": "serious",
        "sector": "finance",
    }


async def test_gender_lock_rejects_change_via_user_endpoint(client, auth_headers):
    """Après création du profile, PUT /profiles/me avec un gender
    différent → 400 gender_not_modifiable."""
    r1 = await client.put(
        "/profiles/me", json=_base_payload(), headers=auth_headers
    )
    assert r1.status_code == 200, r1.text

    payload = _base_payload()
    payload["gender"] = "man"
    r2 = await client.put("/profiles/me", json=payload, headers=auth_headers)
    assert r2.status_code == 400
    assert r2.json()["detail"] == "gender_not_modifiable"


async def test_gender_lock_allows_same_value(client, auth_headers):
    """Renvoyer le MÊME gender est OK (idempotent)."""
    r1 = await client.put(
        "/profiles/me", json=_base_payload(), headers=auth_headers
    )
    assert r1.status_code == 200

    # Même gender, update d'un autre champ
    payload = _base_payload()
    payload["sector"] = "tech"
    r2 = await client.put("/profiles/me", json=payload, headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["sector"] == "tech"
    assert r2.json()["gender"] == "woman"


async def test_gender_set_on_initial_creation_is_allowed(client, auth_headers):
    """La première PUT (création) doit accepter gender — seul le CHANGE est bloqué."""
    payload = _base_payload()
    payload["gender"] = "non_binary"
    r = await client.put("/profiles/me", json=payload, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["gender"] == "non_binary"
