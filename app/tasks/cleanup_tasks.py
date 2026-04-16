from __future__ import annotations

"""
Tâches de nettoyage / pipeline RGPD (§17).

Le worker Celery n'est pas encore câblé (Session 10). Pour l'instant,
les tâches sont des fonctions `async` appelées directement (fire-and-forget
via asyncio.create_task) qui logguent leur déclenchement.

Dès que l'app Celery existera, il suffira de décorer ces fonctions avec
`@celery_app.task` et d'appeler `.delay(...)` au lieu de l'appel direct.
"""

from uuid import UUID

import structlog

log = structlog.get_logger()


async def purge_account_data(user_id: UUID, reason: str) -> None:
    """
    Pipeline RGPD Phase 1 — anonymisation et purge des données perso.

    STUB. L'implémentation complète (§17) :
    - Phase 1 (immédiat) : anonymiser display_name, marquer photos,
      supprimer prompts/tags, couper matchs actifs — implémenté dans
      app/services/gdpr_service.py::initiate_deletion
    - Phase 2 (T+7j) : suppression physique des fichiers photos disque
    - Phase 3 (T+30j) : DROP de la row User (AccountHistory survit)
    """
    log.info(
        "gdpr_purge_scheduled",
        user_id=str(user_id),
        reason=reason,
        note="phase 2/3 câblés en S11 (Celery Beat)",
    )


async def downgrade_expired_subscriptions_task() -> None:
    """
    Stub Celery : parcourt les Subscriptions expirées et lance le gel
    doux. Le câblage Celery viendra en S11 (Celery Beat daily).
    """
    from app.db.session import async_session
    from app.services import subscription_service

    async with async_session() as db:
        result = await subscription_service.downgrade_expired_subscriptions(db)
    log.info("cleanup_downgrade_task_finished", **result)
