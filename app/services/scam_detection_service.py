from __future__ import annotations

"""
Scam detection service (spec §39).

Premier service "intelligence" de Flaam. Rules-based au MVP mais la
signature est STABLE pour l'IA future (voir docs/flaam-ai-scoping.md §5) :

    async def compute_scam_risk(user_id: UUID, db: AsyncSession) -> float

6 signaux pondérés :
- profile_too_perfect       (0.20)
- immediate_money           (0.30)
- link_spam                 (0.15)
- mass_messaging            (0.15)
- report_count              (0.10)
- device_reuse              (0.10)

Somme plafonnée à 1.0.

Seuils (utilisés par les callers, pas ici) :
- > 0.70 : auto-ban (AUTO_BAN_THRESHOLD)
- 0.40-0.70 : flag pour review (REVIEW_THRESHOLD)
- < 0.40 : rien
"""

import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account_history import AccountHistory
from app.models.device import Device
from app.models.match import Match
from app.models.message import Message
from app.models.photo import Photo
from app.models.profile import Profile
from app.models.report import Report
from app.models.user import User


# Seuils publics (utilisés par safety_service.report_user).
AUTO_BAN_THRESHOLD = 0.70
REVIEW_THRESHOLD = 0.40

# Poids des 6 signaux — hardcodés. Les remonter en MatchingConfig
# ferait sortir une matrice par signal qu'on n'a pas besoin de
# paramétrer à chaud au MVP.
WEIGHTS = {
    "profile_too_perfect": 0.20,
    "immediate_money": 0.30,
    "link_spam": 0.15,
    "mass_messaging": 0.15,
    "report_count": 0.10,
    "device_reuse": 0.10,
}

# Mots-clés argent (multi-langue, lower-case, spec §39).
MONEY_KEYWORDS_FR = {
    "envoie", "transfert", "momo", "mobile money", "urgence",
    "hopital", "hôpital", "accident", "aide moi", "prete moi",
    "prête moi", "western union", "cash", "argent", "paie",
}
MONEY_KEYWORDS_EN = {
    "send me", "transfer", "urgent", "hospital", "help me",
    "lend me", "emergency", "money", "cash", "wire",
}

# Liens / plateformes externes (spec §39).
LINK_PATTERNS = [
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"\bwww\.", re.IGNORECASE),
    re.compile(r"\bwhatsapp\b", re.IGNORECASE),
    re.compile(r"\btelegram\b", re.IGNORECASE),
    re.compile(r"\+\d{6,}", re.IGNORECASE),  # numéro de téléphone
]

# Pour mass_messaging : un message copié-collé sur N matches distincts.
MASS_MESSAGE_MIN_MATCHES = 5

# Nombre de reports reçus au-delà duquel on flag (hors signal général).
REPORT_COUNT_THRESHOLD = 3

# Nombre de premiers messages à scanner pour immediate_money / link_spam.
FIRST_MESSAGES_COUNT_MONEY = 3
FIRST_MESSAGES_COUNT_LINKS = 5


# ══════════════════════════════════════════════════════════════════════
# Signature publique
# ══════════════════════════════════════════════════════════════════════


async def compute_scam_risk(user_id: UUID, db: AsyncSession) -> float:
    """
    Calcule le score de risque 0.0 → 1.0 pour un user.

    Signature STABLE. L'IA future sera wrappée autour, Redis/ML ne
    modifient PAS cette signature publique.
    """
    res = await db.execute(
        select(User)
        .options(selectinload(User.profile))
        .where(User.id == user_id)
    )
    user = res.scalar_one_or_none()
    if user is None:
        return 0.0

    score = 0.0

    if await _signal_profile_too_perfect(user, db):
        score += WEIGHTS["profile_too_perfect"]
    if await _signal_immediate_money(user_id, db):
        score += WEIGHTS["immediate_money"]
    if await _signal_link_spam(user_id, db):
        score += WEIGHTS["link_spam"]
    if await _signal_mass_messaging(user_id, db):
        score += WEIGHTS["mass_messaging"]
    if await _signal_report_count(user_id, db):
        score += WEIGHTS["report_count"]
    if await _signal_device_reuse(user, db):
        score += WEIGHTS["device_reuse"]

    return max(0.0, min(1.0, score))


# ══════════════════════════════════════════════════════════════════════
# Signaux (privés, testables individuellement via le service)
# ══════════════════════════════════════════════════════════════════════


async def _signal_profile_too_perfect(
    user: User, db: AsyncSession
) -> bool:
    """
    profile_completeness = 1.0 + ≥3 photos non rejetées + compte < 24h.
    """
    if user.profile is None:
        return False
    if (user.profile.profile_completeness or 0.0) < 1.0:
        return False

    created = user.created_at
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_hours = (
        datetime.now(timezone.utc) - created
    ).total_seconds() / 3600
    if age_hours >= 24:
        return False

    photo_count_row = await db.execute(
        select(func.count(Photo.id)).where(
            Photo.user_id == user.id,
            Photo.moderation_status != "rejected",
        )
    )
    photos = photo_count_row.scalar_one() or 0
    return photos >= 3


async def _signal_immediate_money(
    user_id: UUID, db: AsyncSession
) -> bool:
    """
    Vrai si l'user a mentionné un mot-clé argent dans les 3 premiers
    messages d'au moins un match.
    """
    # Récupère tous les match_ids où l'user a envoyé au moins 1 message.
    match_ids_row = await db.execute(
        select(Message.match_id)
        .where(Message.sender_id == user_id)
        .distinct()
    )
    match_ids = [r[0] for r in match_ids_row.all()]
    if not match_ids:
        return False

    for match_id in match_ids:
        # Les 3 premiers messages envoyés PAR L'USER dans ce match.
        rows = await db.execute(
            select(Message.content)
            .where(
                Message.match_id == match_id,
                Message.sender_id == user_id,
                Message.content.isnot(None),
            )
            .order_by(Message.created_at.asc())
            .limit(FIRST_MESSAGES_COUNT_MONEY)
        )
        for (content,) in rows.all():
            if _contains_money_keyword(content or ""):
                return True
    return False


async def _signal_link_spam(user_id: UUID, db: AsyncSession) -> bool:
    """Lien/numéro dans les 5 premiers messages d'au moins un match."""
    match_ids_row = await db.execute(
        select(Message.match_id)
        .where(Message.sender_id == user_id)
        .distinct()
    )
    match_ids = [r[0] for r in match_ids_row.all()]
    if not match_ids:
        return False

    for match_id in match_ids:
        rows = await db.execute(
            select(Message.content)
            .where(
                Message.match_id == match_id,
                Message.sender_id == user_id,
                Message.content.isnot(None),
            )
            .order_by(Message.created_at.asc())
            .limit(FIRST_MESSAGES_COUNT_LINKS)
        )
        for (content,) in rows.all():
            if _contains_link(content or ""):
                return True
    return False


async def _signal_mass_messaging(
    user_id: UUID, db: AsyncSession
) -> bool:
    """
    Le même contenu exact (len > 20) envoyé dans ≥ 5 matches distincts.
    """
    rows = await db.execute(
        select(
            Message.content,
            func.count(func.distinct(Message.match_id)).label("n_matches"),
        )
        .where(
            Message.sender_id == user_id,
            Message.content.isnot(None),
            func.length(Message.content) > 20,
        )
        .group_by(Message.content)
        .having(
            func.count(func.distinct(Message.match_id))
            >= MASS_MESSAGE_MIN_MATCHES
        )
        .limit(1)
    )
    return rows.first() is not None


async def _signal_report_count(
    user_id: UUID, db: AsyncSession
) -> bool:
    row = await db.execute(
        select(func.count(Report.id)).where(
            Report.reported_user_id == user_id
        )
    )
    count = row.scalar_one() or 0
    return count > REPORT_COUNT_THRESHOLD


async def _signal_device_reuse(
    user: User, db: AsyncSession
) -> bool:
    """
    Vrai si l'un des devices de l'user correspond à un AccountHistory
    banni (last_departure_reason startswith "banned_").
    """
    device_rows = await db.execute(
        select(Device.device_fingerprint).where(Device.user_id == user.id)
    )
    fingerprints = [r[0] for r in device_rows.all() if r[0]]
    if not fingerprints:
        return False

    for fp in fingerprints:
        # JSONB contains : on cherche un AccountHistory banni ayant
        # listé ce fingerprint.
        row = await db.execute(
            select(AccountHistory.id).where(
                and_(
                    AccountHistory.device_fingerprints.contains([fp]),
                    AccountHistory.last_departure_reason.like("banned_%"),
                    AccountHistory.phone_hash != user.phone_hash,
                )
            )
        )
        if row.first() is not None:
            return True
    return False


# ══════════════════════════════════════════════════════════════════════
# Helpers internes
# ══════════════════════════════════════════════════════════════════════


def _contains_money_keyword(text: str) -> bool:
    low = text.lower()
    for kw in MONEY_KEYWORDS_FR:
        if kw in low:
            return True
    for kw in MONEY_KEYWORDS_EN:
        if kw in low:
            return True
    return False


def _contains_link(text: str) -> bool:
    for pat in LINK_PATTERNS:
        if pat.search(text):
            return True
    return False


__all__ = [
    "compute_scam_risk",
    "AUTO_BAN_THRESHOLD",
    "REVIEW_THRESHOLD",
    "WEIGHTS",
]
