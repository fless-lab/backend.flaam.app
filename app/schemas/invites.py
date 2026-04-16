from __future__ import annotations

"""Schemas Pydantic Invite codes (MàJ 7)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


InviteCodeStatus = Literal["active", "used", "expired"]
InviteCodeType = Literal["standard", "ambassador", "event"]


class InviteCodeOut(BaseModel):
    code: str
    type: InviteCodeType
    status: InviteCodeStatus
    expires_at: datetime
    used_by_name: str | None = None
    used_at: datetime | None = None


class GenerateInviteCodesResponse(BaseModel):
    codes: list[InviteCodeOut]
    total: int


class ValidateCodeBody(BaseModel):
    code: str = Field(..., min_length=8, max_length=20)


class ValidateCodeResponse(BaseModel):
    valid: bool
    reason: str | None = None
    city_id: UUID | None = None
    city_name: str | None = None
    creator_name: str | None = None


class RedeemCodeBody(BaseModel):
    code: str = Field(..., min_length=8, max_length=20)


class RedeemCodeResponse(BaseModel):
    redeemed: bool
    waitlist_status: Literal["activated", "waiting"]
    message: str


__all__ = [
    "InviteCodeStatus",
    "InviteCodeType",
    "InviteCodeOut",
    "GenerateInviteCodesResponse",
    "ValidateCodeBody",
    "ValidateCodeResponse",
    "RedeemCodeBody",
    "RedeemCodeResponse",
]
