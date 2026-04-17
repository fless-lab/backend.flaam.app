from __future__ import annotations

"""
Behavior tasks (§S12 stub).

persist_behavior_scores : toutes les heures.

Le behavior tracking en runtime (app/services/matching_engine/
behavior_scorer.py) met déjà à jour Profile.behavior_multiplier à
chaque action utilisateur. Cette tâche batch est un fallback pour :
- Recalculer pour les users qui n'ont pas agi depuis longtemps
  (purge des stats obsolètes).
- Persister les stats Redis incrémentales vers la DB.

Impl complète en S13 — pour l'instant on log simplement.
"""

import structlog

from app.celery_app import celery_app

log = structlog.get_logger()


@celery_app.task(name="app.tasks.behavior_tasks.persist_behavior_scores")
def persist_behavior_scores() -> dict:
    log.info("persist_behavior_scores_stub", note="full impl S13")
    return {"status": "stub"}


__all__ = ["persist_behavior_scores"]
