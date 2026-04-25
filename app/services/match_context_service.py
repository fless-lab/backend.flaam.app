from __future__ import annotations

"""
Match context service — calcule le "pourquoi" d'un match.

Affiché par le mobile en `ChatContextHeader` au-dessus de chaque
conversation. Sentiment cible : "vous avez une raison concrète d'avoir
matché", pas "vous êtes 2 inconnus dans le vide".

Priorité des contextes (du plus fort au plus faible) :
  1. event_common — vous étiez tous les deux à un même event récent
  2. instant_qr — vous vous êtes scannés IRL (origin du match)
  3. quartier_common — même quartier (lives commun)
  4. spot_common — fréquentez le même spot
  5. tags_common — au moins 2 intérêts communs
  6. new — fallback, "Nouveau match"

Le label est généré côté service (FR/EN) selon le user lang.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppException
from app.models.event import Event
from app.models.event_registration import EventRegistration
from app.models.match import Match
from app.models.profile import Profile
from app.models.spot import Spot
from app.models.user import User
from app.models.user_quartier import UserQuartier
from app.models.user_spot import UserSpot
from app.models.quartier import Quartier


def _label_event(event_name: str, lang: str) -> str:
    if lang == "en":
        return f"You both went to {event_name}"
    return f"Vous étiez tous les deux à {event_name}"


def _label_instant_qr(lang: str) -> str:
    if lang == "en":
        return "You scanned each other in person"
    return "Vous vous êtes croisés en vrai"


def _label_quartier(name: str, lang: str) -> str:
    if lang == "en":
        return f"Same neighborhood: {name}"
    return f"Même quartier : {name}"


def _label_spot(name: str, lang: str) -> str:
    if lang == "en":
        return f"You both go to {name}"
    return f"Vous fréquentez tous les deux {name}"


def _label_tags(tags: list[str], lang: str) -> str:
    joined = " · ".join(tags[:3])
    if lang == "en":
        return f"Common interests: {joined}"
    return f"Intérêts communs : {joined}"


def _label_new(lang: str) -> str:
    if lang == "en":
        return "New match"
    return "Nouveau match"


async def get_match_context(
    match_id: UUID, viewer: User, db: AsyncSession, lang: str = "fr",
) -> dict:
    """
    Calcule le contexte du match pour l'écran chat. Retourne :
      {
        "type": "event_common|instant_qr|quartier_common|spot_common|tags_common|new",
        "label": "Texte localisé court (1 ligne)",
        "irl_suggestion_spot_id": UUID|null  (pour le futur IRL nudge)
      }
    """
    match = await db.get(Match, match_id, options=[
        selectinload(Match.user_a),
        selectinload(Match.user_b),
    ])
    if match is None:
        raise AppException(404, "match_not_found")
    if viewer.id not in (match.user_a_id, match.user_b_id):
        raise AppException(403, "not_your_match")

    other_id = match.user_b_id if match.user_a_id == viewer.id else match.user_a_id

    # 1. Origin instant_qr → priorité absolue, pas la peine de chercher autre.
    if match.origin == "instant_qr":
        return {
            "type": "instant_qr",
            "label": _label_instant_qr(lang),
            "irl_suggestion_spot_id": None,
        }

    # 2. Event commun (les 2 inscrits au même event <30 jours)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    event_result = await db.execute(
        select(EventRegistration.event_id, Event.title, Event.starts_at)
        .join(Event, Event.id == EventRegistration.event_id)
        .where(
            EventRegistration.user_id == viewer.id,
            EventRegistration.created_at >= cutoff,
        ),
    )
    viewer_events = {row[0]: (row[1], row[2]) for row in event_result.all()}

    if viewer_events:
        other_events = await db.execute(
            select(EventRegistration.event_id).where(
                EventRegistration.user_id == other_id,
                EventRegistration.event_id.in_(viewer_events.keys()),
            ),
        )
        common = list(other_events.scalars().all())
        if common:
            # Le plus récent
            common_sorted = sorted(common, key=lambda e: viewer_events[e][1], reverse=True)
            event_id = common_sorted[0]
            title, _ = viewer_events[event_id]
            return {
                "type": "event_common",
                "label": _label_event(title, lang),
                "irl_suggestion_spot_id": None,
            }

    # 3. Quartier commun (lives)
    viewer_q = await db.execute(
        select(UserQuartier.quartier_id).where(
            UserQuartier.user_id == viewer.id,
            UserQuartier.relation_type == "lives",
        ),
    )
    viewer_quartier_ids = {q for (q,) in viewer_q.all()}
    if viewer_quartier_ids:
        other_q = await db.execute(
            select(UserQuartier.quartier_id, Quartier.name)
            .join(Quartier, Quartier.id == UserQuartier.quartier_id)
            .where(
                UserQuartier.user_id == other_id,
                UserQuartier.relation_type == "lives",
                UserQuartier.quartier_id.in_(viewer_quartier_ids),
            ),
        )
        rows = other_q.all()
        if rows:
            return {
                "type": "quartier_common",
                "label": _label_quartier(rows[0][1], lang),
                "irl_suggestion_spot_id": None,
            }

    # 4. Spot commun (fidelity > 0.5 des 2 côtés)
    viewer_spots = await db.execute(
        select(UserSpot.spot_id).where(
            UserSpot.user_id == viewer.id,
            UserSpot.fidelity_score >= 0.5,
        ),
    )
    viewer_spot_ids = {s for (s,) in viewer_spots.all()}
    if viewer_spot_ids:
        other_spots = await db.execute(
            select(UserSpot.spot_id, Spot.name)
            .join(Spot, Spot.id == UserSpot.spot_id)
            .where(
                UserSpot.user_id == other_id,
                UserSpot.fidelity_score >= 0.5,
                UserSpot.spot_id.in_(viewer_spot_ids),
            ),
        )
        rows = other_spots.all()
        if rows:
            return {
                "type": "spot_common",
                "label": _label_spot(rows[0][1], lang),
                "irl_suggestion_spot_id": str(rows[0][0]),
            }

    # 5. Tags communs (>= 2)
    viewer_profile = await db.execute(
        select(Profile.tags).where(Profile.user_id == viewer.id),
    )
    viewer_tags_row = viewer_profile.scalar_one_or_none()
    viewer_tags = set(viewer_tags_row or [])
    if viewer_tags:
        other_profile = await db.execute(
            select(Profile.tags).where(Profile.user_id == other_id),
        )
        other_tags = set(other_profile.scalar_one_or_none() or [])
        common_tags = viewer_tags & other_tags
        if len(common_tags) >= 2:
            return {
                "type": "tags_common",
                "label": _label_tags(list(common_tags), lang),
                "irl_suggestion_spot_id": None,
            }

    # 6. Fallback
    return {
        "type": "new",
        "label": _label_new(lang),
        "irl_suggestion_spot_id": None,
    }
