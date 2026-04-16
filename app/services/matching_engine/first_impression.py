from __future__ import annotations

"""
First-impression feed (MàJ 7).

Quand une nouvelle femme s'inscrit, ses 3 premiers feeds sont curatés :
on lui montre les profils masculins LES PLUS SAINS (pas les plus
populaires), pas les matchs strictement pertinents pour elle.

Objectif : retenir les femmes au lancement. Si la première impression
c'est 12 profils vides, elle désinstalle.

Critères stricts (config) :
  - completeness ≥ 0.75
  - behavior_multiplier ≥ 1.0
  - photos ≥ 3
Si pas assez de candidats qualité, on complète avec le reste du feed
normal pour garder la taille à 12.

Asymétrique par design : ne s'applique PAS aux nouveaux hommes
(cf. spec §32 gender balance).
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feed_cache import FeedCache
from app.models.photo import Photo
from app.models.profile import Profile
from app.models.user import User


async def _count_feeds_generated(user_id: UUID, db_session: AsyncSession) -> int:
    row = await db_session.execute(
        select(func.count(FeedCache.id)).where(FeedCache.user_id == user_id)
    )
    return int(row.scalar_one() or 0)


async def apply_first_impression(
    user: User,
    feed_ids: list[UUID],
    config: dict[str, float],
    db_session: AsyncSession,
) -> list[UUID]:
    """
    Re-trie `feed_ids` pour les 3 premiers feeds d'une nouvelle femme.
    Retourne la liste re-triée ou telle-quelle si non applicable.
    """
    if not feed_ids:
        return feed_ids

    profile = user.profile
    if profile is None or profile.gender != "woman":
        return feed_ids

    active_n = int(config.get("first_impression_active_feeds", 3))
    feeds_seen = await _count_feeds_generated(user.id, db_session)
    if feeds_seen >= active_n:
        return feed_ids

    min_completeness = config.get("first_impression_min_completeness", 0.75)
    min_behavior = config.get("first_impression_min_behavior", 1.0)
    min_photos = int(config.get("first_impression_min_photos", 3))
    feed_size = len(feed_ids)

    # Charger les profils candidats + leur nombre de photos
    profile_rows = await db_session.execute(
        select(Profile).where(Profile.user_id.in_(feed_ids))
    )
    profiles = {p.user_id: p for p in profile_rows.scalars()}

    photo_rows = await db_session.execute(
        select(Photo.user_id, func.count(Photo.id))
        .where(Photo.user_id.in_(feed_ids))
        .where(Photo.moderation_status != "rejected")
        .group_by(Photo.user_id)
    )
    photo_counts = {uid: int(n) for uid, n in photo_rows.all()}

    qualified: list[UUID] = []
    others: list[UUID] = []
    for cid in feed_ids:
        p = profiles.get(cid)
        if p is None:
            others.append(cid)
            continue
        if (
            (p.profile_completeness or 0.0) >= min_completeness
            and (p.behavior_multiplier or 0.0) >= min_behavior
            and photo_counts.get(cid, 0) >= min_photos
        ):
            qualified.append(cid)
        else:
            others.append(cid)

    # Tri des qualifiés par behavior DESC puis completeness DESC
    qualified.sort(
        key=lambda cid: (
            -(profiles[cid].behavior_multiplier or 0.0),
            -(profiles[cid].profile_completeness or 0.0),
        )
    )

    result = qualified + others
    return result[:feed_size]


__all__ = ["apply_first_impression"]
