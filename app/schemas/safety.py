from __future__ import annotations

"""Schemas Pydantic — Safety (§5.11, S12.5)."""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# Format E.164 : + suivi de 8 a 15 chiffres.
_E164_RE = re.compile(r"^\+\d{8,15}$")


ReportReason = Literal[
    "inappropriate_content",
    "fake_profile",
    "harassment",
    "scam",
    "underage",
    "other",
]


class ReportBody(BaseModel):
    reported_user_id: UUID
    reason: ReportReason
    description: str | None = Field(default=None, max_length=1000)
    evidence_message_ids: list[UUID] | None = None


class ReportResponse(BaseModel):
    id: UUID
    status: str
    message: str | None = None


class BlockBody(BaseModel):
    blocked_user_id: UUID


class BlockResponse(BaseModel):
    status: Literal["blocked"]
    blocked_user_id: UUID
    message: str | None = None


class UnblockResponse(BaseModel):
    status: Literal["unblocked"]
    message: str | None = None


class ShareDateBody(BaseModel):
    contact_phone: str = Field(..., min_length=8, max_length=20)
    contact_name: str | None = Field(default=None, max_length=100)
    partner_name: str = Field(..., min_length=1, max_length=100)
    meeting_place: str = Field(..., min_length=1, max_length=200)
    meeting_time: datetime


class ShareDateResponse(BaseModel):
    status: Literal["sent"]
    provider_message_id: str | None = None


# ══════════════════════════════════════════════════════════════════════
# Emergency contacts (CRUD)
# ══════════════════════════════════════════════════════════════════════


class EmergencyContactBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    phone: str = Field(..., min_length=8, max_length=20)

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, v: str) -> str:
        v = v.strip()
        if not _E164_RE.match(v):
            raise ValueError("phone must be E.164 format (+indicatif + chiffres)")
        return v


class EmergencyContactUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, min_length=8, max_length=20)

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not _E164_RE.match(v):
            raise ValueError("phone must be E.164 format (+indicatif + chiffres)")
        return v


class EmergencyContactResponse(BaseModel):
    id: UUID
    name: str
    phone: str
    is_primary: bool


# ══════════════════════════════════════════════════════════════════════
# Emergency timer
# ══════════════════════════════════════════════════════════════════════


class EmergencyBody(BaseModel):
    # Nouvelle API S12.5 : on parle en "hours" (float pour autoriser 0.5).
    # Bornes : 30 min min, 12h max.
    hours: float = Field(..., ge=0.5, le=12)

    # Max 2 contacts actifs par timer (validation business).
    contact_ids: list[UUID] | None = Field(default=None, max_length=2)

    # Fallback si aucun contact_ids fourni : ancien mode 1 contact ad-hoc.
    contact_phone: str | None = Field(default=None, min_length=8, max_length=20)
    contact_name: str | None = Field(default=None, max_length=100)

    meeting_place: str | None = Field(default=None, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    # SAFETY-6 : contexte match — si fourni, partner_user_id est dérivé
    # automatiquement depuis la table matches si absent.
    match_id: UUID | None = None
    partner_user_id: UUID | None = None


class EmergencyResponse(BaseModel):
    status: Literal["armed"]
    expires_at: datetime
    session_id: UUID | None = None
    message: str | None = None


class TimerCancelResponse(BaseModel):
    status: Literal["cancelled", "no_active_timer"]
    message: str | None = None


class TimerLocationBody(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class TimerLocationResponse(BaseModel):
    status: Literal["updated"]


class TimerExtendBody(BaseModel):
    extra_hours: float = Field(..., ge=0.5, le=4)


class TimerExtendResponse(BaseModel):
    status: Literal["extended"]
    expires_at: datetime
    message: str | None = None


class PanicBody(BaseModel):
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class PanicResponse(BaseModel):
    status: Literal["alert_sent"]
    contacts_notified: int
    message: str | None = None


__all__ = [
    "ReportReason",
    "ReportBody",
    "ReportResponse",
    "BlockBody",
    "BlockResponse",
    "UnblockResponse",
    "ShareDateBody",
    "ShareDateResponse",
    "EmergencyContactBody",
    "EmergencyContactUpdate",
    "EmergencyContactResponse",
    "EmergencyBody",
    "EmergencyResponse",
    "TimerCancelResponse",
    "TimerLocationBody",
    "TimerLocationResponse",
    "TimerExtendBody",
    "TimerExtendResponse",
    "PanicBody",
    "PanicResponse",
]
