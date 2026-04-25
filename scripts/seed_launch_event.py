from __future__ import annotations

"""
Seed l'event de lancement de la beta Lomé.

Crée un Event ancré sur Café 21 (Tokoin) avec une date paramétrable.
Idempotent : si un event avec le même `slug` existe déjà, on update les
champs métadata et on ne touche pas au `id`.

Usage :
    # Date par défaut (J+7 à 19h heure Lomé)
    python -m scripts.seed_launch_event

    # Date custom (ISO 8601 UTC)
    python -m scripts.seed_launch_event --starts-at 2026-05-15T19:00:00+00:00

    # Slug custom (si on veut différencier plusieurs runs)
    python -m scripts.seed_launch_event --slug flaam-launch-lome-v2

Sortie :
    Affiche l'event_id, la date, le venue, le QR/lien admin.
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import async_session
from app.models.city import City
from app.models.event import Event
from app.models.spot import Spot


DEFAULT_SLUG = "flaam-launch-lome"
DEFAULT_VENUE_NAME = "Café 21"
DEFAULT_TITLE_FR = "Soirée de lancement Flaam — Lomé"
DEFAULT_DESCRIPTION = (
    "On lance Flaam à Lomé. Viens, scanne, allume une flamme. "
    "Pas de numéro à donner — montre ta flamme, ils scannent. "
    "Match créé sur place, vous discutez après. "
    "Bar ouvert, musique, cadre détendu."
)


async def _get_lome(db) -> City:
    res = await db.execute(
        select(City).where(City.name == "Lomé"),
    )
    city = res.scalar_one_or_none()
    if city is None:
        raise SystemExit(
            "City 'Lomé' introuvable. Lance d'abord : "
            "python -m scripts.seed_base_data",
        )
    return city


async def _get_venue(db, city_id) -> Spot:
    res = await db.execute(
        select(Spot).where(Spot.city_id == city_id, Spot.name == DEFAULT_VENUE_NAME),
    )
    spot = res.scalar_one_or_none()
    if spot is None:
        raise SystemExit(
            f"Spot '{DEFAULT_VENUE_NAME}' introuvable. Lance d'abord : "
            "python -m scripts.seed_base_data",
        )
    return spot


async def main(starts_at: datetime, slug: str) -> None:
    async with async_session() as db:
        city = await _get_lome(db)
        venue = await _get_venue(db, city.id)

        ends_at = starts_at + timedelta(hours=4)

        # Idempotence par slug
        existing = (
            await db.execute(select(Event).where(Event.slug == slug))
        ).scalar_one_or_none()

        if existing is not None:
            existing.title = DEFAULT_TITLE_FR
            existing.description = DEFAULT_DESCRIPTION
            existing.starts_at = starts_at
            existing.ends_at = ends_at
            existing.spot_id = venue.id
            existing.city_id = city.id
            existing.category = "launch"
            existing.status = "published"
            existing.is_active = True
            existing.is_approved = True
            existing.is_admin_created = True
            event = existing
            action = "updated"
        else:
            event = Event(
                title=DEFAULT_TITLE_FR,
                description=DEFAULT_DESCRIPTION,
                spot_id=venue.id,
                city_id=city.id,
                starts_at=starts_at,
                ends_at=ends_at,
                category="launch",
                status="published",
                is_active=True,
                is_approved=True,
                is_admin_created=True,
                slug=slug,
            )
            db.add(event)
            action = "created"

        await db.commit()
        await db.refresh(event)

        print("─" * 60)
        print(f"Event {action} :")
        print(f"  id       : {event.id}")
        print(f"  slug     : {event.slug}")
        print(f"  title    : {event.title}")
        print(f"  starts   : {event.starts_at.isoformat()}")
        print(f"  ends     : {event.ends_at.isoformat() if event.ends_at else 'n/a'}")
        print(f"  venue    : {venue.name} ({venue.latitude}, {venue.longitude})")
        print(f"  city     : {city.name}")
        print(f"  status   : {event.status}")
        print("─" * 60)
        print("Deep link : flaam://events/" + str(event.id))
        print("Public URL : https://flaam.app/events/" + (event.slug or ""))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed l'event de lancement Lomé.",
    )
    parser.add_argument(
        "--starts-at",
        type=str,
        default=None,
        help=(
            "Date de début ISO-8601 (ex: 2026-05-15T19:00:00+00:00). "
            "Défaut : maintenant + 7 jours à 19h UTC."
        ),
    )
    parser.add_argument(
        "--slug",
        type=str,
        default=DEFAULT_SLUG,
        help=f"Slug stable de l'event. Défaut : {DEFAULT_SLUG}",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.starts_at:
        starts_at = datetime.fromisoformat(args.starts_at)
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=timezone.utc)
    else:
        # Défaut : J+7 à 19h UTC (≈ 19h locale Lomé, GMT+0 en hiver)
        target = datetime.now(timezone.utc) + timedelta(days=7)
        starts_at = target.replace(hour=19, minute=0, second=0, microsecond=0)

    asyncio.run(main(starts_at=starts_at, slug=args.slug))
