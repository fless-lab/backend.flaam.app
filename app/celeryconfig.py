from __future__ import annotations

"""
Configuration Celery + beat schedule (§S12).

Horloge de référence : UTC. Le bucketing par timezone locale se fait
dans les tasks (ex : matching_tasks.generate_all_feeds convertit l'UTC
en heure locale par ville et ne traite que les villes dans [3h, 5h[).

Pour ajouter/retirer une tâche planifiée : éditer beat_schedule et
s'assurer que la task existe (ou est un stub qui log) dans app/tasks/.
"""

from celery.schedules import crontab

from app.core.config import get_settings

_settings = get_settings()


broker_url = _settings.celery_broker_url
result_backend = _settings.celery_result_backend

task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]

timezone = "UTC"
enable_utc = True

# Les tâches ne doivent pas s'accumuler indéfiniment si un worker tombe.
task_acks_late = True
worker_prefetch_multiplier = 1
broker_connection_retry_on_startup = True


# ══════════════════════════════════════════════════════════════════════
# Beat schedule
# ══════════════════════════════════════════════════════════════════════


beat_schedule = {
    # ── Feeds ─────────────────────────────────────────────────────────
    # DÉSACTIVÉ — le feed est 100% on-the-fly via GET /feed avec cache
    # Redis + invalidation au profil/like/skip. Le batch écrasait ce
    # que l'invalidation venait de faire et créait des conflits. À
    # ré-activer uniquement si on a une raison forte (ex: feed
    # pré-calculé pour push notifications "people nearby today").
    # "generate-all-feeds": {
    #     "task": "app.tasks.matching_tasks.generate_all_feeds",
    #     "schedule": crontab(hour=3, minute=0),
    # },

    # ── Behavior ──────────────────────────────────────────────────────
    "persist-behavior-scores": {
        "task": "app.tasks.behavior_tasks.persist_behavior_scores",
        "schedule": 3600.0,  # toutes les heures
    },

    # ── Matching / Waitlist ───────────────────────────────────────────
    "release-waitlist-batch": {
        "task": "app.tasks.waitlist_tasks.release_waitlist_batch",
        "schedule": 21600.0,  # toutes les 6h
    },

    # ── Events ────────────────────────────────────────────────────────
    "event-reminder": {
        "task": "app.tasks.event_tasks.event_reminder",
        "schedule": 900.0,  # toutes les 15 min
    },
    "event-status-updater": {
        "task": "app.tasks.event_tasks.event_status_updater",
        "schedule": 900.0,  # toutes les 15 min
    },
    "weekly-event-digest": {
        "task": "app.tasks.event_tasks.weekly_event_digest",
        "schedule": crontab(day_of_week=0, hour=18, minute=0),
    },

    # ── Subscriptions ─────────────────────────────────────────────────
    "check-expired-subscriptions": {
        "task": "app.tasks.subscription_tasks.check_expired_subscriptions",
        "schedule": 3600.0,  # toutes les heures
    },

    # ── Reminders ─────────────────────────────────────────────────────
    "send-reply-reminders": {
        "task": "app.tasks.reminder_tasks.send_reply_reminders",
        "schedule": 14400.0,  # toutes les 4h
    },

    # ── Safety ────────────────────────────────────────────────────────
    "send-emergency-sms": {
        "task": "app.tasks.emergency_tasks.send_emergency_sms",
        "schedule": 60.0,  # toutes les minutes
    },

    # ── Analytics ─────────────────────────────────────────────────────
    "compute-daily-kpis": {
        "task": "app.tasks.analytics_tasks.compute_daily_kpis",
        "schedule": crontab(hour=0, minute=30),
    },

    # ── Cleanup ───────────────────────────────────────────────────────
    "purge-expired-matches": {
        "task": "app.tasks.cleanup_tasks.purge_expired_matches",
        "schedule": 21600.0,  # toutes les 6h
    },
    "purge-old-behavior-logs": {
        "task": "app.tasks.cleanup_tasks.purge_old_behavior_logs",
        "schedule": crontab(day_of_week=1, hour=2, minute=0),
    },
    "purge-old-feed-caches": {
        "task": "app.tasks.cleanup_tasks.purge_old_feed_caches",
        "schedule": 43200.0,  # toutes les 12h
    },
    "cleanup-account-histories": {
        "task": "app.tasks.cleanup_tasks.cleanup_account_histories",
        "schedule": crontab(day_of_month=1, hour=3, minute=0),
    },

    # ── Scam ──────────────────────────────────────────────────────────
    "compute-scam-risk-batch": {
        "task": "app.tasks.scam_tasks.compute_scam_risk_batch",
        "schedule": 86400.0,  # 24h
    },
}
