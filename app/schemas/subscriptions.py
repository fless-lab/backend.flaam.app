from __future__ import annotations

"""Schemas Pydantic pour les subscriptions / paiements (§5.11)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SubscriptionMeResponse(BaseModel):
    is_premium: bool
    plan: str | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    is_auto_renew: bool = False
    is_active: bool = False
    currency: str | None = None


class PlanOption(BaseModel):
    code: Literal["weekly", "monthly"]
    duration_days: int
    amount: int
    currency: str


class PlansResponse(BaseModel):
    plans: list[PlanOption]
    city_name: str | None = None


class InitializeBody(BaseModel):
    plan: Literal["weekly", "monthly"]
    payment_method: str = Field(..., min_length=2, max_length=30)
    provider: str = Field(default="paystack", max_length=20)


class InitializeResponse(BaseModel):
    authorization_url: str
    access_code: str | None = None
    reference: str
    provider: str
    plan: str
    amount: int
    currency: str


class CancelResponse(BaseModel):
    status: str = "cancelled"
    expires_at: datetime | None = None


class WebhookResponse(BaseModel):
    status: str
    event: str | None = None
    reason: str | None = None


class SimulateWebhookBody(BaseModel):
    reference: str = Field(..., min_length=4, max_length=100)
    event: Literal["charge.success", "charge.failed"] = "charge.success"


__all__ = [
    "SubscriptionMeResponse",
    "PlanOption",
    "PlansResponse",
    "InitializeBody",
    "InitializeResponse",
    "CancelResponse",
    "WebhookResponse",
    "SimulateWebhookBody",
]
