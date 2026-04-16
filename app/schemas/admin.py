from __future__ import annotations

"""Schemas Pydantic — Admin (§20, §22)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ── Reports ────────────────────────────────────────────────────────────

class AdminReportItem(BaseModel):
    id: UUID
    reporter_id: UUID
    reported_user_id: UUID
    reason: str
    description: str | None
    status: str
    resolution_note: str | None
    resolved_by: str | None
    created_at: datetime


class AdminReportListResponse(BaseModel):
    items: list[AdminReportItem]
    total: int


class AdminReportAction(BaseModel):
    action: Literal["resolve", "dismiss"]
    note: str | None = Field(default=None, max_length=1000)


# ── Users ──────────────────────────────────────────────────────────────

class AdminUserItem(BaseModel):
    id: UUID
    phone_hash: str
    display_name: str | None
    gender: str | None
    city_id: UUID | None
    is_active: bool
    is_banned: bool
    is_deleted: bool
    is_premium: bool
    is_selfie_verified: bool
    is_admin: bool
    created_at: datetime


class AdminUserListResponse(BaseModel):
    items: list[AdminUserItem]
    total: int


class AdminUserDetail(AdminUserItem):
    ban_reason: str | None
    deleted_at: datetime | None
    account_history: dict[str, Any] | None


class AdminBanBody(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class AdminGenderChangeBody(BaseModel):
    new_gender: Literal["man", "woman", "non_binary"]
    reason: str = Field(min_length=3, max_length=500)


# ── Stats ──────────────────────────────────────────────────────────────

class AdminDashboardStats(BaseModel):
    active_users_7d: int
    matches_per_day: float
    gender_ratio_by_city: dict[str, dict[str, int]]
    churn_30d: float
    premium_count: int
    revenue_estimated_30d: int  # en XOF


# ── Events ─────────────────────────────────────────────────────────────

class AdminEventCreateBody(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    spot_id: UUID
    city_id: UUID
    starts_at: datetime
    ends_at: datetime | None = None
    category: str = Field(min_length=2, max_length=30)
    max_attendees: int | None = Field(default=None, ge=1)
    is_sponsored: bool = False
    sponsor_name: str | None = None
    status: Literal["draft", "published"] = "draft"


class AdminEventUpdateBody(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    max_attendees: int | None = Field(default=None, ge=1)
    is_sponsored: bool | None = None
    sponsor_name: str | None = None
    status: Literal["draft", "published", "cancelled", "completed"] | None = None


class AdminEventItem(BaseModel):
    id: UUID
    title: str
    city_id: UUID
    spot_id: UUID
    starts_at: datetime
    status: str
    current_attendees: int
    max_attendees: int | None


class AdminEventListResponse(BaseModel):
    items: list[AdminEventItem]
    total: int


# ── Spots ──────────────────────────────────────────────────────────────

class AdminSpotValidateBody(BaseModel):
    action: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=500)


# ── Photos moderation ──────────────────────────────────────────────────

class AdminPhotoItem(BaseModel):
    id: UUID
    user_id: UUID
    thumbnail_url: str
    moderation_status: str
    moderation_score: float | None
    rejection_reason: str | None
    created_at: datetime


class AdminPhotoListResponse(BaseModel):
    items: list[AdminPhotoItem]
    total: int


class AdminPhotoModerateBody(BaseModel):
    action: Literal["approve", "reject"]
    rejection_reason: str | None = Field(default=None, max_length=200)


class AdminPhotoBulkApproveBody(BaseModel):
    photo_ids: list[UUID] = Field(min_length=1, max_length=100)


class AdminPhotoStats(BaseModel):
    pending: int
    approved: int
    rejected: int
    review: int


# ── Matching config ────────────────────────────────────────────────────

class AdminMatchingConfigItem(BaseModel):
    key: str
    value: float
    category: str | None
    description: str | None


class AdminMatchingConfigListResponse(BaseModel):
    items: list[AdminMatchingConfigItem]


class AdminMatchingConfigUpdateBody(BaseModel):
    value: float


# ── Prompts stats ──────────────────────────────────────────────────────

class AdminPromptStatItem(BaseModel):
    question: str
    total_likes: int
    usage_count: int


class AdminPromptStatsResponse(BaseModel):
    items: list[AdminPromptStatItem]


# ── Batch trigger ──────────────────────────────────────────────────────

class AdminBatchTriggerBody(BaseModel):
    city_id: UUID | None = None


class AdminBatchTriggerResponse(BaseModel):
    task_id: str
    status: Literal["queued"]


# ── Ambassadors ────────────────────────────────────────────────────────

class AdminAmbassadorItem(BaseModel):
    user_id: UUID
    display_name: str | None
    phone_hash: str
    codes_generated: int
    codes_redeemed: int
    created_at: datetime


class AdminAmbassadorListResponse(BaseModel):
    items: list[AdminAmbassadorItem]


class AdminAmbassadorPromoteBody(BaseModel):
    user_id: UUID
    code_count: int = Field(default=50, ge=1, le=100)


# ── Waitlist ───────────────────────────────────────────────────────────

class AdminWaitlistStats(BaseModel):
    total_waiting: int
    min_position: int | None
    max_position: int | None
    gender_ratio: dict[str, int]
    by_city: dict[str, int]


class AdminWaitlistReleaseBody(BaseModel):
    city_id: UUID
    count: int = Field(ge=1, le=500)


class AdminWaitlistReleaseResponse(BaseModel):
    released: int


# ── Generic ack response ───────────────────────────────────────────────

class AdminAckResponse(BaseModel):
    status: Literal["ok"] = "ok"


class AdminDeleteResponse(BaseModel):
    status: Literal["deleted"] = "deleted"


class AdminBulkApproveResponse(BaseModel):
    approved: int

