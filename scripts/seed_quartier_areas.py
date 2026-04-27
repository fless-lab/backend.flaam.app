"""
Seed des polygones quartiers (R&D #215, #199 epic).

Conçu pour être réutilisable sur n'importe quelle ville. Deux modes :

  1. OSM Overpass — fetch automatique des bounds depuis OpenStreetMap
     pour les quartiers ciblés. Lent, sujet aux rate limits Overpass.
     Recommandé pour le 1er seed d'une nouvelle ville.

  2. Hardcoded fallback — utilise un dict POLYGONS_BY_CITY défini en
     bas du fichier (rectangles approximatifs autour du centroïde).
     Fallback rapide quand Overpass est down ou pour CI.

Usage :
    docker compose exec api python -m scripts.seed_quartier_areas \\
        --city Lomé --mode overpass --dry-run
    docker compose exec api python -m scripts.seed_quartier_areas \\
        --city Lomé --mode hardcoded

Idempotent : les quartiers déjà avec area set sont skippés sauf si
--force.

Contrats :
- Skip silencieux si Quartier.area déjà rempli (sauf --force)
- Calcule city.diameter_km à la fin (max distance centroïdes 2 quartiers)
- Recalcule lat/lng = centroïde du polygone si fourni (auto-correction)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Iterable

import httpx
from geoalchemy2.shape import from_shape
from shapely.geometry import Point, Polygon, mapping
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session
from app.models.city import City
from app.models.quartier import Quartier


# ══════════════════════════════════════════════════════════════════════
# Hardcoded fallback : rectangles approximatifs autour des quartiers.
# Largeur ~1.5 km (≈ 0.012° lat × 0.014° lng à 6°N).
# ══════════════════════════════════════════════════════════════════════

# Format : {city_name: {quartier_name: (min_lat, min_lng, max_lat, max_lng)}}
POLYGONS_BY_CITY: dict[str, dict[str, tuple[float, float, float, float]]] = {
    "Lomé": {
        # Approximations grossières — à raffiner via Overpass en prod.
        "Tokoin": (6.150, 1.200, 6.180, 1.230),
        "Bè": (6.130, 1.220, 6.160, 1.250),
        "Djidjolé": (6.155, 1.190, 6.180, 1.215),
        "Nyékonakpoè": (6.120, 1.205, 6.140, 1.225),
        "Agoè": (6.180, 1.180, 6.220, 1.220),
        "Hédzranawoé": (6.170, 1.235, 6.195, 1.260),
        "Kégué": (6.195, 1.215, 6.220, 1.245),
        "Kodjoviakopé": (6.130, 1.190, 6.150, 1.215),
        "Adidogomé": (6.160, 1.140, 6.200, 1.180),
        "Baguida": (6.160, 1.260, 6.200, 1.310),
    },
    # Pour ajouter une ville :
    # "Abidjan": {"Cocody": (5.32, -3.96, 5.40, -3.90), ...},
}


def _bbox_to_polygon(bbox: tuple[float, float, float, float]) -> Polygon:
    """Construit un polygone fermé depuis (min_lat, min_lng, max_lat, max_lng)."""
    min_lat, min_lng, max_lat, max_lng = bbox
    return Polygon([
        (min_lng, min_lat),
        (max_lng, min_lat),
        (max_lng, max_lat),
        (min_lng, max_lat),
        (min_lng, min_lat),
    ])


# ══════════════════════════════════════════════════════════════════════
# Mode OSM Overpass
# ══════════════════════════════════════════════════════════════════════

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

OVERPASS_QUERY = """
[out:json][timeout:30];
(
  relation["name"="{name}"]["place"~"^(suburb|neighbourhood|quarter)$"];
);
out geom;
"""


async def _fetch_osm_polygon(quartier_name: str) -> Polygon | None:
    """
    Fetch les bounds OSM pour un quartier. Retourne None si pas trouvé
    ou si la réponse n'est pas un polygone exploitable.
    """
    query = OVERPASS_QUERY.format(name=quartier_name)
    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            r = await client.post(OVERPASS_URL, data={"data": query})
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        print(f"  ! Overpass error for '{quartier_name}': {exc}")
        return None

    elements = data.get("elements", [])
    if not elements:
        return None
    elem = elements[0]
    members = elem.get("members", [])
    # On reconstruit le polygone à partir des outer ways
    points: list[tuple[float, float]] = []
    for m in members:
        if m.get("type") == "way" and m.get("role") == "outer":
            for node in m.get("geometry", []):
                points.append((node["lon"], node["lat"]))
    if len(points) < 3:
        return None
    if points[0] != points[-1]:
        points.append(points[0])
    try:
        return Polygon(points)
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════
# Seed orchestration
# ══════════════════════════════════════════════════════════════════════


async def _get_city(db: AsyncSession, name: str) -> City | None:
    res = await db.execute(select(City).where(City.name == name))
    return res.scalar_one_or_none()


async def _get_quartiers(db: AsyncSession, city_id) -> list[Quartier]:
    res = await db.execute(
        select(Quartier).where(Quartier.city_id == city_id)
    )
    return list(res.scalars().all())


async def _resolve_polygon(
    name: str, city_name: str, mode: str
) -> Polygon | None:
    if mode == "overpass":
        poly = await _fetch_osm_polygon(name)
        if poly is not None:
            return poly
        # Fallback automatique vers hardcoded si OSM échoue
        bbox = POLYGONS_BY_CITY.get(city_name, {}).get(name)
        if bbox:
            print(f"  → hardcoded fallback for '{name}' (OSM had nothing)")
            return _bbox_to_polygon(bbox)
        return None
    # mode == "hardcoded"
    bbox = POLYGONS_BY_CITY.get(city_name, {}).get(name)
    if bbox is None:
        return None
    return _bbox_to_polygon(bbox)


def _haversine_km(a: Point, b: Point) -> float:
    """Approximation grand cercle simple (sans dépendance numpy)."""
    from math import asin, cos, radians, sin, sqrt

    lat1, lng1 = radians(a.y), radians(a.x)
    lat2, lng2 = radians(b.y), radians(b.x)
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(h))


async def seed_for_city(
    city_name: str,
    mode: str,
    force: bool,
    dry_run: bool,
) -> None:
    async with async_session() as db:
        city = await _get_city(db, city_name)
        if city is None:
            print(f"City '{city_name}' not found in DB. Run seed_base_data first.")
            return
        quartiers = await _get_quartiers(db, city.id)
        if not quartiers:
            print(f"No quartiers found for {city_name}.")
            return

        print(f"Found {len(quartiers)} quartier(s) for {city_name} (mode={mode})")
        updated = 0
        skipped = 0
        centroids: list[Point] = []

        for q in quartiers:
            if q.area is not None and not force:
                print(f"  - {q.name}: skipped (area already set)")
                centroids.append(Point(q.longitude, q.latitude))
                skipped += 1
                continue
            poly = await _resolve_polygon(q.name, city_name, mode)
            if poly is None:
                print(f"  - {q.name}: NO polygon found (will stay legacy lat/lng)")
                centroids.append(Point(q.longitude, q.latitude))
                continue

            centroid = poly.centroid
            print(
                f"  - {q.name}: polygon set, "
                f"centroid=({centroid.y:.4f}, {centroid.x:.4f})"
            )
            if not dry_run:
                q.area = from_shape(poly, srid=4326)
                # Auto-correction du centroïde si on a une vraie zone.
                q.latitude = centroid.y
                q.longitude = centroid.x
            centroids.append(centroid)
            updated += 1

        # Calcul du diameter_km : max distance entre 2 centroïdes
        if len(centroids) >= 2:
            max_d = 0.0
            for i, a in enumerate(centroids):
                for b in centroids[i + 1:]:
                    d = _haversine_km(a, b)
                    if d > max_d:
                        max_d = d
            print(f"  city diameter ≈ {max_d:.2f} km")
            if not dry_run and (city.diameter_km is None or force):
                city.diameter_km = max_d

        if dry_run:
            print(f"\nDRY RUN — {updated} updates pending, {skipped} skipped (rollback)")
            await db.rollback()
        else:
            await db.commit()
            print(f"\nDone. {updated} quartier(s) updated, {skipped} skipped.")


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed quartier polygons")
    parser.add_argument(
        "--city", required=True, help="City name (must exist in DB)"
    )
    parser.add_argument(
        "--mode", choices=("overpass", "hardcoded"), default="hardcoded",
        help="overpass = fetch OSM, hardcoded = use POLYGONS_BY_CITY dict",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-seed even if area is already set",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be done without committing",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    asyncio.run(seed_for_city(
        city_name=args.city,
        mode=args.mode,
        force=args.force,
        dry_run=args.dry_run,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
