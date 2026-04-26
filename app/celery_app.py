from __future__ import annotations

"""
Celery app (§S12).

Instancie `celery_app` et charge la config depuis `app.celeryconfig`.
Les tasks sont regroupées dans `app.tasks.*`. Chaque module y expose
des fonctions `_async()` testables directement + des wrappers sync
`@celery_app.task` qui font `asyncio.run(_async())`.

Lancer les workers :
    celery -A app.celery_app worker --loglevel=info
    celery -A app.celery_app beat --loglevel=info
"""

from celery import Celery

from app.core.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "flaam",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
)
celery_app.config_from_object("app.celeryconfig")

# Découverte auto des tasks déclarées avec @celery_app.task dans
# app.tasks.* (tous les modules du package).
celery_app.autodiscover_tasks(
    [
        "app.tasks.analytics_tasks",
        "app.tasks.behavior_tasks",
        "app.tasks.cleanup_tasks",
        "app.tasks.emergency_tasks",
        "app.tasks.event_tasks",
        "app.tasks.feed_tasks",
        "app.tasks.matching_tasks",
        "app.tasks.photo_tasks",
        "app.tasks.reminder_tasks",
        "app.tasks.scam_tasks",
        "app.tasks.subscription_tasks",
        "app.tasks.waitlist_tasks",
    ]
)


__all__ = ["celery_app"]
