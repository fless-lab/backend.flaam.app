from __future__ import annotations

"""Schemas Pydantic — Contacts masqués (§5.12)."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# SHA-256 = 64 hex chars. Validation stricte : le hashing est fait
# côté client, le serveur ne voit jamais de numéro en clair.
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BlacklistImportBody(BaseModel):
    phone_hashes: list[str] = Field(..., min_length=1, max_length=5000)

    @field_validator("phone_hashes")
    @classmethod
    def _validate_hashes(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        seen = set()
        for h in v:
            h_low = h.lower().strip()
            if not _SHA256_RE.match(h_low):
                raise ValueError(f"invalid_phone_hash: {h!r}")
            if h_low not in seen:
                seen.add(h_low)
                out.append(h_low)
        return out


class BlacklistImportResponse(BaseModel):
    imported: int
    skipped: int
    total: int


class BlacklistListResponse(BaseModel):
    phone_hashes: list[str]
    count: int


class BlacklistDeleteResponse(BaseModel):
    status: Literal["deleted", "not_found"]


__all__ = [
    "BlacklistImportBody",
    "BlacklistImportResponse",
    "BlacklistListResponse",
    "BlacklistDeleteResponse",
]
