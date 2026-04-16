from __future__ import annotations

"""
Préférences implicites (MàJ 6).

Construit un profil type implicite à partir des signaux comportementaux
des 30 derniers jours, puis ajuste le score L3 (lifestyle) d'un candidat.

Principes stricts (à ne PAS violer) :
  - CONTENT-BASED uniquement. Pas de collaborative filtering.
    On regarde ce que TU as regardé en silence, rien d'autre.
  - Cap logarithmique du temps : au-delà de 60s = bruit.
  - Corroboration obligatoire : un temps sans interaction est jeté.
  - Confidence proportionnelle au nombre de signaux.
  - Ajustement L3 borné à ±15% × confidence.
"""

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    REDIS_IMPLICIT_PREFS_KEY,
    REDIS_IMPLICIT_PREFS_TTL_SECONDS,
)
from app.models.behavior_log import BehaviorLog
from app.models.profile import Profile


# ── Bornes de sanitisation ──

_TIME_MIN_SECONDS = 8.0
_TIME_CAP_SECONDS = 60.0
_MIN_SIGNALS_FOR_PROFILE = 5
_CONFIDENCE_SATURATION = 50.0       # au-delà → confiance = 1.0
_MIN_CONFIDENCE_FOR_ADJUSTMENT = 0.3
_ADJUSTMENT_CAP = 0.15              # ±15% max sur L3


def sanitize_time_signal(time_seconds: float, has_corroboration: bool) -> float:
    """
    Transforme le temps brut en signal [0, 1].

    Règles :
      - time < 8s  → 0.0  (pas un signal)
      - sans corroboration → 0.0 (téléphone posé, on jette)
      - time > 60s → 1.0 (cap)
      - entre les deux → log(t/8) / log(60/8)

    Ce test est la pierre angulaire de l'anti-gaming. Pas de raccourci.
    """
    if time_seconds is None or time_seconds < _TIME_MIN_SECONDS:
        return 0.0
    if not has_corroboration:
        return 0.0
    capped = min(float(time_seconds), _TIME_CAP_SECONDS)
    # Log-curve entre _TIME_MIN et _TIME_CAP
    signal = math.log(capped / _TIME_MIN_SECONDS) / math.log(
        _TIME_CAP_SECONDS / _TIME_MIN_SECONDS
    )
    return max(0.0, min(1.0, signal))


def has_corroboration(events: list[dict]) -> bool:
    """
    Vrai si au moins un événement d'interaction existe au-delà du temps passé.
    Chaque event = {"type": str, "data": dict}.
    """
    for e in events:
        etype = e.get("type")
        data = e.get("data") or {}
        if etype == "photo_scrolled":
            return True
        if etype == "prompt_expanded":
            return True
        if etype == "scroll_depth":
            depth = data.get("depth")
            if depth is not None and float(depth) > 0.30:
                return True
    return False


async def compute_implicit_profile(
    user_id: UUID,
    db_session: AsyncSession,
    redis_client: aioredis.Redis,
    *,
    use_cache: bool = True,
) -> dict:
    """
    Construit le profil implicite sur les 30 derniers jours.

    Si < _MIN_SIGNALS_FOR_PROFILE signaux exploitables → profil vide,
    confidence = 0.

    Cache Redis : implicit_prefs:{user_id}, TTL 25h. Sérialisé en JSON.
    """
    cache_key = REDIS_IMPLICIT_PREFS_KEY.format(user_id=str(user_id))
    if use_cache:
        raw = await redis_client.get(cache_key)
        if raw is not None:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                await redis_client.delete(cache_key)

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    rows = await db_session.execute(
        select(BehaviorLog)
        .where(BehaviorLog.user_id == user_id)
        .where(BehaviorLog.created_at >= cutoff)
        .order_by(BehaviorLog.created_at)
    )
    logs = list(rows.scalars())

    if len(logs) < _MIN_SIGNALS_FOR_PROFILE:
        empty = {
            "preferred_tags": {},
            "preferred_sectors": {},
            "rejected_tags": {},
            "rejected_sectors": {},
            "signal_count": len(logs),
            "confidence": 0.0,
        }
        if use_cache:
            await redis_client.set(
                cache_key, json.dumps(empty), ex=REDIS_IMPLICIT_PREFS_TTL_SECONDS
            )
        return empty

    # Grouper par profil cible
    by_target: dict[UUID, list[BehaviorLog]] = defaultdict(list)
    for log in logs:
        if log.target_user_id is None:
            continue
        by_target[log.target_user_id].append(log)

    positive: dict[UUID, float] = {}
    negative: list[UUID] = []

    for target_id, events in by_target.items():
        events_dicts = [
            {"type": e.event_type, "data": e.extra_data or {}} for e in events
        ]
        corroborated = has_corroboration(events_dicts)

        total_time = 0.0
        for e in events:
            if e.event_type == "profile_view_duration" and e.duration_seconds:
                total_time += float(e.duration_seconds)

        time_signal = sanitize_time_signal(total_time, corroborated)
        photo_count = sum(1 for e in events if e.event_type == "photo_scrolled")
        prompt_count = sum(1 for e in events if e.event_type == "prompt_expanded")
        return_count = sum(1 for e in events if e.event_type == "return_visit")

        # Quick skip : action=skip en < 2s et sans photo scrollée
        skip_events = [
            e
            for e in events
            if e.event_type == "profile_action"
            and (e.extra_data or {}).get("action") == "skip"
        ]
        is_quick_skip = (
            len(skip_events) > 0 and total_time < 2.0 and photo_count == 0
        )

        if is_quick_skip:
            negative.append(target_id)
            continue

        engagement = (
            time_signal * 2.0
            + min(photo_count, 5) * 0.3
            + prompt_count * 0.5
            + return_count * 1.0  # return_visit = 3x explicite (spec MàJ 5)
        )
        if engagement > 0.5:
            positive[target_id] = engagement

    # Charger les profils référencés (batch)
    all_ids = list(set(positive.keys()) | set(negative))
    profiles: dict[UUID, Profile] = {}
    if all_ids:
        prof_rows = await db_session.execute(
            select(Profile).where(Profile.user_id.in_(all_ids))
        )
        profiles = {p.user_id: p for p in prof_rows.scalars()}

    preferred_tags: dict[str, float] = defaultdict(float)
    preferred_sectors: dict[str, float] = defaultdict(float)
    rejected_tags: dict[str, float] = defaultdict(float)
    rejected_sectors: dict[str, float] = defaultdict(float)

    for pid, score in positive.items():
        prof = profiles.get(pid)
        if prof is None:
            continue
        for tag in prof.tags or []:
            preferred_tags[tag] += score
        if prof.sector:
            preferred_sectors[prof.sector] += score

    for pid in negative:
        prof = profiles.get(pid)
        if prof is None:
            continue
        for tag in prof.tags or []:
            rejected_tags[tag] += 1.0
        if prof.sector:
            rejected_sectors[prof.sector] += 1.0

    def _normalize(d: dict[str, float]) -> dict[str, float]:
        if not d:
            return {}
        m = max(d.values())
        if m == 0:
            return {}
        return {k: round(v / m, 3) for k, v in d.items()}

    total_signals = len(positive) + len(negative)
    confidence = min(1.0, total_signals / _CONFIDENCE_SATURATION)

    result = {
        "preferred_tags": _normalize(dict(preferred_tags)),
        "preferred_sectors": _normalize(dict(preferred_sectors)),
        "rejected_tags": _normalize(dict(rejected_tags)),
        "rejected_sectors": _normalize(dict(rejected_sectors)),
        "signal_count": total_signals,
        "confidence": round(confidence, 3),
    }
    if use_cache:
        await redis_client.set(
            cache_key, json.dumps(result), ex=REDIS_IMPLICIT_PREFS_TTL_SECONDS
        )
    return result


def apply_implicit_adjustment(
    base_score: float,
    candidate_profile: Profile,
    implicit_profile: dict,
) -> float:
    """
    Ajuste `base_score` (∈ [0, 1]) en fonction du profil implicite.

    Règles :
      - confidence < 0.3 → pas d'ajustement.
      - bonus = Σ preferred_tags[tag] + preferred_sectors[sector] (normalisé)
      - malus = Σ rejected_tags[tag] + rejected_sectors[sector] (normalisé)
      - adjustment = (bonus - malus) × confidence × 0.15
      - adjustment clampé à ±0.15

    Retourne score ∈ [0, 1].
    """
    confidence = float(implicit_profile.get("confidence", 0.0))
    if confidence < _MIN_CONFIDENCE_FOR_ADJUSTMENT:
        return base_score

    candidate_tags = list(candidate_profile.tags or [])
    candidate_sector = candidate_profile.sector or ""

    pref_tags = implicit_profile.get("preferred_tags", {}) or {}
    pref_sectors = implicit_profile.get("preferred_sectors", {}) or {}
    rej_tags = implicit_profile.get("rejected_tags", {}) or {}
    rej_sectors = implicit_profile.get("rejected_sectors", {}) or {}

    bonus = sum(pref_tags.get(t, 0.0) for t in candidate_tags)
    if candidate_sector:
        bonus += pref_sectors.get(candidate_sector, 0.0)

    malus = sum(rej_tags.get(t, 0.0) for t in candidate_tags)
    if candidate_sector:
        malus += rej_sectors.get(candidate_sector, 0.0)

    if candidate_tags:
        bonus /= len(candidate_tags)
        malus /= len(candidate_tags)

    adjustment = (bonus - malus) * confidence * _ADJUSTMENT_CAP
    adjustment = max(-_ADJUSTMENT_CAP, min(_ADJUSTMENT_CAP, adjustment))
    return max(0.0, min(1.0, base_score + adjustment))


__all__ = [
    "sanitize_time_signal",
    "has_corroboration",
    "compute_implicit_profile",
    "apply_implicit_adjustment",
]
