from __future__ import annotations

"""Schemas Pydantic — Config (§5.14, §27, §28)."""

from pydantic import BaseModel


class VersionResponse(BaseModel):
    """GET /config/version — pas d'auth."""

    min_version: str
    current_version: str
    force_update: bool
    update_url: str


class FeatureFlagsResponse(BaseModel):
    """GET /config/feature-flags — user-scoped."""

    flags: dict[str, bool]


__all__ = ["VersionResponse", "FeatureFlagsResponse"]
