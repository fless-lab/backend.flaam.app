from __future__ import annotations

"""
Tests du matching engine — L1 filtres, L2 géo, L3 lifestyle, L4 behavior,
MàJ 6 préférences implicites, L5 corrections, et pipeline complet.

Scénario pédagogique basé sur le seed Lomé (§4 Session 4) :

  Quartiers : Tokoin, Bè, Djidjolé, Agoè, Nyékonakpoè
  Proximity : Tokoin↔Bè = 0.82, Tokoin↔Agoè = 0.33, …
  Spots     : Café 21 (cafe), Salle Olympe (gym), Chez Tonton (restaurant)

  Ama   — woman, 25, seeking men, serious, tech,
          quartiers: lives=Tokoin, hangs=Bè,
          spots: Café 21, Salle Olympe
  Kofi  — man,   28, seeking women, serious, creative,
          quartiers: lives=Tokoin, hangs=Djidjolé,
          spots: Café 21, Chez Tonton   → match fort avec Ama
  Yao   — man,   32, seeking women, serious, finance,
          quartiers: lives=Agoè,
          spots: Chez Tonton            → match faible (proximity 0.33 < 0.40)
"""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from app.models.city import City
from app.models.photo import Photo
from app.models.profile import Profile
from app.models.quartier import Quartier
from app.models.quartier_proximity import QuartierProximity
from app.models.spot import Spot
from app.models.user import User
from app.models.user_quartier import UserQuartier
from app.models.user_spot import UserSpot
from app.services.matching_engine import geo_scorer

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_geo_cache():
    """Le cache proximity est module-level : on le reset entre chaque test."""
    geo_scorer.reset_proximity_cache()
    yield
    geo_scorer.reset_proximity_cache()


async def _seed_lome(db_session) -> dict:
    """
    Crée Lomé + 5 quartiers + graphe de proximity + 3 spots.
    Retourne un dict avec les entités utiles.
    """
    city = City(
        id=uuid4(),
        name="Lomé",
        country_code="TG",
        country_name="Togo",
        country_flag="🇹🇬",
        phone_prefix="+228",
        timezone="Africa/Lome",
        currency_code="XOF",
        premium_price_monthly=5000,
        premium_price_weekly=1500,
        phase="launch",
        is_active=True,
    )
    db_session.add(city)
    await db_session.flush()

    quartiers = {
        "tokoin": Quartier(id=uuid4(), name="Tokoin", city_id=city.id,
                           latitude=6.158, longitude=1.2115),
        "be": Quartier(id=uuid4(), name="Bè", city_id=city.id,
                       latitude=6.134, longitude=1.221),
        "djidjole": Quartier(id=uuid4(), name="Djidjolé", city_id=city.id,
                             latitude=6.167, longitude=1.1955),
        "agoe": Quartier(id=uuid4(), name="Agoè", city_id=city.id,
                         latitude=6.1995, longitude=1.189),
        "nyekonakpoe": Quartier(id=uuid4(), name="Nyékonakpoè", city_id=city.id,
                                latitude=6.131, longitude=1.209),
    }
    db_session.add_all(quartiers.values())
    await db_session.flush()

    # Proximity (respecter la contrainte quartier_a_id < quartier_b_id)
    def _prox(a: Quartier, b: Quartier, score: float, dist: float):
        ids = sorted([a.id, b.id], key=str)
        qa, qb = (a, b) if ids[0] == a.id else (b, a)
        db_session.add(
            QuartierProximity(
                quartier_a_id=qa.id, quartier_b_id=qb.id,
                proximity_score=score, distance_km=dist,
            )
        )

    _prox(quartiers["tokoin"], quartiers["be"], 0.82, 2.1)
    _prox(quartiers["tokoin"], quartiers["djidjole"], 0.70, 3.0)
    _prox(quartiers["tokoin"], quartiers["agoe"], 0.33, 7.8)
    _prox(quartiers["tokoin"], quartiers["nyekonakpoe"], 0.60, 3.5)
    _prox(quartiers["be"], quartiers["djidjole"], 0.45, 5.5)
    _prox(quartiers["be"], quartiers["agoe"], 0.25, 9.0)
    _prox(quartiers["be"], quartiers["nyekonakpoe"], 0.75, 2.8)
    _prox(quartiers["djidjole"], quartiers["agoe"], 0.60, 4.2)
    _prox(quartiers["djidjole"], quartiers["nyekonakpoe"], 0.50, 5.0)
    _prox(quartiers["agoe"], quartiers["nyekonakpoe"], 0.20, 10.0)

    spots = {
        "cafe21": Spot(
            id=uuid4(), name="Café 21", category="cafe",
            city_id=city.id,
            location=from_shape(Point(1.2137, 6.1725), srid=4326),
            latitude=6.1725, longitude=1.2137,
            is_verified=True, is_active=True,
        ),
        "olympe": Spot(
            id=uuid4(), name="Salle Olympe", category="gym",
            city_id=city.id,
            location=from_shape(Point(1.2188, 6.135), srid=4326),
            latitude=6.135, longitude=1.2188,
            is_verified=True, is_active=True,
        ),
        "tonton": Spot(
            id=uuid4(), name="Chez Tonton", category="restaurant",
            city_id=city.id,
            location=from_shape(Point(1.2241, 6.1362), srid=4326),
            latitude=6.1362, longitude=1.2241,
            is_verified=True, is_active=True,
        ),
    }
    db_session.add_all(spots.values())
    await db_session.flush()

    return {"city": city, "quartiers": quartiers, "spots": spots}


async def _make_user(
    db_session,
    *,
    phone: str,
    city_id,
    display_name: str,
    gender: str,
    seeking: str,
    birth_year: int = 1998,
    intention: str = "serious",
    sector: str = "tech",
    tags: list[str] | None = None,
    languages: list[str] | None = None,
    completeness: float = 0.85,
    behavior: float = 1.0,
    selfie: bool = True,
    active: bool = True,
    visible: bool = True,
    last_active_offset_days: int = 0,
    photos_count: int = 3,
    account_age_days: int = 60,
) -> User:
    from app.utils.phone import hash_phone

    created_at = datetime.now(timezone.utc) - timedelta(days=account_age_days)
    last_active = datetime.now(timezone.utc) - timedelta(days=last_active_offset_days)

    user = User(
        id=uuid4(),
        phone_hash=hash_phone(phone),
        phone_country_code="228",
        is_phone_verified=True,
        is_selfie_verified=selfie,
        is_active=active,
        is_visible=visible,
        city_id=city_id,
        last_active_at=last_active,
    )
    # created_at est server_default → on le patch après insert si nécessaire
    db_session.add(user)
    await db_session.flush()

    profile = Profile(
        user_id=user.id,
        display_name=display_name,
        birth_date=date(birth_year, 6, 1),
        gender=gender,
        seeking_gender=seeking,
        intention=intention,
        sector=sector,
        tags=tags or [],
        languages=languages or ["fr"],
        seeking_age_min=18,
        seeking_age_max=50,
        profile_completeness=completeness,
        behavior_multiplier=behavior,
    )
    db_session.add(profile)

    for i in range(photos_count):
        db_session.add(
            Photo(
                id=uuid4(),
                user_id=user.id,
                original_url=f"http://x/{user.id}/{i}.jpg",
                thumbnail_url=f"http://x/{user.id}/{i}_t.jpg",
                medium_url=f"http://x/{user.id}/{i}_m.jpg",
                display_order=i,
                is_verified_selfie=(i == 0),
                content_hash=f"hash-{user.id}-{i}",
                width=800, height=1200, file_size_bytes=100_000,
                moderation_status="approved",
            )
        )

    await db_session.flush()

    # Patch created_at pour simuler l'ancienneté de compte
    user.created_at = created_at
    await db_session.flush()
    await db_session.refresh(user)
    return user


async def _attach_quartier(db_session, user, quartier, relation_type="lives"):
    db_session.add(
        UserQuartier(
            user_id=user.id,
            quartier_id=quartier.id,
            relation_type=relation_type,
            is_primary=(relation_type == "lives"),
        )
    )
    await db_session.flush()


async def _attach_spot(
    db_session, user, spot, fidelity_score=0.6, last_checkin_days_ago=5
):
    db_session.add(
        UserSpot(
            user_id=user.id,
            spot_id=spot.id,
            fidelity_score=fidelity_score,
            fidelity_level="confirmed",
            last_checkin_at=datetime.now(timezone.utc)
            - timedelta(days=last_checkin_days_ago),
            checkin_count=3,
        )
    )
    await db_session.flush()


async def _seed_ama_kofi_yao(db_session) -> dict:
    """Crée Ama / Kofi / Yao avec le setup pédagogique."""
    base = await _seed_lome(db_session)
    city = base["city"]
    q = base["quartiers"]
    s = base["spots"]

    ama = await _make_user(
        db_session, phone="+22890000001", city_id=city.id,
        display_name="Ama", gender="woman", seeking="men",
        birth_year=1999, tags=["foodie", "cinema"], languages=["fr", "ewe"],
    )
    kofi = await _make_user(
        db_session, phone="+22890000002", city_id=city.id,
        display_name="Kofi", gender="man", seeking="women",
        birth_year=1996, tags=["foodie", "music"], languages=["fr", "ewe"],
    )
    yao = await _make_user(
        db_session, phone="+22890000003", city_id=city.id,
        display_name="Yao", gender="man", seeking="women",
        birth_year=1992, tags=["sport"], languages=["fr"],
    )

    # Ama : lives=Tokoin, hangs=Bè, spots=Café 21, Salle Olympe
    await _attach_quartier(db_session, ama, q["tokoin"], "lives")
    await _attach_quartier(db_session, ama, q["be"], "hangs")
    await _attach_spot(db_session, ama, s["cafe21"], fidelity_score=0.8)
    await _attach_spot(db_session, ama, s["olympe"], fidelity_score=0.5)

    # Kofi : lives=Tokoin, hangs=Djidjolé, spots=Café 21, Chez Tonton
    await _attach_quartier(db_session, kofi, q["tokoin"], "lives")
    await _attach_quartier(db_session, kofi, q["djidjole"], "hangs")
    await _attach_spot(db_session, kofi, s["cafe21"], fidelity_score=0.7)
    await _attach_spot(db_session, kofi, s["tonton"], fidelity_score=0.6)

    # Yao : lives=Agoè, spots=Chez Tonton
    await _attach_quartier(db_session, yao, q["agoe"], "lives")
    await _attach_spot(db_session, yao, s["tonton"], fidelity_score=0.6)

    await db_session.commit()
    await db_session.refresh(ama)
    await db_session.refresh(kofi)
    await db_session.refresh(yao)

    return {**base, "ama": ama, "kofi": kofi, "yao": yao}


async def _default_config() -> dict:
    """Copie des MATCHING_DEFAULTS — évite de dépendre du config_service."""
    from app.core.constants import MATCHING_DEFAULTS
    return dict(MATCHING_DEFAULTS)


# ══════════════════════════════════════════════════════════════════════
# L1 — Hard filters
# ══════════════════════════════════════════════════════════════════════


async def test_hard_filters_basic_match(db_session):
    """Happy path : Kofi et Yao apparaissent dans les candidats d'Ama."""
    from app.services.matching_engine.hard_filters import apply_hard_filters

    data = await _seed_ama_kofi_yao(db_session)
    candidates = await apply_hard_filters(data["ama"], db_session)
    assert data["kofi"].id in candidates
    assert data["yao"].id in candidates
    assert data["ama"].id not in candidates  # pas soi-même


async def test_hard_filters_exclude_blocked(db_session):
    """Ama bloque Kofi → Kofi n'apparaît plus dans les candidats."""
    from app.models.block import Block
    from app.services.matching_engine.hard_filters import apply_hard_filters

    data = await _seed_ama_kofi_yao(db_session)
    db_session.add(Block(blocker_id=data["ama"].id, blocked_id=data["kofi"].id))
    await db_session.commit()

    candidates = await apply_hard_filters(data["ama"], db_session)
    assert data["kofi"].id not in candidates
    assert data["yao"].id in candidates


async def test_hard_filters_exclude_same_gender(db_session):
    """Ama cherche des 'men' → une autre femme n'est jamais candidate."""
    from app.services.matching_engine.hard_filters import apply_hard_filters

    data = await _seed_ama_kofi_yao(db_session)
    other_woman = await _make_user(
        db_session, phone="+22890000099", city_id=data["city"].id,
        display_name="Akua", gender="woman", seeking="men",
    )
    await db_session.commit()

    candidates = await apply_hard_filters(data["ama"], db_session)
    assert other_woman.id not in candidates


async def test_hard_filters_exclude_inactive(db_session):
    """Un user inactif > 7j est filtré."""
    from app.services.matching_engine.hard_filters import apply_hard_filters

    data = await _seed_ama_kofi_yao(db_session)
    # Kofi devient inactif depuis 10 jours
    data["kofi"].last_active_at = datetime.now(timezone.utc) - timedelta(days=10)
    await db_session.commit()

    candidates = await apply_hard_filters(data["ama"], db_session)
    assert data["kofi"].id not in candidates


async def test_hard_filters_exclude_blacklisted_contact(db_session):
    """Un user dans la blacklist contacts est filtré."""
    from app.models.contact_blacklist import ContactBlacklist
    from app.services.matching_engine.hard_filters import apply_hard_filters

    data = await _seed_ama_kofi_yao(db_session)
    db_session.add(
        ContactBlacklist(
            user_id=data["ama"].id, phone_hash=data["kofi"].phone_hash
        )
    )
    await db_session.commit()

    candidates = await apply_hard_filters(data["ama"], db_session)
    assert data["kofi"].id not in candidates


# ══════════════════════════════════════════════════════════════════════
# L2 — Geo scorer
# ══════════════════════════════════════════════════════════════════════


async def test_geo_scorer_exact_quartier(db_session):
    """Ama et Kofi vivent tous les deux à Tokoin → score géo élevé."""
    from app.services.matching_engine.geo_scorer import (
        compute_geo_scores, load_proximity_cache,
    )

    data = await _seed_ama_kofi_yao(db_session)
    await load_proximity_cache(data["city"].id, db_session)
    config = await _default_config()

    scores = await compute_geo_scores(
        data["ama"], [data["kofi"].id, data["yao"].id], config, db_session
    )
    assert scores[data["kofi"].id] > scores[data["yao"].id]
    assert scores[data["kofi"].id] > 0.3  # match fort


async def test_geo_scorer_soft_match_nearby(db_session):
    """
    Ama vit à Tokoin, on teste avec un user qui vit à Bè (proximity 0.82).
    Pas de quartier commun mais score non nul via le graphe.
    """
    from app.services.matching_engine.geo_scorer import (
        compute_geo_scores, load_proximity_cache,
    )

    data = await _seed_ama_kofi_yao(db_session)
    # Crée un user qui vit uniquement à Bè (pas à Tokoin)
    bob = await _make_user(
        db_session, phone="+22890000010", city_id=data["city"].id,
        display_name="Bob", gender="man", seeking="women",
    )
    await _attach_quartier(db_session, bob, data["quartiers"]["be"], "lives")
    await db_session.commit()

    # Ama aussi on lui retire Bè pour n'avoir que Tokoin (sinon exact match)
    ama_only_tokoin = await _make_user(
        db_session, phone="+22890000011", city_id=data["city"].id,
        display_name="Ama2", gender="woman", seeking="men",
    )
    await _attach_quartier(
        db_session, ama_only_tokoin, data["quartiers"]["tokoin"], "lives"
    )
    await db_session.commit()
    await db_session.refresh(ama_only_tokoin)

    await load_proximity_cache(data["city"].id, db_session)
    config = await _default_config()
    scores = await compute_geo_scores(
        ama_only_tokoin, [bob.id], config, db_session
    )
    assert 0.0 < scores[bob.id] < 1.0


async def test_geo_scorer_below_threshold_is_low(db_session):
    """Tokoin↔Agoè = 0.33 < threshold 0.40 → score quartier quasi nul."""
    from app.services.matching_engine.geo_scorer import (
        compute_geo_scores, load_proximity_cache,
    )

    data = await _seed_ama_kofi_yao(db_session)
    # Crée une Ama qui n'a QUE Tokoin (pour éviter le bonus Bè-Agoè=0.25)
    ama2 = await _make_user(
        db_session, phone="+22890000020", city_id=data["city"].id,
        display_name="Ama-Tokoin", gender="woman", seeking="men",
    )
    await _attach_quartier(
        db_session, ama2, data["quartiers"]["tokoin"], "lives"
    )
    await db_session.commit()
    await db_session.refresh(ama2)

    await load_proximity_cache(data["city"].id, db_session)
    config = await _default_config()
    scores = await compute_geo_scores(
        ama2, [data["yao"].id], config, db_session
    )
    # Yao (Agoè) sous le seuil par rapport à Tokoin : score très bas
    assert scores[data["yao"].id] < 0.25


async def test_geo_scorer_interested_bridges_gap(db_session):
    """
    Si Ama coche Agoè en 'interested', Yao (qui vit à Agoè) devient
    accessible même si la proximity Tokoin↔Agoè est sous le seuil.
    """
    from app.services.matching_engine.geo_scorer import (
        compute_geo_scores, load_proximity_cache,
    )

    data = await _seed_ama_kofi_yao(db_session)
    # Baseline sans interested
    await load_proximity_cache(data["city"].id, db_session)
    config = await _default_config()
    baseline = await compute_geo_scores(
        data["ama"], [data["yao"].id], config, db_session
    )

    # Ama coche Agoè en interested
    await _attach_quartier(
        db_session, data["ama"], data["quartiers"]["agoe"], "interested"
    )
    await db_session.commit()
    await db_session.refresh(data["ama"])

    with_interested = await compute_geo_scores(
        data["ama"], [data["yao"].id], config, db_session
    )
    assert with_interested[data["yao"].id] > baseline[data["yao"].id]


async def test_geo_scorer_spots_in_common(db_session):
    """Ama et Kofi partagent Café 21 → le score spot contribue."""
    from app.services.matching_engine.geo_scorer import (
        compute_geo_scores, load_proximity_cache,
    )

    data = await _seed_ama_kofi_yao(db_session)
    await load_proximity_cache(data["city"].id, db_session)
    config = await _default_config()

    scores_with = await compute_geo_scores(
        data["ama"], [data["kofi"].id], config, db_session
    )

    # Crée un "Kofi2" identique sur les quartiers mais sans spot commun
    kofi2 = await _make_user(
        db_session, phone="+22890000030", city_id=data["city"].id,
        display_name="Kofi2", gender="man", seeking="women",
    )
    await _attach_quartier(
        db_session, kofi2, data["quartiers"]["tokoin"], "lives"
    )
    await _attach_quartier(
        db_session, kofi2, data["quartiers"]["djidjole"], "hangs"
    )
    # Un spot pas en commun avec Ama
    await _attach_spot(db_session, kofi2, data["spots"]["tonton"])
    await db_session.commit()

    scores_without = await compute_geo_scores(
        data["ama"], [kofi2.id], config, db_session
    )
    assert scores_with[data["kofi"].id] > scores_without[kofi2.id]


# ══════════════════════════════════════════════════════════════════════
# L3 — Lifestyle scorer
# ══════════════════════════════════════════════════════════════════════


async def test_lifestyle_scorer_tags(db_session):
    """Tags en commun → score lifestyle plus élevé qu'un candidat sans tags."""
    from app.services.matching_engine.lifestyle_scorer import (
        compute_lifestyle_scores,
    )

    data = await _seed_ama_kofi_yao(db_session)
    # Ama tags=[foodie, cinema], Kofi=[foodie, music], Yao=[sport]
    config = await _default_config()
    scores = await compute_lifestyle_scores(
        data["ama"], [data["kofi"].id, data["yao"].id], config, db_session
    )
    assert scores[data["kofi"].id] > scores[data["yao"].id]


async def test_lifestyle_scorer_returns_0_to_1(db_session):
    from app.services.matching_engine.lifestyle_scorer import (
        compute_lifestyle_scores,
    )

    data = await _seed_ama_kofi_yao(db_session)
    config = await _default_config()
    scores = await compute_lifestyle_scores(
        data["ama"], [data["kofi"].id, data["yao"].id], config, db_session
    )
    for s in scores.values():
        assert 0.0 <= s <= 1.0


# ══════════════════════════════════════════════════════════════════════
# L4 — Behavior
# ══════════════════════════════════════════════════════════════════════


async def test_behavior_multiplier_range(db_session, redis_client):
    """Après 20 actions mixtes, le multiplier reste dans [0.6, 1.4]."""
    from app.services.matching_engine.behavior_scorer import (
        get_behavior_multipliers, update_behavior_on_action,
    )

    data = await _seed_ama_kofi_yao(db_session)
    config = await _default_config()

    # Spam : 20 likes
    for _ in range(20):
        await update_behavior_on_action(
            data["ama"].id, "like", None, redis_client, db_session, config
        )
    await db_session.commit()

    mults = await get_behavior_multipliers(
        [data["ama"].id], redis_client, db_session
    )
    assert 0.6 <= mults[data["ama"].id] <= 1.4


async def test_behavior_fallback_to_db(db_session, redis_client):
    """Sans Redis, le multiplicateur retombe sur Profile.behavior_multiplier."""
    from app.services.matching_engine.behavior_scorer import (
        get_behavior_multipliers,
    )

    data = await _seed_ama_kofi_yao(db_session)
    data["kofi"].profile.behavior_multiplier = 1.25
    await db_session.commit()

    mults = await get_behavior_multipliers(
        [data["kofi"].id], redis_client, db_session
    )
    assert mults[data["kofi"].id] == pytest.approx(1.25)


# ══════════════════════════════════════════════════════════════════════
# MàJ 6 — Préférences implicites
# ══════════════════════════════════════════════════════════════════════


async def test_sanitize_time_caps_at_60s():
    from app.services.matching_engine.implicit_preferences import (
        sanitize_time_signal,
    )
    # 120s avec corroboration = cap à 1.0
    assert sanitize_time_signal(120.0, has_corroboration=True) == pytest.approx(1.0)
    # 300s idem
    assert sanitize_time_signal(300.0, has_corroboration=True) == pytest.approx(1.0)
    # 60s exactement = 1.0
    assert sanitize_time_signal(60.0, has_corroboration=True) == pytest.approx(1.0)


async def test_sanitize_time_rejects_without_corroboration():
    from app.services.matching_engine.implicit_preferences import (
        sanitize_time_signal,
    )
    # 20s sans corroboration = 0 (téléphone posé)
    assert sanitize_time_signal(20.0, has_corroboration=False) == 0.0
    # Idem 200s sans corroboration
    assert sanitize_time_signal(200.0, has_corroboration=False) == 0.0
    # Sous le seuil minimum = 0 même avec corroboration
    assert sanitize_time_signal(3.0, has_corroboration=True) == 0.0


async def test_implicit_profile_empty_when_few_signals(db_session, redis_client):
    """< 5 behavior_logs → confidence=0 et dicts vides."""
    from app.services.matching_engine.implicit_preferences import (
        compute_implicit_profile,
    )
    from app.models.behavior_log import BehaviorLog

    data = await _seed_ama_kofi_yao(db_session)
    # 3 logs seulement
    for i in range(3):
        db_session.add(
            BehaviorLog(
                user_id=data["ama"].id,
                event_type="profile_view_duration",
                target_user_id=data["kofi"].id,
                duration_seconds=10.0,
            )
        )
    await db_session.commit()

    result = await compute_implicit_profile(
        data["ama"].id, db_session, redis_client, use_cache=False
    )
    assert result["confidence"] == 0.0
    assert result["preferred_tags"] == {}
    assert result["signal_count"] == 3


async def test_implicit_adjustment_bounded_15_percent():
    """L'ajustement ne peut pas déborder ±0.15 quelle que soit la preference."""
    from types import SimpleNamespace
    from app.services.matching_engine.implicit_preferences import (
        apply_implicit_adjustment,
    )

    candidate = SimpleNamespace(tags=["foodie", "music"], sector="creative")
    # Preferences massivement biaisées avec confiance max
    implicit = {
        "preferred_tags": {"foodie": 1.0, "music": 1.0},
        "preferred_sectors": {"creative": 1.0},
        "rejected_tags": {},
        "rejected_sectors": {},
        "confidence": 1.0,
    }
    adjusted = apply_implicit_adjustment(0.5, candidate, implicit)
    assert 0.5 <= adjusted <= 0.65  # +0.15 max

    # Rejet massif
    implicit_rej = {
        "preferred_tags": {},
        "preferred_sectors": {},
        "rejected_tags": {"foodie": 1.0, "music": 1.0},
        "rejected_sectors": {"creative": 1.0},
        "confidence": 1.0,
    }
    adjusted_rej = apply_implicit_adjustment(0.5, candidate, implicit_rej)
    assert 0.35 <= adjusted_rej <= 0.5  # -0.15 max


async def test_implicit_adjustment_skipped_when_low_confidence():
    """Confidence < 0.3 → pas d'ajustement."""
    from types import SimpleNamespace
    from app.services.matching_engine.implicit_preferences import (
        apply_implicit_adjustment,
    )

    candidate = SimpleNamespace(tags=["foodie"], sector="creative")
    implicit = {
        "preferred_tags": {"foodie": 1.0},
        "preferred_sectors": {"creative": 1.0},
        "rejected_tags": {},
        "rejected_sectors": {},
        "confidence": 0.2,
    }
    assert apply_implicit_adjustment(0.5, candidate, implicit) == 0.5


# ══════════════════════════════════════════════════════════════════════
# L5 — Corrections
# ══════════════════════════════════════════════════════════════════════


async def test_corrections_wildcard_selection(db_session):
    """
    Wildcards V1 = candidats geo_score > médiane + lifestyle_score < 0.3.
    """
    from app.services.matching_engine.corrections import inject_wildcards

    a, b, c, d = (uuid4() for _ in range(4))
    sorted_candidates = [(a, 0.9), (b, 0.7), (c, 0.5), (d, 0.3)]
    geo_scores = {a: 0.9, b: 0.8, c: 0.8, d: 0.3}
    lifestyle_scores = {a: 0.9, b: 0.1, c: 0.2, d: 0.5}
    # top_profiles : a (déjà pris dans le top)
    picks = await inject_wildcards(
        user=None,
        top_profiles=[a],
        sorted_candidates=sorted_candidates,
        geo_scores=geo_scores,
        lifestyle_scores=lifestyle_scores,
        count=2,
        db_session=db_session,
    )
    # b et c passent (géo > médiane ET lifestyle < 0.3), d ne passe pas
    assert set(picks) == {b, c}


async def test_corrections_wildcard_percentage(db_session):
    """Avec un pool et count=2, on n'obtient jamais plus de 2 wildcards."""
    from app.services.matching_engine.corrections import inject_wildcards

    ids = [uuid4() for _ in range(10)]
    sorted_candidates = [(i, 1.0 - k * 0.1) for k, i in enumerate(ids)]
    geo_scores = {i: 0.9 for i in ids}  # tous au-dessus de la médiane
    lifestyle_scores = {i: 0.1 for i in ids}  # tous divergents

    picks = await inject_wildcards(
        user=None,
        top_profiles=[ids[0], ids[1]],
        sorted_candidates=sorted_candidates,
        geo_scores=geo_scores,
        lifestyle_scores=lifestyle_scores,
        count=2,
        db_session=db_session,
    )
    assert len(picks) == 2
    assert ids[0] not in picks and ids[1] not in picks


async def test_corrections_new_user_boost(db_session):
    """Un user créé il y a 2 jours passe le boost."""
    from app.services.matching_engine.corrections import apply_new_user_boost

    data = await _seed_ama_kofi_yao(db_session)
    # new_user créé il y a 2 jours
    newbie = await _make_user(
        db_session, phone="+22890000050", city_id=data["city"].id,
        display_name="Akosua", gender="man", seeking="women",
        account_age_days=2,
    )
    # old_user créé il y a 100j
    veteran = await _make_user(
        db_session, phone="+22890000051", city_id=data["city"].id,
        display_name="Yawo", gender="man", seeking="women",
        account_age_days=100,
    )
    await db_session.commit()

    picks = await apply_new_user_boost(
        [newbie.id, veteran.id], max_count=2, db_session=db_session
    )
    assert newbie.id in picks
    assert veteran.id not in picks


async def test_shuffle_feed_is_deterministic(db_session):
    """Même user + même date → même ordre."""
    from app.services.matching_engine.corrections import shuffle_feed

    ids = [uuid4() for _ in range(10)]
    user_id = uuid4()
    today = date(2026, 4, 16)

    a = shuffle_feed(ids, user_id, today)
    b = shuffle_feed(ids, user_id, today)
    assert a == b

    c = shuffle_feed(ids, user_id, date(2026, 4, 17))
    assert a != c  # autre date → autre ordre


# ══════════════════════════════════════════════════════════════════════
# Intégration — Ama/Kofi/Yao scenarios
# ══════════════════════════════════════════════════════════════════════


async def test_ama_matches_kofi_stronger_than_yao(db_session):
    """Kofi (Tokoin+Café 21) > Yao (Agoè) sur le score combiné géo+lifestyle."""
    from app.services.matching_engine.geo_scorer import (
        compute_geo_scores, load_proximity_cache,
    )
    from app.services.matching_engine.lifestyle_scorer import (
        compute_lifestyle_scores,
    )

    data = await _seed_ama_kofi_yao(db_session)
    await load_proximity_cache(data["city"].id, db_session)
    config = await _default_config()

    cids = [data["kofi"].id, data["yao"].id]
    geo = await compute_geo_scores(data["ama"], cids, config, db_session)
    life = await compute_lifestyle_scores(
        data["ama"], cids, config, db_session
    )
    kofi_total = geo[data["kofi"].id] + life[data["kofi"].id]
    yao_total = geo[data["yao"].id] + life[data["yao"].id]
    assert kofi_total > yao_total


async def test_full_pipeline_returns_feed(db_session, redis_client):
    """
    Pipeline complet : Ama doit obtenir un feed non vide contenant Kofi.
    La taille est <= 12. Avec 2 hommes, on aura peu d'items mais le
    pipeline doit fonctionner de bout en bout.
    """
    from app.services.matching_engine import generate_feed_for_user

    data = await _seed_ama_kofi_yao(db_session)
    await db_session.commit()

    result = await generate_feed_for_user(
        data["ama"].id, db_session, redis_client
    )
    assert len(result["profile_ids"]) <= 12
    assert data["kofi"].id in result["profile_ids"]


async def test_full_pipeline_returns_8_to_12(db_session, redis_client):
    """Avec 15 candidats masculins, la taille finale ∈ [8, 12]."""
    from app.services.matching_engine import generate_feed_for_user

    data = await _seed_ama_kofi_yao(db_session)
    # Ajoute 13 hommes supplémentaires à Tokoin pour garantir la taille
    for i in range(13):
        extra = await _make_user(
            db_session, phone=f"+2289010{i:04d}", city_id=data["city"].id,
            display_name=f"User{i}", gender="man", seeking="women",
            tags=["foodie"] if i % 2 == 0 else ["music"],
        )
        await _attach_quartier(
            db_session, extra, data["quartiers"]["tokoin"], "lives"
        )
    await db_session.commit()

    result = await generate_feed_for_user(
        data["ama"].id, db_session, redis_client
    )
    assert 8 <= len(result["profile_ids"]) <= 12


async def test_first_impression_only_applies_to_women(
    db_session, redis_client
):
    """
    Pour Kofi (homme), first-impression ne re-trie pas. Pour Ama (femme),
    les profils qualifiés remontent.
    """
    from app.services.matching_engine.first_impression import (
        apply_first_impression,
    )

    data = await _seed_ama_kofi_yao(db_session)
    config = await _default_config()
    feed_ids = [data["kofi"].id, data["yao"].id]

    # Kofi est homme → passthrough
    result = await apply_first_impression(
        data["kofi"], feed_ids, config, db_session
    )
    assert result == feed_ids


async def test_config_service_fallback_to_default(db_session, redis_client):
    """Si Redis vide et DB vide, on retombe sur MATCHING_DEFAULTS."""
    from app.services.config_service import get_config

    v = await get_config("geo_w_quartier", redis_client, db_session)
    assert v == pytest.approx(0.45)


# ─────────────────────────────────────────────────────────────────────
# Age fit (overlap ±3 ans + soft drop-off)
# ─────────────────────────────────────────────────────────────────────


def test_age_fit_perfect_match():
    from app.services.matching_engine.age_fit import compute_age_fit

    # Tous les 2 dans range stricte → 1.0
    fit = compute_age_fit(
        user_age=27,
        candidate_age=28,
        user_seeking_age_min=25,
        user_seeking_age_max=30,
        candidate_seeking_age_min=25,
        candidate_seeking_age_max=30,
    )
    assert fit == pytest.approx(1.0)


def test_age_fit_one_year_below_user_range():
    from app.services.matching_engine.age_fit import compute_age_fit

    # Candidat 1 an sous le min user → 1 - 0.20 = 0.80
    fit = compute_age_fit(
        user_age=27,
        candidate_age=24,
        user_seeking_age_min=25,
        user_seeking_age_max=30,
        candidate_seeking_age_min=20,
        candidate_seeking_age_max=35,
    )
    assert fit == pytest.approx(0.80)


def test_age_fit_three_years_floor():
    from app.services.matching_engine.age_fit import compute_age_fit

    # Candidat 3 ans hors range → 1 - 3*0.20 = 0.40 (le floor)
    fit = compute_age_fit(
        user_age=27,
        candidate_age=22,
        user_seeking_age_min=25,
        user_seeking_age_max=30,
        candidate_seeking_age_min=20,
        candidate_seeking_age_max=35,
    )
    assert fit == pytest.approx(0.40)


def test_age_fit_takes_worst_of_both_directions():
    from app.services.matching_engine.age_fit import compute_age_fit

    # User 29 cherche 25-30, candidat 28 (OK, 0 hors range)
    # Candidat 28 cherche 30-35, user 29 (1 an sous le min candidat)
    # → max(0, 1) = 1 → fit = 0.80
    fit = compute_age_fit(
        user_age=29,
        candidate_age=28,
        user_seeking_age_min=25,
        user_seeking_age_max=30,
        candidate_seeking_age_min=30,
        candidate_seeking_age_max=35,
    )
    assert fit == pytest.approx(0.80)


def test_age_fit_clamp_at_overlap():
    from app.services.matching_engine.age_fit import compute_age_fit

    # Au-delà de l'overlap, le hard filter devrait avoir filtré, mais
    # défensivement on clamp au floor au lieu de descendre à 0/négatif.
    fit = compute_age_fit(
        user_age=27,
        candidate_age=18,
        user_seeking_age_min=25,
        user_seeking_age_max=30,
        candidate_seeking_age_min=20,
        candidate_seeking_age_max=35,
    )
    assert fit == pytest.approx(0.40)
