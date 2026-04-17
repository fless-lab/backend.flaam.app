from __future__ import annotations

"""Tests Profiles (§5.2, §13)."""

from datetime import date
from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _base_profile_payload() -> dict:
    return {
        "display_name": "Ama",
        "birth_date": "2000-03-15",
        "gender": "woman",
        "seeking_gender": "men",
        "intention": "serious",
        "sector": "finance",
    }


async def test_get_me_before_profile_created(client, auth_headers):
    resp = await client.get("/profiles/me", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "profile_not_created"


async def test_update_profile_creates_profile(client, auth_headers):
    resp = await client.put(
        "/profiles/me",
        json=_base_profile_payload(),
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["display_name"] == "Ama"
    assert body["age"] >= 18
    assert body["intention"] == "serious"
    assert body["profile_completeness"] >= 0.0
    # Pas de photos encore
    assert body["photos"] == []


async def test_update_profile_missing_required_fields(client, auth_headers):
    resp = await client.put(
        "/profiles/me",
        json={"display_name": "Ama"},  # sans birth_date/gender/etc.
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "missing_required_fields" in resp.json()["detail"]


async def test_update_profile_rejects_minors(client, auth_headers):
    payload = _base_profile_payload()
    payload["birth_date"] = date.today().replace(year=date.today().year - 16).isoformat()
    resp = await client.put("/profiles/me", json=payload, headers=auth_headers)
    assert resp.status_code == 422  # pydantic validator


async def test_update_profile_partial_update(client, auth_headers):
    # Create profile first
    await client.put("/profiles/me", json=_base_profile_payload(), headers=auth_headers)
    # Then patch just the tags
    resp = await client.put(
        "/profiles/me",
        json={"tags": ["foodie", "yoga"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["foodie", "yoga"]
    # Le display_name doit rester inchangé
    assert resp.json()["display_name"] == "Ama"


async def test_visibility_toggle(client, auth_headers):
    await client.put("/profiles/me", json=_base_profile_payload(), headers=auth_headers)
    resp = await client.patch(
        "/profiles/me/visibility", json={"is_visible": False}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["is_visible"] is False


async def test_completeness_score(client, auth_headers):
    await client.put(
        "/profiles/me",
        json={
            **_base_profile_payload(),
            "tags": ["foodie", "yoga"],
            "prompts": [
                {"question": "Un dimanche parfait c'est", "answer": "brunch"}
            ],
        },
        headers=auth_headers,
    )
    resp = await client.get("/profiles/me/completeness", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert 0.0 <= body["score"] <= 1.0
    steps_achieved = {b["step"]: b["achieved"] for b in body["breakdown"]}
    assert steps_achieved.get("tags") is True
    assert steps_achieved.get("prompts") is True
    assert steps_achieved.get("photos") is False  # aucun selfie upload


async def test_completeness_score_cached_in_db(
    client, auth_headers, db_session, test_user
):
    """
    Le score est stocké dans Profile.profile_completeness au PUT
    /profiles/me — pas recalculé à la volée au GET.
    """
    await client.put(
        "/profiles/me",
        json={
            **_base_profile_payload(),
            "tags": ["foodie", "yoga"],
            "prompts": [{"question": "q", "answer": "a"}],
        },
        headers=auth_headers,
    )
    # Relire le Profile brut depuis la DB (pas via l'API)
    from app.models.profile import Profile
    from sqlalchemy import select as _sel

    res = await db_session.execute(
        _sel(Profile).where(Profile.user_id == test_user.id)
    )
    profile = res.scalar_one()
    # prompts (0.20) + tags (0.15) = 0.35 attendu exactement
    assert abs(profile.profile_completeness - 0.35) < 1e-6

    # GET /completeness retourne bien la valeur stockée
    resp = await client.get("/profiles/me/completeness", headers=auth_headers)
    assert abs(resp.json()["score"] - profile.profile_completeness) < 1e-6


async def test_onboarding_state(client, auth_headers):
    resp = await client.get("/profiles/me/onboarding", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    # Premier appel : city_selection est in_progress (aucun city_id)
    assert body["current_step"] == "city_selection"
    steps_by_name = {s["step"]: s for s in body["steps"]}
    assert steps_by_name["city_selection"]["status"] == "in_progress"
    assert steps_by_name["prompts"]["skippable"] is True
    assert steps_by_name["photos"]["skippable"] is False


async def test_onboarding_skip_non_skippable(client, auth_headers):
    resp = await client.post(
        "/profiles/me/onboarding/skip",
        json={"step": "photos"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "step_not_skippable" in resp.json()["detail"]


async def test_onboarding_skip_skippable(client, auth_headers):
    resp = await client.post(
        "/profiles/me/onboarding/skip",
        json={"step": "prompts"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["skipped"] == "prompts"
    assert body["warning"]  # on informe que ça diminue la visibilité


async def test_update_profile_with_city_id_advances_onboarding(
    client, auth_headers, db_session, test_user
):
    """PUT /profiles/me avec city_id valide (launch) avance l'onboarding."""
    from app.models.city import City

    city = City(
        id=uuid4(),
        name="Lomé",
        country_code="TG",
        country_name="Togo",
        timezone="Africa/Lome",
        currency_code="XOF",
        premium_price_monthly=5000,
        premium_price_weekly=1500,
        phase="launch",
        is_active=True,
    )
    db_session.add(city)
    await db_session.commit()

    resp = await client.put(
        "/profiles/me",
        json={**_base_profile_payload(), "city_id": str(city.id)},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["city_id"] == str(city.id)

    # Onboarding doit avoir avancé au-delà de city_selection
    onb = await client.get("/profiles/me/onboarding", headers=auth_headers)
    assert onb.json()["current_step"] != "city_selection"


async def test_update_profile_with_teaser_city_rejected(
    client, auth_headers, db_session
):
    """PUT /profiles/me avec city_id teaser doit renvoyer 400."""
    from app.models.city import City

    teaser_city = City(
        id=uuid4(),
        name="Kara",
        country_code="TG",
        country_name="Togo",
        timezone="Africa/Lome",
        currency_code="XOF",
        premium_price_monthly=5000,
        premium_price_weekly=1500,
        phase="teaser",
        is_active=True,
    )
    db_session.add(teaser_city)
    await db_session.commit()

    resp = await client.put(
        "/profiles/me",
        json={"city_id": str(teaser_city.id)},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "city_not_available"


async def test_patch_profile_city_only(client, auth_headers, db_session):
    """PATCH /profiles/me avec city_id seul (pas de Profile) → 200."""
    from app.models.city import City

    city = City(
        id=uuid4(),
        name="Abidjan",
        country_code="CI",
        country_name="Cote d'Ivoire",
        timezone="Africa/Abidjan",
        currency_code="XOF",
        premium_price_monthly=5000,
        premium_price_weekly=1500,
        phase="launch",
        is_active=True,
    )
    db_session.add(city)
    await db_session.commit()

    resp = await client.patch(
        "/profiles/me",
        json={"city_id": str(city.id)},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["city_id"] == str(city.id)
    assert resp.json()["onboarding_step"] != "city_selection"


async def test_patch_profile_basic_info_partial(client, auth_headers, db_session):
    """PATCH /profiles/me avec basic_info partiel (sans intention/sector) → 200."""
    from app.models.city import City

    city = City(
        id=uuid4(),
        name="Dakar",
        country_code="SN",
        country_name="Senegal",
        timezone="Africa/Dakar",
        currency_code="XOF",
        premium_price_monthly=5000,
        premium_price_weekly=1500,
        phase="launch",
        is_active=True,
    )
    db_session.add(city)
    await db_session.commit()

    # D'abord city
    await client.patch(
        "/profiles/me",
        json={"city_id": str(city.id)},
        headers=auth_headers,
    )

    # Puis basic_info sans intention ni sector
    resp = await client.patch(
        "/profiles/me",
        json={
            "display_name": "Ama",
            "birth_date": "2000-03-15",
            "gender": "woman",
            "seeking_gender": "men",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["display_name"] == "Ama"
    # intention/sector sont None car pas encore remplis
    assert body.get("intention") is None
    assert body.get("sector") is None


async def test_patch_then_put_completes_profile(client, auth_headers, db_session):
    """PATCH pour creer le profil partiel, puis PUT pour le completer."""
    from app.models.city import City

    city = City(
        id=uuid4(),
        name="Accra",
        country_code="GH",
        country_name="Ghana",
        timezone="Africa/Accra",
        currency_code="GHS",
        premium_price_monthly=50,
        premium_price_weekly=15,
        phase="launch",
        is_active=True,
    )
    db_session.add(city)
    await db_session.commit()

    # PATCH city
    await client.patch(
        "/profiles/me",
        json={"city_id": str(city.id)},
        headers=auth_headers,
    )
    # PATCH basic_info
    await client.patch(
        "/profiles/me",
        json={
            "display_name": "Kofi",
            "birth_date": "1998-06-20",
            "gender": "man",
            "seeking_gender": "women",
        },
        headers=auth_headers,
    )
    # PATCH intention + sector
    resp = await client.patch(
        "/profiles/me",
        json={"intention": "serious", "sector": "tech"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["intention"] == "serious"
    assert body["sector"] == "tech"
    assert body["display_name"] == "Kofi"


async def test_get_other_profile_not_visible(client, auth_headers, db_session):
    """Voir un user invisible doit renvoyer 404."""
    from app.models.user import User
    from app.utils.phone import hash_phone

    other = User(
        phone_hash=hash_phone("+22899999998"),
        phone_country_code="228",
        is_visible=False,
    )
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)

    resp = await client.get(f"/profiles/{other.id}", headers=auth_headers)
    assert resp.status_code == 404
