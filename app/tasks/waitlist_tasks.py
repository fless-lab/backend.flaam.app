from __future__ import annotations

"""
Waitlist tasks (§S12 stub).

release_waitlist_batch : toutes les 6h.

Libère N places de la waitlist vers l'état "invited" selon la capacité
de la ville (spec §4). Le service `waitlist_service` expose déjà la
logique par ville — cette tâche sera câblée en S13 avec la stratégie
de release par cohorte.
"""

import structlog

from app.celery_app import celery_app

log = structlog.get_logger()


@celery_app.task(name="app.tasks.waitlist_tasks.release_waitlist_batch")
def release_waitlist_batch() -> dict:
    log.info("release_waitlist_batch_stub", note="full impl S13")
    return {"status": "stub"}


__all__ = ["release_waitlist_batch"]
