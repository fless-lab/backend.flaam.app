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

    STUB. L'implémentation complète (§17) viendra en Session 10 :
    - Phase 1 (immédiat) : anonymiser display_name, purger photos,
      supprimer prompts/tags, couper matchs actifs
    - Phase 2 (T+24h) : supprimer messages, signalements personnels
    - Phase 3 (T+30j) : DROP de la row User (AccountHistory survit)
    """
    log.info(
        "gdpr_purge_scheduled",
        user_id=str(user_id),
        reason=reason,
        note="stub — pipeline RGPD complet en Session 10",
    )
