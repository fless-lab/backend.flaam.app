from __future__ import annotations

"""
Service de calcul de proximity entre 2 quartiers (#216, R&D Phase 2).

Trois branches selon la donnée disponible :

1. Les 2 quartiers ont `area` (Polygon WGS84) :
   - Si les zones s'intersectent → score haut (0.85 + bonus overlap)
   - Sinon → distance centroïdes normalisée par city.diameter_km
2. Pas d'area sur l'un ou l'autre, mais lat/lng OK :
   - Distance centroïdes normalisée (legacy fallback)
3. Données manquantes → 0.5 neutre

Cache Redis avec TTL pour éviter de recalculer à chaque requête feed.
La key normalise l'ordre des IDs (proximity est symétrique).

Toggle via settings.geolocated_quartiers_enabled. OFF = ne pas appeler
ce service, retomber sur l'ancien cache préchargé dans geo_scorer.
"""

from math import asin, cos, radians, sin, sqrt
from typing import Optional
from uuid import UUID

import redis.asyncio as aioredis
from geoalchemy2.shape import to_shape
from shapely.geometry import Polygon, Point
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.city import City
from app.models.quartier import Quartier


_settings = get_settings()


# ── Geo helpers ──────────────────────────────────────────────────────


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance grand cercle en km."""
    rlat1, rlng1 = radians(lat1), radians(lng1)
    rlat2, rlng2 = radians(lat2), radians(lng2)
    dlat = rlat2 - rlat1
    dlng = rlng2 - rlng1
    h = sin(dlat / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin(dlng / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(h))


def _polygon_from_quartier(q: Quartier) -> Polygon | None:
    if q.area is None:
        return None
    try:
        shape = to_shape(q.area)
        return shape if isinstance(shape, Polygon) else None
    except Exception:
        return None


# ── Compute logic ────────────────────────────────────────────────────


def compute_proximity_sync(
    a: Quartier,
    b: Quartier,
    city_diameter_km: float | None,
    overlap_base: float = 0.85,
    overlap_amplitude: float = 0.15,
) -> float:
    """
    Calcule le score sans I/O (testable en isolation).
    Retourne un float ∈ [0, 1].

    Les multiplicateurs `overlap_base` et `overlap_amplitude` sont
    paramétrables via Redis (MATCHING_DEFAULTS) — les callers async
    (`get_proximity` + `_load_dynamic_proximity_cache`) les passent
    en paramètres après lecture config. Les valeurs par défaut ici
    n'agissent qu'en cas d'appel direct sans passer par get_configs.
    """
    if a.id == b.id:
        return 1.0

    diameter = city_diameter_km or _settings.geolocated_default_city_diameter_km
    if diameter <= 0:
        diameter = _settings.geolocated_default_city_diameter_km

    poly_a = _polygon_from_quartier(a)
    poly_b = _polygon_from_quartier(b)

    # Branche 1 : les 2 ont une zone réelle.
    if poly_a is not None and poly_b is not None:
        if poly_a.intersects(poly_b):
            # Overlap : ratio par rapport à la plus petite zone, pour
            # que "petit quartier complètement dans grand" donne 1.0.
            inter = poly_a.intersection(poly_b).area
            min_area = min(poly_a.area, poly_b.area)
            overlap_ratio = inter / min_area if min_area > 0 else 0.0
            return min(1.0, overlap_base + overlap_amplitude * overlap_ratio)
        # Zones distinctes mais on a quand même les centroïdes.
        return _centroid_score(
            poly_a.centroid.y, poly_a.centroid.x,
            poly_b.centroid.y, poly_b.centroid.x,
            diameter,
        )

    # Branche 2 : fallback sur lat/lng (legacy).
    if a.latitude is not None and b.latitude is not None:
        return _centroid_score(
            a.latitude, a.longitude,
            b.latitude, b.longitude,
            diameter,
        )

    # Branche 3 : données manquantes, neutre.
    return 0.5


def _centroid_score(
    lat_a: float, lng_a: float,
    lat_b: float, lng_b: float,
    diameter_km: float,
) -> float:
    dist = _haversine_km(lat_a, lng_a, lat_b, lng_b)
    score = 1.0 - (dist / diameter_km)
    return max(0.0, min(1.0, score))


# ── Cache Redis + load Quartier/City ─────────────────────────────────


def _cache_key(city_id: UUID, a_id: UUID, b_id: UUID) -> str:
    # Normalise l'ordre pour exploiter la symétrie proximity(a,b)=proximity(b,a)
    a, b = sorted([str(a_id), str(b_id)])
    return f"proximity:{city_id}:{a}:{b}"


async def get_proximity(
    quartier_a_id: UUID,
    quartier_b_id: UUID,
    city_id: UUID,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> float:
    """
    Point d'entrée — vérifie le cache Redis, sinon charge les quartiers
    + city, calcule, stocke et retourne.

    Les multiplicateurs (overlap_base, overlap_amplitude) sont lus depuis
    Redis via config_service — modifiables à chaud sans redémarrage.
    """
    if quartier_a_id == quartier_b_id:
        return 1.0

    key = _cache_key(city_id, quartier_a_id, quartier_b_id)
    cached = await redis.get(key)
    if cached is not None:
        try:
            return float(cached)
        except (TypeError, ValueError):
            pass  # cache corrompu, on recalcule

    # Charge les 2 quartiers + city en 2 requêtes
    res_q = await db.execute(
        select(Quartier).where(Quartier.id.in_([quartier_a_id, quartier_b_id]))
    )
    quartiers = {q.id: q for q in res_q.scalars()}
    a = quartiers.get(quartier_a_id)
    b = quartiers.get(quartier_b_id)
    if a is None or b is None:
        return 0.5

    res_c = await db.execute(select(City).where(City.id == city_id))
    city = res_c.scalar_one_or_none()
    diameter = city.diameter_km if city else None

    # Lecture des multiplicateurs Redis (60s cache via get_configs)
    from app.services.config_service import get_configs
    cfg = await get_configs(
        ("geo_overlap_score_base", "geo_overlap_score_amplitude"),
        redis, db,
    )
    base = cfg.get("geo_overlap_score_base", 0.85)
    amplitude = cfg.get("geo_overlap_score_amplitude", 0.15)

    score = compute_proximity_sync(a, b, diameter, base, amplitude)
    await redis.set(
        key, str(score), ex=_settings.geolocated_proximity_cache_ttl_seconds,
    )
    return score


async def invalidate_for_city(
    city_id: UUID, redis: aioredis.Redis
) -> int:
    """
    Invalide tout le cache proximity pour une ville donnée. À appeler
    après ajout/édition de quartier ou recalcul de city.diameter_km.

    Retourne le nombre de keys supprimées.
    """
    pattern = f"proximity:{city_id}:*"
    deleted = 0
    async for key in redis.scan_iter(match=pattern):
        await redis.delete(key)
        deleted += 1
    return deleted


async def recompute_city_diameter(
    city_id: UUID,
    db: AsyncSession,
    redis: aioredis.Redis | None = None,
) -> float | None:
    """
    Recalcule city.diameter_km comme la max distance haversine entre
    2 centroïdes de quartiers de la ville (#218 R&D Phase 4).

    À appeler après :
    - Ajout d'un nouveau quartier dans la ville
    - Édition de quartier.area (centroïde déplacé)
    - Migration data initiale (1 fois par ville)

    Si `redis` fourni, invalide aussi le cache proximity (les scores
    dépendent du diameter).

    Retourne la nouvelle valeur ou None si < 2 quartiers (ville trop
    petite pour un diameter significatif).
    """
    res = await db.execute(
        select(Quartier.latitude, Quartier.longitude)
        .where(Quartier.city_id == city_id)
    )
    points = list(res.all())
    if len(points) < 2:
        return None

    max_d = 0.0
    for i, (lat_a, lng_a) in enumerate(points):
        for lat_b, lng_b in points[i + 1:]:
            d = _haversine_km(lat_a, lng_a, lat_b, lng_b)
            if d > max_d:
                max_d = d

    res_city = await db.execute(select(City).where(City.id == city_id))
    city = res_city.scalar_one_or_none()
    if city is not None:
        city.diameter_km = max_d
        await db.commit()

    if redis is not None:
        await invalidate_for_city(city_id, redis)
    return max_d


__all__ = [
    "compute_proximity_sync",
    "get_proximity",
    "invalidate_for_city",
    "recompute_city_diameter",
]
