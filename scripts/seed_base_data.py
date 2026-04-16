from __future__ import annotations

"""
Seed des données de base : pays (TG, CI), villes (Lomé launch, Kara/Sokodé
teaser, Abidjan launch), quartiers de Lomé + graphe de proximité, et
quelques spots d'exemple (Café 21, Salle Olympe, Chez Tonton).

Usage :
    python -m scripts.seed_base_data

Idempotent : on teste l'existence par `(name, country_code)` sur City,
`(name, city_id)` sur Quartier, et `name` sur Spot. Pas d'upsert —
on skip simplement si déjà présent.
"""

import asyncio
import math
from uuid import UUID

from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import select

from app.db.session import async_session
from app.models.city import City
from app.models.quartier import Quartier
from app.models.quartier_proximity import QuartierProximity
from app.models.spot import Spot


# ── Pays / villes ────────────────────────────────────────────────────

COUNTRIES = [
    {
        "country_code": "TG",
        "country_name": "Togo",
        "country_flag": "🇹🇬",
        "phone_prefix": "+228",
        "timezone": "Africa/Lome",
        "currency_code": "XOF",
        "cities": [
            {
                "name": "Lomé",
                "phase": "launch",
                "display_order": 10,
                "premium_price_monthly": 5000,
                "premium_price_weekly": 1500,
            },
            {
                "name": "Kara",
                "phase": "teaser",
                "display_order": 20,
                "premium_price_monthly": 5000,
                "premium_price_weekly": 1500,
            },
            {
                "name": "Sokodé",
                "phase": "teaser",
                "display_order": 30,
                "premium_price_monthly": 5000,
                "premium_price_weekly": 1500,
            },
        ],
    },
    {
        "country_code": "CI",
        "country_name": "Côte d'Ivoire",
        "country_flag": "🇨🇮",
        "phone_prefix": "+225",
        "timezone": "Africa/Abidjan",
        "currency_code": "XOF",
        "cities": [
            {
                "name": "Abidjan",
                "phase": "launch",
                "display_order": 10,
                "premium_price_monthly": 5000,
                "premium_price_weekly": 1500,
            },
        ],
    },
]


# ── Quartiers de Lomé (coordonnées approximatives) ───────────────────

LOME_QUARTIERS = [
    {"name": "Tokoin", "latitude": 6.1580, "longitude": 1.2115},
    {"name": "Bè", "latitude": 6.1340, "longitude": 1.2210},
    {"name": "Djidjolé", "latitude": 6.1670, "longitude": 1.1955},
    {"name": "Agoè", "latitude": 6.1995, "longitude": 1.1890},
    {"name": "Nyékonakpoè", "latitude": 6.1310, "longitude": 1.2090},
]


# ── Spots exemple (proches de Tokoin/Bè) ─────────────────────────────

SAMPLE_SPOTS = [
    {
        "name": "Café 21",
        "category": "cafe",
        "latitude": 6.1725,
        "longitude": 1.2137,
        "address": "Rue du 24 Janvier, Tokoin",
    },
    {
        "name": "Salle Olympe",
        "category": "gym",
        "latitude": 6.1351,
        "longitude": 1.2188,
        "address": "Boulevard de la Paix, Bè",
    },
    {
        "name": "Chez Tonton",
        "category": "restaurant",
        "latitude": 6.1362,
        "longitude": 1.2241,
        "address": "Bè-Apéyémé",
    },
]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def seed_countries_and_cities(session) -> dict[str, dict]:
    """
    Retourne un dict :
        {"TG": {"Lomé": City, ...}, "CI": {"Abidjan": City}}
    """
    out: dict[str, dict] = {}
    for country in COUNTRIES:
        out[country["country_code"]] = {}
        for city_spec in country["cities"]:
            stmt = select(City).where(
                City.country_code == country["country_code"],
                City.name == city_spec["name"],
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is not None:
                out[country["country_code"]][city_spec["name"]] = existing
                continue
            city = City(
                name=city_spec["name"],
                country_code=country["country_code"],
                country_name=country["country_name"],
                country_flag=country["country_flag"],
                phone_prefix=country["phone_prefix"],
                timezone=country["timezone"],
                currency_code=country["currency_code"],
                premium_price_monthly=city_spec["premium_price_monthly"],
                premium_price_weekly=city_spec["premium_price_weekly"],
                min_weekly_visibility=15,
                is_active=True,
                phase=city_spec["phase"],
                display_order=city_spec["display_order"],
                waitlist_threshold=500,
            )
            session.add(city)
            out[country["country_code"]][city_spec["name"]] = city
    await session.flush()
    return out


async def seed_quartiers(session, lome_city: City) -> list[Quartier]:
    quartiers: list[Quartier] = []
    for spec in LOME_QUARTIERS:
        stmt = select(Quartier).where(
            Quartier.city_id == lome_city.id, Quartier.name == spec["name"]
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            quartiers.append(existing)
            continue
        q = Quartier(
            name=spec["name"],
            city_id=lome_city.id,
            latitude=spec["latitude"],
            longitude=spec["longitude"],
        )
        session.add(q)
        quartiers.append(q)
    await session.flush()
    return quartiers


async def seed_proximity(session, quartiers: list[Quartier]) -> None:
    pairs = []
    for i, qa in enumerate(quartiers):
        for qb in quartiers[i + 1 :]:
            dist = _haversine_km(
                qa.latitude, qa.longitude, qb.latitude, qb.longitude
            )
            pairs.append((qa, qb, dist))

    if not pairs:
        return
    max_dist = max(d for _, _, d in pairs)

    for qa, qb, dist in pairs:
        a, b = sorted((qa, qb), key=lambda q: str(q.id))
        stmt = select(QuartierProximity).where(
            QuartierProximity.quartier_a_id == a.id,
            QuartierProximity.quartier_b_id == b.id,
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            continue
        score = max(0.05, 1.0 - (dist / max_dist))
        session.add(
            QuartierProximity(
                quartier_a_id=a.id,
                quartier_b_id=b.id,
                proximity_score=round(score, 4),
                distance_km=round(dist, 2),
            )
        )


async def seed_spots(session, lome_city: City) -> None:
    for spec in SAMPLE_SPOTS:
        stmt = select(Spot).where(
            Spot.city_id == lome_city.id, Spot.name == spec["name"]
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            continue
        geom = from_shape(Point(spec["longitude"], spec["latitude"]), srid=4326)
        session.add(
            Spot(
                name=spec["name"],
                category=spec["category"],
                city_id=lome_city.id,
                location=geom,
                latitude=spec["latitude"],
                longitude=spec["longitude"],
                address=spec["address"],
                is_verified=True,
                is_active=True,
            )
        )


async def run() -> None:
    async with async_session() as session:
        cities_by_country = await seed_countries_and_cities(session)
        lome = cities_by_country.get("TG", {}).get("Lomé")
        if lome is not None:
            quartiers = await seed_quartiers(session, lome)
            await seed_proximity(session, quartiers)
            await seed_spots(session, lome)
        await session.commit()
    print("seed OK")


if __name__ == "__main__":
    asyncio.run(run())
