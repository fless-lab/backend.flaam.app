from __future__ import annotations

"""
FlameScanAttempt — log de chaque tentative de scan QR (succès et échec).

Sert :
1. Sécurité user (femmes surtout) : voir qui a tenté de me scanner et
   pourquoi ça a échoué (disabled, too_far, blocked, rate_limit). Permet
   de détecter QR fuités, harceleurs persistants, patterns suspects.
2. Métriques produit : taux de succès / origine échec.

Tous les statuts sont loggés (incl. matched, idempotent). L'utilisateur
final voit la liste filtrée selon son niveau premium :
- Free : count agrégé du jour
- Premium : liste détaillée 30 dernières tentatives reçues
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


# Statuts loggés : aligné avec les erreurs de instant_match_service.
# matched = succès (Match créé). idempotent = match récent existant
# renvoyé. Tous les autres = échecs.
ATTEMPT_STATUS_VALUES = {
    "matched",
    "idempotent",
    "self_scan",
    "qr_invalid_or_expired",
    "flame_scan_disabled",
    "scanner_unavailable",
    "target_unavailable",
    "selfie_not_verified",
    "different_cities",
    "gender_incompatible",
    "age_out_of_range",
    "blocked",
    "scans_sent_today_exceeded",
    "scans_received_today_exceeded",
    "location_required",
    "target_location_unknown",
    "too_far_from_target",
}


class FlameScanAttempt(Base, UUIDMixin):
    __tablename__ = "flame_scan_attempts"

    scanner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Peut être NULL si lookup token a échoué (qr_invalid_or_expired).
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    # Coordonnées scanner au moment du scan (audit).
    scanner_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    scanner_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True,
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    __table_args__ = (
        Index("ix_flame_scan_attempts_target_at", "target_id", "at"),
        Index("ix_flame_scan_attempts_scanner_at", "scanner_id", "at"),
    )
