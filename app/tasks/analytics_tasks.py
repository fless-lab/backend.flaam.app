from __future__ import annotations

"""
Analytics tasks (§S12 stub).

compute_daily_kpis : quotidien (00h30 UTC).

Collecte les KPIs produit (MAU, DAU, match rate, like→match conversion,
ghost→user conversion Porte 3, etc.) et persiste en table `daily_kpis`
pour le dashboard admin.

Impl complète en S13 — les requêtes agrégées dépendent de la table
`daily_kpis` pas encore créée.
"""

import structlog

from app.celery_app import celery_app

log = structlog.get_logger()


@celery_app.task(name="app.tasks.analytics_tasks.compute_daily_kpis")
def compute_daily_kpis() -> dict:
    log.info("compute_daily_kpis_stub", note="full impl S13")
    return {"status": "stub"}


__all__ = ["compute_daily_kpis"]
