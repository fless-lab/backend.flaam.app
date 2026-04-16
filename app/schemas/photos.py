from __future__ import annotations

"""Schemas Pydantic pour le module Photos (spec §5.3)."""

from uuid import UUID

from pydantic import BaseModel, Field


class PhotoResponse(BaseModel):
    id: UUID
    original_url: str
    thumbnail_url: str
    medium_url: str
    display_order: int
    moderation_status: str
    width: int
    height: int
    file_size_bytes: int
    is_verified_selfie: bool = False
    dominant_color: str | None = None


class PhotoReorderBody(BaseModel):
    """
    Liste d'IDs dans le nouvel ordre d'affichage. L'index dans la liste
    devient `display_order` (0 = photo principale).
    """

    order: list[UUID] = Field(..., min_length=1, max_length=6)


__all__ = ["PhotoResponse", "PhotoReorderBody"]
