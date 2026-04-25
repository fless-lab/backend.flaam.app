from __future__ import annotations

"""
Invite service (MàJ 7).

- 3 codes par femme inscrite, 50 par ambassadrice. Les hommes
  non-ambassadeurs n'en reçoivent pas.
- Format du code : "FLAAM-XXXXXXXX" (8 chars alphanumériques upper).
- Expiration : 30 jours (standard), 90 jours (ambassador).
- Redeem : marque le code utilisé + ajoute l'utilisateur à la waitlist
  avec status=activated (skip waitlist).
"""

import secrets
import string
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.invite_code import InviteCode
from app.models.user import User
from app.services import waitlist_service

log = structlog.get_logger()


CODES_PER_USER = 1
CODES_PER_FEMALE = 3
CODES_PER_AMBASSADOR = 50
STANDARD_EXPIRY_DAYS = 30
AMBASSADOR_EXPIRY_DAYS = 90


def _generate_code_string() -> str:
    alphabet = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"FLAAM-{suffix}"


def _is_female(user: User) -> bool:
    return (
        user.profile is not None
        and user.profile.gender == "woman"
    )


def _quota(user: User) -> int:
    """
    Quota d'invites par user :
      - ambassador : 50 codes (pour seeding ville par influenceurs)
      - femme : 3 codes (régule le ratio H/F en early stage)
      - tout autre user : 1 code (chaque user peut inviter 1 ami)

    Décision produit beta : ouvert à tous, mais asymétrique pour
    encourager le ratio. À tuner si le ratio derive.
    """
    if user.is_ambassador:
        return CODES_PER_AMBASSADOR
    if _is_female(user):
        return CODES_PER_FEMALE
    return CODES_PER_USER


def _code_type(user: User) -> str:
    return "ambassador" if user.is_ambassador else "standard"


def _expiry(user: User) -> datetime:
    days = (
        AMBASSADOR_EXPIRY_DAYS if user.is_ambassador else STANDARD_EXPIRY_DAYS
    )
    return datetime.now(timezone.utc) + timedelta(days=days)


# ── Generate ─────────────────────────────────────────────────────────

async def generate_codes(user: User, db: AsyncSession) -> list[InviteCode]:
    quota = _quota(user)
    if quota == 0:
        raise AppException(
            status.HTTP_403_FORBIDDEN, "invite_codes_not_available"
        )
    if user.city_id is None:
        raise AppException(
            status.HTTP_400_BAD_REQUEST, "city_not_selected"
        )

    # Codes actifs existants
    result = await db.execute(
        select(InviteCode).where(
            InviteCode.creator_id == user.id,
            InviteCode.is_active.is_(True),
            InviteCode.used_by_id.is_(None),
        )
    )
    existing_active = list(result.scalars().all())
    to_generate = max(0, quota - len(existing_active))
    if to_generate == 0:
        return existing_active

    created: list[InviteCode] = []
    for _ in range(to_generate):
        # Très faible probabilité de collision, mais on re-tire si besoin.
        for _attempt in range(5):
            code_str = _generate_code_string()
            exists = await db.execute(
                select(InviteCode.id).where(InviteCode.code == code_str)
            )
            if exists.scalar_one_or_none() is None:
                break
        else:
            raise AppException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "code_collision"
            )

        ic = InviteCode(
            code=code_str,
            creator_id=user.id,
            city_id=user.city_id,
            type=_code_type(user),
            expires_at=_expiry(user),
        )
        db.add(ic)
        created.append(ic)

    await db.commit()
    for ic in created:
        await db.refresh(ic)
    log.info(
        "invite_codes_generated",
        user_id=str(user.id),
        count=len(created),
    )
    return existing_active + created


# ── List mine ────────────────────────────────────────────────────────

async def list_my_codes(user: User, db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(InviteCode).where(InviteCode.creator_id == user.id)
        .order_by(InviteCode.created_at.desc())
    )
    codes = list(result.scalars().all())
    out: list[dict] = []
    now = datetime.now(timezone.utc)
    for ic in codes:
        if ic.used_by_id is not None:
            code_status = "used"
        elif not ic.is_active or ic.expires_at < now:
            code_status = "expired"
        else:
            code_status = "active"

        used_by_name = None
        if ic.used_by_id is not None:
            used_by = await db.get(User, ic.used_by_id)
            if used_by and used_by.profile is not None:
                used_by_name = used_by.profile.display_name

        out.append(
            {
                "code": ic.code,
                "type": ic.type,
                "status": code_status,
                "expires_at": ic.expires_at,
                "used_by_name": used_by_name,
                "used_at": ic.used_at,
            }
        )
    return out


# ── Validate ─────────────────────────────────────────────────────────

async def _fetch_code_for_validation(
    code: str, db: AsyncSession
) -> tuple[InviteCode | None, str | None]:
    result = await db.execute(
        select(InviteCode).where(InviteCode.code == code)
    )
    ic = result.scalar_one_or_none()
    if ic is None:
        return None, "not_found"
    if not ic.is_active:
        return ic, "inactive"
    if ic.used_by_id is not None:
        return ic, "already_used"
    if ic.expires_at < datetime.now(timezone.utc):
        return ic, "expired"
    return ic, None


async def validate_code(code: str, db: AsyncSession) -> dict:
    ic, reason = await _fetch_code_for_validation(code, db)
    if ic is None or reason is not None:
        return {
            "valid": False,
            "reason": reason or "not_found",
            "city_id": None,
            "city_name": None,
            "creator_name": None,
        }

    creator = await db.get(User, ic.creator_id)
    creator_name = None
    if creator and creator.profile is not None:
        creator_name = creator.profile.display_name

    from app.models.city import City  # local import to avoid cycles

    city = await db.get(City, ic.city_id)
    return {
        "valid": True,
        "reason": None,
        "city_id": ic.city_id,
        "city_name": city.name if city else None,
        "creator_name": creator_name,
    }


# ── Redeem ───────────────────────────────────────────────────────────

async def redeem_code(
    code: str, user: User, db: AsyncSession
) -> dict:
    ic, reason = await _fetch_code_for_validation(code, db)
    if ic is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "code_not_found")
    if reason is not None:
        raise AppException(
            status.HTTP_400_BAD_REQUEST, f"code_{reason}"
        )
    if ic.creator_id == user.id:
        raise AppException(
            status.HTTP_400_BAD_REQUEST, "cannot_redeem_own_code"
        )

    ic.used_by_id = user.id
    ic.used_at = datetime.now(timezone.utc)
    ic.is_active = False

    # Placement waitlist immédiat (skip) via waitlist_service
    join_result = await waitlist_service.process_waitlist_join(
        user, ic.city_id, db, invite_code_used=ic.code
    )
    log.info(
        "invite_code_redeemed",
        code=ic.code,
        user_id=str(user.id),
        creator_id=str(ic.creator_id),
    )
    return {
        "redeemed": True,
        "waitlist_status": join_result["status"],
        "message": join_result["message"],
    }


__all__ = [
    "CODES_PER_FEMALE",
    "CODES_PER_AMBASSADOR",
    "STANDARD_EXPIRY_DAYS",
    "AMBASSADOR_EXPIRY_DAYS",
    "generate_codes",
    "list_my_codes",
    "validate_code",
    "redeem_code",
]
