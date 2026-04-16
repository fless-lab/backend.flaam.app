from __future__ import annotations

"""
GDPR service — suppression de compte en 3 phases (§17).

Phase 1 (immédiat, ce fichier) :
    - Anonymise profile : display_name="Utilisateur supprimé", prompts=[],
      tags=[], languages=[]
    - Marque photos en is_deleted=True (fichiers disque laissés pour
      Phase 2 — précision B de la spec S10)
    - Ferme tous les matches actifs (status="expired", unmatched_at)
    - Purge Redis : feed, behavior, behavior_stats, implicit_prefs

Les flags User.is_deleted/deleted_at/is_active/is_visible sont settés
par le handler avant l'appel (cohérence avec auth.py). La mise à jour
d'AccountHistory est déléguée à abuse_prevention_service.update_history_on_deletion
(qui gère aussi device_fingerprints + risk_score).

Phase 2 (T+7j) : suppression physique des fichiers photos disque
    — Celery Beat, implémentation S11.

Phase 3 (T+30j) : DROP de la row User. AccountHistory survit pour la
détection anti-récidive.

CLAUDE.md : pas de db.commit() dans les services. Le handler commit.
"""

from datetime import datetime, timezone
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    REDIS_BEHAVIOR_KEY,
    REDIS_BEHAVIOR_STATS_KEY,
    REDIS_IMPLICIT_PREFS_KEY,
)
from app.models.match import Match
from app.models.photo import Photo

log = structlog.get_logger()


ANONYMIZED_DISPLAY_NAME = "Utilisateur supprimé"


async def apply_phase1_db_changes(user, db: AsyncSession) -> dict:
    """
    Phase 1 RGPD — modifications DB (pas de commit).

    Prérequis : le handler a déjà setté user.is_deleted/deleted_at/
    is_active/is_visible et appelé update_history_on_deletion.

    Idempotent : si user.profile déjà anonymisé, on ré-anonymise sans impact.

    Retourne {photos_marked, matches_closed}.
    """
    now = datetime.now(timezone.utc)

    # ── Profile anonymize ──
    if user.profile is not None:
        user.profile.display_name = ANONYMIZED_DISPLAY_NAME
        user.profile.prompts = []
        user.profile.tags = []
        user.profile.languages = []

    # ── Photos : soft delete, fichier laissé pour Phase 2 ──
    photos_rows = (
        await db.execute(select(Photo).where(Photo.user_id == user.id))
    ).scalars().all()
    for p in photos_rows:
        p.is_deleted = True
        p.moderation_status = "deleted"

    # ── Matches : close all active ──
    active_matches = (
        await db.execute(
            select(Match).where(
                (Match.user_a_id == user.id) | (Match.user_b_id == user.id),
                Match.status.in_(("pending", "matched")),
            )
        )
    ).scalars().all()
    for m in active_matches:
        m.status = "expired"
        m.unmatched_at = now
        m.unmatched_by = user.id

    return {
        "photos_marked": len(photos_rows),
        "matches_closed": len(active_matches),
    }


async def purge_user_redis_keys(user_id: UUID, redis: aioredis.Redis) -> None:
    """
    Purge les clés Redis utilisateur (feed, behavior, implicit_prefs).
    À appeler après commit DB.
    """
    uid_str = str(user_id)
    redis_keys = [
        f"feed:{uid_str}",
        REDIS_BEHAVIOR_KEY.format(user_id=uid_str),
        REDIS_BEHAVIOR_STATS_KEY.format(user_id=uid_str),
        REDIS_IMPLICIT_PREFS_KEY.format(user_id=uid_str),
    ]
    await redis.delete(*redis_keys)


__all__ = [
    "apply_phase1_db_changes",
    "purge_user_redis_keys",
    "ANONYMIZED_DISPLAY_NAME",
]
