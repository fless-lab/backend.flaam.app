from __future__ import annotations

"""
Tests FlaamError (§21 Session 11).

Valide :
- ``FlaamError`` porte code, status_code et message traduit.
- Le handler HTTP rend ``{"error": code, "message": ...}``.
- AppException continue de rendre ``{"detail": code}`` (coexistence).
- Integration via Accept-Language : EN et FR reels sur un endpoint.
"""

import pytest

from app.core.errors import FlaamError
from app.core.i18n import MESSAGES


def test_flaam_error_holds_code_status_and_message():
    exc = FlaamError("daily_likes_exhausted", 429, "fr", limit=5)
    assert exc.code == "daily_likes_exhausted"
    assert exc.status_code == 429
    assert "5" in exc.message
    assert exc.message == MESSAGES["daily_likes_exhausted"]["fr"].format(limit=5)


def test_flaam_error_translates_en():
    exc = FlaamError("daily_likes_exhausted", 429, "en", limit=5)
    assert "5" in exc.message
    assert exc.message == MESSAGES["daily_likes_exhausted"]["en"].format(limit=5)


def test_flaam_error_falls_back_to_fr_on_unknown_lang():
    exc = FlaamError("gender_not_modifiable", 400, "xx")
    assert exc.message == MESSAGES["gender_not_modifiable"]["fr"]


def test_flaam_error_unknown_code_returns_raw_key():
    exc = FlaamError("no_such_code_123", 400, "fr")
    assert exc.message == "no_such_code_123"


@pytest.mark.asyncio(loop_scope="session")
async def test_flaam_error_response_shape_fr(client, auth_headers):
    """Integration FR : gender_not_modifiable renvoie {error, message} FR."""
    payload = {
        "display_name": "Ama",
        "birth_date": "2000-03-15",
        "gender": "woman",
        "seeking_gender": "men",
        "intention": "serious",
        "sector": "finance",
    }
    await client.put("/profiles/me", json=payload, headers=auth_headers)

    payload["gender"] = "man"
    resp = await client.put(
        "/profiles/me",
        json=payload,
        headers={**auth_headers, "Accept-Language": "fr-FR"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "gender_not_modifiable"
    assert body["message"] == MESSAGES["gender_not_modifiable"]["fr"]


@pytest.mark.asyncio(loop_scope="session")
async def test_flaam_error_response_shape_en(client, auth_headers):
    """Integration EN : le meme endpoint en anglais via Accept-Language."""
    payload = {
        "display_name": "Ama",
        "birth_date": "2000-03-15",
        "gender": "woman",
        "seeking_gender": "men",
        "intention": "serious",
        "sector": "finance",
    }
    await client.put("/profiles/me", json=payload, headers=auth_headers)

    payload["gender"] = "man"
    resp = await client.put(
        "/profiles/me",
        json=payload,
        headers={**auth_headers, "Accept-Language": "en-US,en;q=0.9"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "gender_not_modifiable"
    assert body["message"] == MESSAGES["gender_not_modifiable"]["en"]


@pytest.mark.asyncio(loop_scope="session")
async def test_app_exception_still_uses_detail(client, auth_headers):
    """AppException (non migre) continue de renvoyer {detail}."""
    # profile_not_created est toujours AppException (non migre).
    resp = await client.get("/profiles/me", headers=auth_headers)
    assert resp.status_code == 404
    body = resp.json()
    assert body.get("detail") == "profile_not_created"
    # Pas de champ "error" (ancien format)
    assert "error" not in body
