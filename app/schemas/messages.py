from __future__ import annotations

"""Schemas Pydantic pour le module Messages (§5.8)."""

from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ── Request bodies ───────────────────────────────────────────────────


class SendMessageBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    client_message_id: str = Field(..., min_length=1, max_length=64)


class MeetupProposalBody(BaseModel):
    spot_id: UUID
    proposed_date: date
    proposed_time: time
    note: str | None = Field(default=None, max_length=500)
    client_message_id: str = Field(..., min_length=1, max_length=64)


class MeetupResponseBody(BaseModel):
    action: Literal["accept", "modify", "refuse"]
    counter_date: date | None = None
    counter_time: time | None = None


class ReadReceiptBody(BaseModel):
    last_read_message_id: UUID


# ── Responses ────────────────────────────────────────────────────────


MessageType = Literal["text", "voice", "meetup"]
MessageStatus = Literal["sent", "delivered", "read", "expired"]


class MessageResponse(BaseModel):
    id: UUID
    match_id: UUID
    sender_id: UUID
    content: str | None = None
    message_type: MessageType
    status: MessageStatus
    created_at: datetime
    client_message_id: str | None = None
    media_url: str | None = None
    media_duration_seconds: int | None = None
    meetup_data: dict | None = None


class MessageListResponse(BaseModel):
    messages: list[MessageResponse]
    next_cursor: str | None = None
    has_more: bool


class UnreadCountResponse(BaseModel):
    match_id: UUID
    unread_count: int


class ReadReceiptResponse(BaseModel):
    match_id: UUID
    last_read_message_id: UUID
    updated_count: int


__all__ = [
    "SendMessageBody",
    "MeetupProposalBody",
    "MeetupResponseBody",
    "ReadReceiptBody",
    "MessageResponse",
    "MessageListResponse",
    "UnreadCountResponse",
    "ReadReceiptResponse",
    "MessageType",
    "MessageStatus",
]
