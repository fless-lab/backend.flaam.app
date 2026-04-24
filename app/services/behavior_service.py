from __future__ import annotations

"""
Behavior service (spec §5.13).

Ingère les batches d'events comportementaux envoyés par le client et
persiste dans BehaviorLog. Alimente :
- L4 behavior scorer
- Implicit preferences (§6.4)
- Scam detection (§39)

Les events qui exposent duration_seconds (profile_viewed, scroll_depth)
ont leur durée extraite du payload `data` pour aller dans la colonne
dédiée (indexable). Le reste du payload est conservé en JSONB.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.behavior_log import BehaviorLog
from app.models.user import User


# Types d'événements acceptés (en sync avec schemas/behavior.EventType).
# Les events les plus bas (like_given...) sont enregistrés mais pas
# encore consommés par le matching engine actuel — ils alimenteront
# les évolutions futures (learning to rank, scoring engagement chat,
# analyse du funnel premium).
VALID_EVENT_TYPES = {
    # Signaux feed / profil
    "profile_viewed",
    "photo_scrolled",
    "prompt_read",
    "return_visit",
    "scroll_depth",
    # Sessions
    "app_session_start",
    "app_session_end",
    # Actions explicites sur un profil
    "like_given",
    "skip_given",
    # Chat
    "conversation_opened",
    "conversation_duration",
    "message_typed_deleted",
    # Premium funnel
    "premium_plan_tapped",
    "premium_upsell_dwell",
}


def _extract_duration(
    event_type: str, data: dict | None
) -> float | None:
    """
    Certains events ont une durée métier (profile_viewed, app_session_*).
    On la remonte dans la colonne dédiée pour les queries du behavior
    scorer, et on laisse le reste dans JSONB.
    """
    if not data:
        return None
    raw = data.get("duration_seconds")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


async def log_events(
    *,
    user: User,
    events: list[dict],
    db: AsyncSession,
) -> int:
    """
    Bulk insert. Retourne le nombre d'events acceptés.

    Events invalides (type inconnu) sont silencieusement ignorés —
    on retourne juste le compteur accepté. Cette tolérance protège
    d'un client qui pousse un nouveau type avant un déploiement server.
    """
    if not events:
        return 0

    to_add: list[BehaviorLog] = []
    for ev in events:
        event_type = ev.get("event_type")
        if event_type not in VALID_EVENT_TYPES:
            continue

        target = ev.get("target_user_id")
        target_uuid: UUID | None = None
        if target is not None:
            try:
                target_uuid = UUID(str(target))
            except (TypeError, ValueError):
                target_uuid = None

        data = ev.get("data")
        duration = _extract_duration(event_type, data)

        # timestamp client-side optionnel : on le range dans data pour
        # reconstituer la chronologie si besoin. created_at reste la
        # date server-side (source de vérité anti-antidate).
        ts = ev.get("timestamp")
        if ts is not None and data is None:
            data = {"client_timestamp": _iso(ts)}
        elif ts is not None:
            data = {**data, "client_timestamp": _iso(ts)}

        to_add.append(
            BehaviorLog(
                user_id=user.id,
                event_type=event_type,
                target_user_id=target_uuid,
                duration_seconds=duration,
                extra_data=data,
            )
        )

    if to_add:
        db.add_all(to_add)
        await db.commit()

    return len(to_add)


def _iso(ts) -> str:
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


__all__ = ["log_events", "VALID_EVENT_TYPES"]
