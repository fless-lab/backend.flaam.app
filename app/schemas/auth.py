from __future__ import annotations

"""Schemas Pydantic pour le module Auth (spec §5.1 + Auth sans mot de passe)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# ── OTP ──────────────────────────────────────────────────────────────

class OtpRequestBody(BaseModel):
    phone: str = Field(..., examples=["+22890123456"])
    device_fingerprint: str | None = Field(default=None, max_length=256)


class OtpResponse(BaseModel):
    message: str = "OTP sent"
    channel: Literal["sms", "whatsapp"] = "sms"
    expires_in: int
    retry_after: int


class OtpResendBody(BaseModel):
    phone: str
    channel: Literal["sms", "whatsapp"] = "whatsapp"
    device_fingerprint: str | None = Field(default=None, max_length=256)


class OtpVerifyBody(BaseModel):
    phone: str
    code: str = Field(..., min_length=4, max_length=8)
    device_fingerprint: str | None = Field(default=None, max_length=256)
    platform: Literal["android", "ios", "web"] | None = None
    app_version: str | None = Field(default=None, max_length=20)
    os_version: str | None = Field(default=None, max_length=30)


class AuthTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    is_new_user: bool = False
    user_id: UUID
    onboarding_step: str | None = None
    restriction: str | None = None
    mfa_required: bool = False


class RefreshTokenBody(BaseModel):
    refresh_token: str


# ── Email ────────────────────────────────────────────────────────────

class AddEmailBody(BaseModel):
    email: EmailStr


class VerifyEmailBody(BaseModel):
    token: str = Field(..., min_length=16, max_length=128)


# ── Recovery (numéro perdu) ──────────────────────────────────────────

class RecoveryRequestBody(BaseModel):
    email: EmailStr


class RecoveryConfirmBody(BaseModel):
    recovery_token: str = Field(..., min_length=16, max_length=128)
    new_phone: str


class RecoveryCompleteBody(BaseModel):
    recovery_token: str = Field(..., min_length=16, max_length=128)
    otp: str = Field(..., min_length=4, max_length=8)


# ── MFA (PIN 6 chiffres) ─────────────────────────────────────────────

class MfaPinBody(BaseModel):
    pin: str = Field(..., pattern=r"^\d{6}$")


# ── Changement de numéro ─────────────────────────────────────────────

class PhoneChangeTokenResponse(BaseModel):
    change_token: str
    expires_in: int


class SetNewPhoneBody(BaseModel):
    change_token: str = Field(..., min_length=16, max_length=128)
    new_phone: str
    otp: str = Field(..., min_length=4, max_length=8)


# ── Suppression de compte ────────────────────────────────────────────

class DeleteAccountBody(BaseModel):
    reason: str | None = Field(default=None, max_length=200)
    confirm: bool = Field(default=False)


class RestrictionInfo(BaseModel):
    restriction: str
    reason: str | None = None
    expires_at: datetime | None = None


class SimpleMessage(BaseModel):
    message: str


__all__ = [
    "OtpRequestBody",
    "OtpResponse",
    "OtpResendBody",
    "OtpVerifyBody",
    "AuthTokenResponse",
    "RefreshTokenBody",
    "AddEmailBody",
    "VerifyEmailBody",
    "RecoveryRequestBody",
    "RecoveryConfirmBody",
    "RecoveryCompleteBody",
    "MfaPinBody",
    "PhoneChangeTokenResponse",
    "SetNewPhoneBody",
    "DeleteAccountBody",
    "RestrictionInfo",
    "SimpleMessage",
]
