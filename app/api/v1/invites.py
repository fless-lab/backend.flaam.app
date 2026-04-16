from __future__ import annotations

"""Routes Invite codes (MàJ 7)."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.models.user import User
from app.schemas.invites import (
    GenerateInviteCodesResponse,
    InviteCodeOut,
    RedeemCodeBody,
    RedeemCodeResponse,
    ValidateCodeBody,
    ValidateCodeResponse,
)
from app.services import invite_service

router = APIRouter(prefix="/invites", tags=["invites"])


def _serialize(ic, used_by_name=None) -> dict:
    # Status dérivé : le service renvoie un objet InviteCode, on recalcule
    # le status minimal pour la génération (toujours "active" car fresh).
    return {
        "code": ic.code,
        "type": ic.type,
        "status": "active",
        "expires_at": ic.expires_at,
        "used_by_name": used_by_name,
        "used_at": ic.used_at,
    }


@router.post("/generate", response_model=GenerateInviteCodesResponse)
async def generate(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    codes = await invite_service.generate_codes(user, db)
    return {
        "codes": [_serialize(ic) for ic in codes],
        "total": len(codes),
    }


@router.get("/me", response_model=list[InviteCodeOut])
async def my_codes(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await invite_service.list_my_codes(user, db)


@router.post("/validate", response_model=ValidateCodeResponse)
async def validate_code(
    body: ValidateCodeBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await invite_service.validate_code(body.code, db)


@router.post("/redeem", response_model=RedeemCodeResponse)
async def redeem_code(
    body: RedeemCodeBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await invite_service.redeem_code(body.code, user, db)


__all__ = ["router"]
