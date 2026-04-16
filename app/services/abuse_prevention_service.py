from __future__ import annotations

"""
Anti-abus cycle création/suppression/recréation (spec §30).

Table de vérité : voir §30.4. Tous les chemins de `calculate_restrictions`
respectent cette matrice. Toute modification doit être reflétée à la fois
ici et dans la doc.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_history import AccountHistory


PERMANENT_BAN_REASONS = {"banned_harassment", "banned_scam", "banned_underage"}


def _no_restriction() -> dict:
    return {
        "allowed": True,
        "restriction": "none",
        "reason": None,
        "new_user_boost": True,
        "daily_likes_override": None,
        "restriction_expires_at": None,
        "risk_score": 0.0,
    }


def _clean_return(risk: float) -> dict:
    return {
        "allowed": True,
        "restriction": "none",
        "reason": "Returning user. Clean history.",
        "new_user_boost": True,
        "daily_likes_override": None,
        "restriction_expires_at": None,
        "risk_score": risk,
    }


def calculate_restrictions(history: AccountHistory) -> dict:
    """Matrice de restrictions (spec §30.3)."""
    now = datetime.now(timezone.utc)

    # Ban permanent
    if history.last_departure_reason in PERMANENT_BAN_REASONS:
        return {
            "allowed": False,
            "restriction": "permanent_ban",
            "reason": f"Account permanently banned: {history.last_departure_reason}",
            "new_user_boost": False,
            "daily_likes_override": None,
            "restriction_expires_at": None,
            "risk_score": 1.0,
        }

    if history.total_bans >= 2:
        return {
            "allowed": False,
            "restriction": "permanent_ban",
            "reason": f"Multiple bans ({history.total_bans}). Permanent.",
            "new_user_boost": False,
            "daily_likes_override": None,
            "restriction_expires_at": None,
            "risk_score": 1.0,
        }

    if history.total_bans == 1 and history.last_departure_reason in {
        "banned_spam",
        "banned_fake",
    }:
        days_since_ban = (
            (now - history.last_ban_at).days if history.last_ban_at else 0
        )
        if days_since_ban < 30:
            return {
                "allowed": False,
                "restriction": "cooldown_active",
                "reason": f"Banned {days_since_ban} days ago. Must wait 30 days.",
                "new_user_boost": False,
                "daily_likes_override": None,
                "restriction_expires_at": (
                    history.last_ban_at + timedelta(days=30)
                    if history.last_ban_at
                    else None
                ),
                "risk_score": 0.8,
            }
        return {
            "allowed": True,
            "restriction": "probation",
            "reason": "Returning after ban. Probation period.",
            "new_user_boost": False,
            "daily_likes_override": 3,
            "restriction_expires_at": now + timedelta(days=60),
            "risk_score": 0.6,
        }

    # Suppression volontaire
    if history.last_account_deleted_at is None:
        return _no_restriction()

    hours_since = (
        now - history.last_account_deleted_at
    ).total_seconds() / 3600
    days_since = (now - history.last_account_deleted_at).days
    total_cycles = history.total_accounts_deleted

    if hours_since < 1:
        return {
            "allowed": False,
            "restriction": "cooldown_active",
            "reason": "Account deleted less than 1 hour ago.",
            "new_user_boost": False,
            "daily_likes_override": None,
            "restriction_expires_at": history.last_account_deleted_at
            + timedelta(hours=24),
            "risk_score": 0.7,
        }
    if days_since < 1:
        return {
            "allowed": True,
            "restriction": "no_boost",
            "reason": "Account deleted less than 24h ago. No new user boost.",
            "new_user_boost": False,
            "daily_likes_override": 3,
            "restriction_expires_at": None,
            "risk_score": 0.5,
        }
    if days_since < 7:
        return {
            "allowed": True,
            "restriction": "no_boost",
            "reason": "Account deleted less than 7 days ago.",
            "new_user_boost": False,
            "daily_likes_override": None,
            "restriction_expires_at": None,
            "risk_score": 0.3,
        }
    if days_since < 30:
        return {
            "allowed": True,
            "restriction": "no_boost",
            "reason": (
                f"{total_cycles} account cycles detected."
                if total_cycles >= 3
                else "Account deleted less than 30 days ago."
            ),
            "new_user_boost": False,
            "daily_likes_override": None,
            "restriction_expires_at": None,
            "risk_score": 0.4 if total_cycles >= 3 else 0.2,
        }
    if days_since < 180:
        if total_cycles >= 3:
            return {
                "allowed": True,
                "restriction": "no_boost",
                "reason": f"{total_cycles} account cycles. No boost despite time gap.",
                "new_user_boost": False,
                "daily_likes_override": None,
                "restriction_expires_at": None,
                "risk_score": 0.2,
            }
        return {
            "allowed": True,
            "restriction": "reduced_boost",
            "reason": "Returning user. Reduced new user boost.",
            "new_user_boost": True,
            "boost_multiplier": 0.5,
            "daily_likes_override": None,
            "restriction_expires_at": None,
            "risk_score": 0.1,
        }
    if days_since < 365:
        if total_cycles >= 5:
            return {
                "allowed": True,
                "restriction": "no_boost",
                "reason": f"{total_cycles} total cycles. Excessive.",
                "new_user_boost": False,
                "daily_likes_override": None,
                "restriction_expires_at": None,
                "risk_score": 0.15,
            }
        return _clean_return(risk=0.05)

    # > 1 an
    if total_cycles >= 5:
        return {
            "allowed": True,
            "restriction": "no_boost",
            "reason": f"{total_cycles} total cycles over lifetime.",
            "new_user_boost": False,
            "daily_likes_override": None,
            "restriction_expires_at": None,
            "risk_score": 0.1,
        }
    return _clean_return(risk=0.0)


# ── Lookups ──────────────────────────────────────────────────────────

async def find_history_by_phone(
    phone_hash: str, db: AsyncSession
) -> AccountHistory | None:
    result = await db.execute(
        select(AccountHistory).where(AccountHistory.phone_hash == phone_hash)
    )
    return result.scalar_one_or_none()


async def find_history_by_device(
    device_fp: str, db: AsyncSession
) -> AccountHistory | None:
    """
    Détecte un changement de SIM avec le même device (spec §30.6).

    Device fingerprint = hash de {Android ID, modèle, résolution écran}.
    Exclut l'IMEI (permission spéciale Android 10+).
    """
    result = await db.execute(
        select(AccountHistory).where(
            AccountHistory.device_fingerprints.contains([device_fp])
        )
    )
    return result.scalar_one_or_none()


def compute_risk_score(history: AccountHistory) -> float:
    """Score 0.0 → 1.0 basé sur l'historique (§30.7)."""
    score = 0.0
    score += min(0.5, history.total_bans * 0.25)
    if history.total_accounts_created >= 5:
        score += 0.2
    elif history.total_accounts_created >= 3:
        score += 0.1
    if history.last_account_deleted_at and history.last_account_created_at:
        gap = history.last_account_created_at - history.last_account_deleted_at
        if gap.total_seconds() < 3600:
            score += 0.3
        elif gap.days < 7:
            score += 0.1
    if len(history.device_fingerprints) >= 3:
        score += 0.1
    return min(1.0, score)


async def update_history_on_deletion(
    user,
    reason: str,
    device_fingerprint: str | None,
    db: AsyncSession,
) -> AccountHistory:
    """
    Mise à jour / création de l'AccountHistory au moment de la suppression
    (§30.7). Doit être appelé AVANT de purger les données personnelles.

    `reason` : "user_deleted" ou "banned_{spam|fake|harassment|scam|underage|other}".
    """
    history = await find_history_by_phone(user.phone_hash, db)
    if history is None:
        history = AccountHistory(
            phone_hash=user.phone_hash,
            device_fingerprints=[device_fingerprint] if device_fingerprint else [],
            total_accounts_created=user.account_created_count or 1,
            first_account_created_at=user.created_at,
        )
        db.add(history)
        await db.flush()

    if device_fingerprint and device_fingerprint not in history.device_fingerprints:
        history.device_fingerprints = [
            *history.device_fingerprints,
            device_fingerprint,
        ]

    now = datetime.now(timezone.utc)
    history.total_accounts_deleted += 1
    history.last_account_deleted_at = now
    history.last_departure_reason = reason
    if reason.startswith("banned_"):
        history.total_bans += 1
        history.last_ban_at = now

    history.risk_score = compute_risk_score(history)
    return history


__all__ = [
    "calculate_restrictions",
    "find_history_by_phone",
    "find_history_by_device",
    "compute_risk_score",
    "update_history_on_deletion",
]
