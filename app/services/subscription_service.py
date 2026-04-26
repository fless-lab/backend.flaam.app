from __future__ import annotations

"""
Subscription service — gère le gel doux quand un premium expire.

Principe produit non-négociable (CLAUDE.md) :
    Premium expiré = gel doux, PAS suppression.
    Les quartiers/spots extras sont désactivés du matching (is_active_in_matching=false).
    Quand l'user re-souscrit, tout est réactivé intégralement.

Limites free (spec §business-model) :
    - 3 quartiers actifs : max 1 lives + 1 works + 1 hangs
    - 3 quartiers "interested" actifs
    - 5 spots actifs

Règle de sélection : on garde les plus anciens (premier ajouté = plus
"authentique"). Les suivants, par ordre created_at ASC, passent en
is_active_in_matching=False.
"""

from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription import Subscription
from app.models.user import User
from app.models.user_quartier import UserQuartier
from app.models.user_spot import UserSpot

log = structlog.get_logger()


# Limites free (spec §business-model). L'admin peut les modifier via
# matching_config dans une future itération.
FREE_QUARTIER_LIMITS_PER_TYPE: dict[str, int] = {
    "lives": 1,
    "works": 1,
    "interested": 3,
}
FREE_SPOT_LIMIT = 5


async def downgrade_user_limits(user: User, db: AsyncSession) -> dict:
    """
    Gel doux : passe en is_active_in_matching=False tout ce qui dépasse
    les quotas free. Met is_premium=False.

    Retourne un résumé : {quartiers_frozen: int, spots_frozen: int}.
    """
    # Quartiers : un par type (lives/works/hangs) + 3 interested.
    # Ordre created_at ASC → on garde les plus anciens.
    rows = (
        await db.execute(
            select(UserQuartier)
            .where(UserQuartier.user_id == user.id)
            .order_by(UserQuartier.created_at.asc())
        )
    ).scalars().all()

    seen_count: dict[str, int] = {}
    quartiers_frozen = 0
    for uq in rows:
        limit = FREE_QUARTIER_LIMITS_PER_TYPE.get(uq.relation_type, 0)
        current = seen_count.get(uq.relation_type, 0)
        if current < limit:
            uq.is_active_in_matching = True
            seen_count[uq.relation_type] = current + 1
        else:
            if uq.is_active_in_matching:
                quartiers_frozen += 1
            uq.is_active_in_matching = False

    # Spots : on garde les 5 plus anciens.
    spot_rows = (
        await db.execute(
            select(UserSpot)
            .where(UserSpot.user_id == user.id)
            .order_by(UserSpot.created_at.asc())
        )
    ).scalars().all()

    spots_frozen = 0
    for idx, us in enumerate(spot_rows):
        if idx < FREE_SPOT_LIMIT:
            us.is_active_in_matching = True
        else:
            if us.is_active_in_matching:
                spots_frozen += 1
            us.is_active_in_matching = False

    user.is_premium = False
    await db.commit()

    log.info(
        "premium_downgrade",
        user_id=str(user.id),
        quartiers_frozen=quartiers_frozen,
        spots_frozen=spots_frozen,
    )
    return {
        "quartiers_frozen": quartiers_frozen,
        "spots_frozen": spots_frozen,
    }


async def upgrade_user_limits(user: User, db: AsyncSession) -> dict:
    """
    Réactive tous les quartiers et spots de l'user. Appelé lors d'un
    paiement premium réussi.

    Retourne : {quartiers_reactivated: int, spots_reactivated: int}.
    """
    rows = (
        await db.execute(
            select(UserQuartier).where(UserQuartier.user_id == user.id)
        )
    ).scalars().all()
    q_count = 0
    for uq in rows:
        if not uq.is_active_in_matching:
            uq.is_active_in_matching = True
            q_count += 1

    spot_rows = (
        await db.execute(
            select(UserSpot).where(UserSpot.user_id == user.id)
        )
    ).scalars().all()
    s_count = 0
    for us in spot_rows:
        if not us.is_active_in_matching:
            us.is_active_in_matching = True
            s_count += 1

    user.is_premium = True
    await db.commit()

    log.info(
        "premium_upgrade",
        user_id=str(user.id),
        quartiers_reactivated=q_count,
        spots_reactivated=s_count,
    )
    return {
        "quartiers_reactivated": q_count,
        "spots_reactivated": s_count,
    }


async def downgrade_expired_subscriptions(db: AsyncSession) -> dict:
    """
    Batch : trouve toutes les Subscriptions expirées avec is_active=True,
    passe is_active=False et déclenche downgrade_user_limits pour chaque
    user. Idempotent.

    Appelée par Celery Beat (câblage S11) ou trigger admin.
    """
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(Subscription).where(
                Subscription.expires_at < now,
                Subscription.is_active.is_(True),
            )
        )
    ).scalars().all()

    processed = 0
    for sub in rows:
        user = await db.get(User, sub.user_id)
        if user is None:
            continue
        sub.is_active = False
        await downgrade_user_limits(user, db)
        processed += 1

    log.info("premium_downgrade_batch", processed=processed)
    return {"processed": processed}


__all__ = [
    "downgrade_user_limits",
    "upgrade_user_limits",
    "downgrade_expired_subscriptions",
    "FREE_QUARTIER_LIMITS_PER_TYPE",
    "FREE_SPOT_LIMIT",
]
