from __future__ import annotations

"""Tests Photos (§5.3)."""

import io
from pathlib import Path

import pytest
from PIL import Image

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _make_image_bytes(
    size: tuple[int, int] = (800, 1000),
    color: tuple[int, int, int] = (50, 120, 200),
    fmt: str = "JPEG",
) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


async def _upload(client, auth_headers, *, display_order=None, color=(50, 120, 200)):
    files = {"file": ("test.jpg", _make_image_bytes(color=color), "image/jpeg")}
    data = {}
    if display_order is not None:
        data["display_order"] = str(display_order)
    return await client.post(
        "/photos", files=files, data=data, headers=auth_headers
    )


async def test_upload_photo_success(client, auth_headers, isolated_storage):
    resp = await _upload(client, auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["display_order"] == 0
    assert body["moderation_status"] == "pending"
    assert body["width"] > 0 and body["height"] > 0
    assert body["file_size_bytes"] > 0
    assert body["dominant_color"].startswith("#")
    assert body["original_url"].endswith("_original.webp")
    assert body["thumbnail_url"].endswith("_thumb.webp")
    assert body["medium_url"].endswith("_medium.webp")

    # Les 3 fichiers sont bien sur le disque
    photo_id = body["id"]
    user_dir = next(Path(isolated_storage).iterdir())
    files = {p.name for p in user_dir.iterdir()}
    assert f"{photo_id}_original.webp" in files
    assert f"{photo_id}_medium.webp" in files
    assert f"{photo_id}_thumb.webp" in files

    # Le thumbnail est bien 150x150
    thumb = Image.open(user_dir / f"{photo_id}_thumb.webp")
    assert thumb.size == (150, 150)


async def test_upload_invalid_image(client, auth_headers):
    files = {"file": ("not_an_image.txt", b"hello world", "image/jpeg")}
    resp = await client.post("/photos", files=files, headers=auth_headers)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_image"


async def test_upload_max_reached(client, auth_headers):
    for i in range(6):
        resp = await _upload(client, auth_headers, color=(i * 40, 100, 100))
        assert resp.status_code == 201, f"upload {i}: {resp.text}"
    resp = await _upload(client, auth_headers)
    assert resp.status_code == 400
    assert "max_photos_reached" in resp.json()["detail"]


async def test_delete_below_min(client, auth_headers):
    """Avec 3 photos pile, la suppression doit échouer (min = 3)."""
    photo_ids = []
    for i in range(3):
        r = await _upload(client, auth_headers, color=(i * 50, 100, 100))
        photo_ids.append(r.json()["id"])

    resp = await client.delete(f"/photos/{photo_ids[0]}", headers=auth_headers)
    assert resp.status_code == 400
    assert "min_photos_required" in resp.json()["detail"]


async def test_delete_photo_success(client, auth_headers):
    photo_ids = []
    for i in range(4):
        r = await _upload(client, auth_headers, color=(i * 50, 100, 100))
        photo_ids.append(r.json()["id"])

    resp = await client.delete(f"/photos/{photo_ids[1]}", headers=auth_headers)
    assert resp.status_code == 204, resp.text


async def test_reorder_photos(client, auth_headers):
    ids = []
    for i in range(3):
        r = await _upload(client, auth_headers, color=(i * 50, 100, 100))
        ids.append(r.json()["id"])

    # On inverse l'ordre
    new_order = list(reversed(ids))
    resp = await client.patch(
        "/photos/reorder", json={"order": new_order}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [p["id"] for p in body] == new_order
    assert [p["display_order"] for p in body] == [0, 1, 2]


async def test_reorder_photos_mismatch(client, auth_headers):
    # Deux photos en DB mais on envoie un ordre incomplet
    r1 = await _upload(client, auth_headers)
    await _upload(client, auth_headers)

    resp = await client.patch(
        "/photos/reorder",
        json={"order": [r1.json()["id"]]},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "order_length_mismatch" in resp.json()["detail"]


async def test_upload_advances_onboarding_photos_step(
    client, auth_headers, db_session, test_user
):
    """
    Dès la 3e photo uploadée, l'étape onboarding PHOTOS est satisfaite.
    Pour que `next_step` sorte de PHOTOS il faut que tout le préfixe
    (city/basic_info/selfie) soit lui aussi fait. On cable tout le
    préfixe à la main pour tester le cas concret.
    """
    from uuid import uuid4

    from app.models.city import City
    from app.models.profile import Profile
    from datetime import date as _date

    # Setup minimal : city + profile + selfie_verified + photos
    city = City(
        id=uuid4(),
        name="Lomé",
        country_code="TG",
        country_name="Togo",
        timezone="Africa/Lome",
        currency_code="XOF",
        premium_price_monthly=5000,
        premium_price_weekly=1500,
        is_active=True,
    )
    db_session.add(city)
    test_user.city_id = city.id
    test_user.is_selfie_verified = True
    profile = Profile(
        user_id=test_user.id,
        display_name="Ama",
        birth_date=_date(2000, 3, 15),
        gender="woman",
        seeking_gender="men",
        intention="serious",
        sector="finance",
    )
    db_session.add(profile)
    # Attache la relation explicitement (lazy="selectin" n'a chargé
    # `profile` qu'à la création de test_user alors qu'il n'existait pas
    # encore — sans ça, le handler verrait user.profile == None).
    test_user.profile = profile
    await db_session.commit()

    # Upload 3 photos → doit faire avancer `onboarding_step` au-delà de
    # `photos` (la prochaine étape bloquante non faite = `quartiers`).
    for i in range(3):
        r = await _upload(client, auth_headers, color=(i * 50, 100, 100))
        assert r.status_code == 201

    await db_session.refresh(test_user)
    assert test_user.onboarding_step == "quartiers"


async def test_upload_selfie_sets_is_verified(
    client, auth_headers, db_session, test_user
):
    files = {"file": ("selfie.jpg", _make_image_bytes(), "image/jpeg")}
    resp = await client.post(
        "/profiles/me/selfie", files=files, headers=auth_headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_verified_selfie"] is True
    # Modération asynchrone §17 : la photo reste pending quoi qu'il arrive
    assert body["moderation_status"] == "pending"

    await db_session.refresh(test_user)
    assert test_user.is_selfie_verified is True


async def test_selfie_refused_when_liveness_required(
    client, auth_headers, monkeypatch
):
    """Quand le flag est True, l'endpoint refuse jusqu'à ce que le
    worker liveness soit câblé (Session 11)."""
    from app.api.v1 import profiles as profiles_api

    monkeypatch.setattr(profiles_api.settings, "selfie_liveness_required", True)

    files = {"file": ("selfie.jpg", _make_image_bytes(), "image/jpeg")}
    resp = await client.post(
        "/profiles/me/selfie", files=files, headers=auth_headers
    )
    assert resp.status_code == 501
    assert resp.json()["detail"] == "liveness_pipeline_not_ready"
