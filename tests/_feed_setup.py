from __future__ import annotations

"""
Helpers de seeding partagés pour les tests Feed / Matches / Ice-breaker.

Pas de décorateur test (underscore prefix) → pytest ne collecte pas ce
module. Inspiré de tests/test_matching.py, simplifié pour S6.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from app.core.security import create_access_token
from app.models.city import City
from app.models.photo import Photo
from app.models.profile import Profile
from app.models.quartier import Quartier
from app.models.quartier_proximity import QuartierProximity
from app.models.spot import Spot
from app.models.user import User
from app.models.user_quartier import UserQuartier
from app.models.user_spot import UserSpot
from app.utils.phone import hash_phone


async def seed_city_lome(db) -> dict:
    """Crée Lomé + 3 quartiers + 3 spots + graphe de proximity minimal."""
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
    db.add(city)
    await db.flush()

    q = {
        "tokoin": Quartier(
            id=uuid4(), name="Tokoin", city_id=city.id,
            latitude=6.158, longitude=1.2115,
        ),
        "be": Quartier(
            id=uuid4(), name="Bè", city_id=city.id,
            latitude=6.134, longitude=1.221,
        ),
        "djidjole": Quartier(
            id=uuid4(), name="Djidjolé", city_id=city.id,
            latitude=6.167, longitude=1.1955,
        ),
    }
    db.add_all(q.values())
    await db.flush()

    def _prox(a: Quartier, b: Quartier, score: float, dist: float):
        ids = sorted([a.id, b.id], key=str)
        qa, qb = (a, b) if ids[0] == a.id else (b, a)
        db.add(
            QuartierProximity(
                quartier_a_id=qa.id, quartier_b_id=qb.id,
                proximity_score=score, distance_km=dist,
            )
        )

    _prox(q["tokoin"], q["be"], 0.82, 2.1)
    _prox(q["tokoin"], q["djidjole"], 0.70, 3.0)
    _prox(q["be"], q["djidjole"], 0.45, 5.5)

    spots = {
        "cafe21": Spot(
            id=uuid4(), name="Café 21", category="cafe",
            city_id=city.id,
            location=from_shape(Point(1.2137, 6.1725), srid=4326),
            latitude=6.1725, longitude=1.2137,
            is_verified=True, is_active=True,
        ),
        "tonton": Spot(
            id=uuid4(), name="Chez Tonton", category="restaurant",
            city_id=city.id,
            location=from_shape(Point(1.2241, 6.1362), srid=4326),
            latitude=6.1362, longitude=1.2241,
            is_verified=True, is_active=True,
        ),
        "olympe": Spot(
            id=uuid4(), name="Salle Olympe", category="gym",
            city_id=city.id,
            location=from_shape(Point(1.2188, 6.135), srid=4326),
            latitude=6.135, longitude=1.2188,
            is_verified=True, is_active=True,
        ),
    }
    db.add_all(spots.values())
    await db.flush()

    return {"city": city, "quartiers": q, "spots": spots}


async def make_user(
    db,
    *,
    phone: str,
    city_id: UUID,
    display_name: str,
    gender: str = "woman",
    seeking: str = "men",
    birth_year: int = 1998,
    intention: str = "serious",
    sector: str = "tech",
    tags: list[str] | None = None,
    languages: list[str] | None = None,
    prompts: list[dict] | None = None,
    completeness: float = 0.85,
    is_premium: bool = False,
    selfie_verified: bool = True,
    is_visible: bool = True,
    is_active: bool = True,
    last_active_offset_days: int = 0,
    photos_count: int = 3,
    account_age_days: int = 60,
    language: str = "fr",
) -> User:
    last_active = datetime.now(timezone.utc) - timedelta(
        days=last_active_offset_days
    )
    created_at = datetime.now(timezone.utc) - timedelta(days=account_age_days)

    user = User(
        id=uuid4(),
        phone_hash=hash_phone(phone),
        phone_country_code="228",
        is_phone_verified=True,
        is_selfie_verified=selfie_verified,
        is_active=is_active,
        is_visible=is_visible,
        is_premium=is_premium,
        city_id=city_id,
        last_active_at=last_active,
        language=language,
    )
    db.add(user)
    await db.flush()

    profile = Profile(
        user_id=user.id,
        display_name=display_name,
        birth_date=date(birth_year, 6, 1),
        gender=gender,
        seeking_gender=seeking,
        intention=intention,
        sector=sector,
        rhythm="early_bird",
        tags=tags or [],
        languages=languages or ["fr"],
        prompts=prompts or [],
        seeking_age_min=18,
        seeking_age_max=50,
        profile_completeness=completeness,
        behavior_multiplier=1.0,
    )
    db.add(profile)

    for i in range(photos_count):
        db.add(
            Photo(
                id=uuid4(),
                user_id=user.id,
                original_url=f"http://x/{user.id}/{i}.jpg",
                thumbnail_url=f"http://x/{user.id}/{i}_t.jpg",
                medium_url=f"http://x/{user.id}/{i}_m.jpg",
                display_order=i,
                is_verified_selfie=(i == 0 and selfie_verified),
                content_hash=f"hash-{user.id}-{i}",
                width=800, height=1200, file_size_bytes=100_000,
                moderation_status="approved",
            )
        )

    await db.flush()
    user.created_at = created_at
    await db.flush()
    await db.refresh(user)
    return user


async def attach_quartier(db, user: User, quartier: Quartier, relation="lives"):
    db.add(
        UserQuartier(
            user_id=user.id,
            quartier_id=quartier.id,
            relation_type=relation,
            is_primary=(relation == "lives"),
        )
    )
    await db.flush()


async def attach_spot(
    db,
    user: User,
    spot: Spot,
    fidelity_level: str = "confirmed",
    fidelity_score: float = 0.6,
    days_ago: int = 5,
):
    db.add(
        UserSpot(
            user_id=user.id,
            spot_id=spot.id,
            fidelity_level=fidelity_level,
            fidelity_score=fidelity_score,
            checkin_count=3,
            last_checkin_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        )
    )
    await db.flush()


def headers_for(user: User) -> dict:
    token = create_access_token(user.id)
    return {"Authorization": f"Bearer {token}"}


async def seed_ama_and_kofi(db) -> dict:
    """
    Scénario classique : Ama (woman, seeking men) + Kofi (man, seeking women)
    avec spots et quartiers en commun → match fort.
    """
    base = await seed_city_lome(db)
    q = base["quartiers"]
    s = base["spots"]

    ama = await make_user(
        db,
        phone="+22890000001",
        city_id=base["city"].id,
        display_name="Ama",
        gender="woman",
        seeking="men",
        birth_year=1999,
        tags=["foodie", "cinema"],
        prompts=[
            {"question": "Un dimanche parfait c'est...",
             "answer": "Grasse mat + brunch", "prompt_id": "sunday"},
        ],
    )
    kofi = await make_user(
        db,
        phone="+22890000002",
        city_id=base["city"].id,
        display_name="Kofi",
        gender="man",
        seeking="women",
        birth_year=1996,
        tags=["foodie", "music"],
        prompts=[
            {"question": "Mon maquis préféré parce que...",
             "answer": "Le poulet braisé", "prompt_id": "maquis"},
        ],
    )

    await attach_quartier(db, ama, q["tokoin"], "lives")
    await attach_quartier(db, ama, q["be"], "hangs")
    await attach_spot(db, ama, s["cafe21"], "regular", 0.8)
    await attach_spot(db, ama, s["olympe"], "confirmed", 0.5)

    await attach_quartier(db, kofi, q["tokoin"], "lives")
    await attach_quartier(db, kofi, q["djidjole"], "hangs")
    await attach_spot(db, kofi, s["cafe21"], "regular", 0.7)
    await attach_spot(db, kofi, s["tonton"], "confirmed", 0.6)

    await db.commit()
    await db.refresh(ama)
    await db.refresh(kofi)
    return {**base, "ama": ama, "kofi": kofi}
