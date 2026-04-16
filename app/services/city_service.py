from __future__ import annotations

"""
City service — MàJ villes/pays + §5.

- get_cities_by_country(country_code) : toutes les villes sauf hidden,
  enrichies avec le compteur waitlist (si teaser).
- get_available_countries() : pays avec au moins une ville non-hidden.
- get_launch_status(city_id) : état waitlist + phase pour une ville.
- join_waitlist(user, city_id) : délègue à waitlist_service.
"""

from collections import defaultdict
from uuid import UUID

import structlog
from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.city import City
from app.models.user import User
from app.models.waitlist_entry import WaitlistEntry
from app.services import waitlist_service

log = structlog.get_logger()


# ── Listing ──────────────────────────────────────────────────────────

async def get_cities_by_country(
    country_code: str, db: AsyncSession
) -> dict:
    result = await db.execute(
        select(City)
        .where(
            City.country_code == country_code.upper(),
            City.phase != "hidden",
        )
        .order_by(City.display_order, City.name)
    )
    cities = list(result.scalars().all())
    if not cities:
        raise AppException(status.HTTP_404_NOT_FOUND, "country_not_available")

    # Compteurs waitlist par city_id
    counts = await _waitlist_counts_for_cities([c.id for c in cities], db)

    country_name = cities[0].country_name
    country_flag = cities[0].country_flag

    out_cities = []
    for c in cities:
        total = counts.get(c.id, 0)
        selectable = c.phase in ("launch", "growth", "stable")
        entry = {
            "id": c.id,
            "name": c.name,
            "country_code": c.country_code,
            "country_name": c.country_name,
            "country_flag": c.country_flag,
            "phase": c.phase,
            "selectable": selectable,
            "waitlist": None,
        }
        if c.phase == "teaser":
            entry["waitlist"] = {
                "total_registered": total,
                "threshold": c.waitlist_threshold,
                "remaining": max(0, c.waitlist_threshold - total),
            }
        out_cities.append(entry)

    return {
        "country_code": country_code.upper(),
        "country_name": country_name,
        "country_flag": country_flag,
        "cities": out_cities,
    }


async def _waitlist_counts_for_cities(
    city_ids: list[UUID], db: AsyncSession
) -> dict[UUID, int]:
    if not city_ids:
        return {}
    result = await db.execute(
        select(WaitlistEntry.city_id, func.count(WaitlistEntry.id))
        .where(WaitlistEntry.city_id.in_(city_ids))
        .group_by(WaitlistEntry.city_id)
    )
    return {cid: count for cid, count in result.all()}


# ── Countries ────────────────────────────────────────────────────────

async def get_available_countries(db: AsyncSession) -> dict:
    result = await db.execute(
        select(City).where(City.phase != "hidden")
    )
    cities = list(result.scalars().all())

    by_country: dict[str, dict] = defaultdict(
        lambda: {
            "country_code": "",
            "country_name": "",
            "country_flag": None,
            "phone_prefix": None,
            "active_cities_count": 0,
            "teaser_cities_count": 0,
        }
    )
    for c in cities:
        bucket = by_country[c.country_code]
        bucket["country_code"] = c.country_code
        bucket["country_name"] = c.country_name
        bucket["country_flag"] = c.country_flag or bucket["country_flag"]
        bucket["phone_prefix"] = c.phone_prefix or bucket["phone_prefix"]
        if c.phase in ("launch", "growth", "stable"):
            bucket["active_cities_count"] += 1
        elif c.phase == "teaser":
            bucket["teaser_cities_count"] += 1

    countries = sorted(by_country.values(), key=lambda b: b["country_name"])
    return {"countries": countries}


# ── Launch status ────────────────────────────────────────────────────

async def get_launch_status(city_id: UUID, db: AsyncSession) -> dict:
    city = await db.get(City, city_id)
    if city is None or city.phase == "hidden":
        raise AppException(status.HTTP_404_NOT_FOUND, "city_not_found")

    total_res = await db.execute(
        select(func.count(WaitlistEntry.id)).where(
            WaitlistEntry.city_id == city_id
        )
    )
    total = total_res.scalar_one() or 0

    male_res = await db.execute(
        select(func.count(WaitlistEntry.id)).where(
            WaitlistEntry.city_id == city_id, WaitlistEntry.gender == "male"
        )
    )
    male = male_res.scalar_one() or 0

    female_res = await db.execute(
        select(func.count(WaitlistEntry.id)).where(
            WaitlistEntry.city_id == city_id,
            WaitlistEntry.gender == "female",
        )
    )
    female = female_res.scalar_one() or 0

    return {
        "city_id": city.id,
        "phase": city.phase,
        "total_registered": total,
        "male_registered": male,
        "female_registered": female,
        "waitlist_threshold": city.waitlist_threshold,
        "remaining_to_launch": max(0, city.waitlist_threshold - total),
    }


# ── Join waitlist (facade) ───────────────────────────────────────────

async def join_waitlist(user: User, city_id: UUID, db: AsyncSession) -> dict:
    city = await db.get(City, city_id)
    if city is None or city.phase == "hidden":
        raise AppException(status.HTTP_404_NOT_FOUND, "city_not_found")
    return await waitlist_service.process_waitlist_join(user, city_id, db)


__all__ = [
    "get_cities_by_country",
    "get_available_countries",
    "get_launch_status",
    "join_waitlist",
]
