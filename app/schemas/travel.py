from __future__ import annotations

"""Schemas Pydantic pour le mode voyage (§profile travel)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# Durées proposées : 3, 7 (default), 14, 30 jours. Pas de "personnalisé"
# ni de durées >30j → au-delà l'user doit changer sa ville principale.
TravelDuration = Literal["3d", "7d", "14d", "30d"]


class TravelActivateBody(BaseModel):
    """Activer le mode voyage."""
    city_id: UUID
    duration: TravelDuration = "7d"


class TravelStatusResponse(BaseModel):
    """État du mode voyage pour l'user courant.

    `is_active` est dérivé : true si travel_city_id non null ET
    travel_until > now. `can_activate` est false si déjà actif OU si
    le quota d'activations sur 30j est atteint (`activations_remaining`
    indique combien il en reste).
    """
    is_active: bool
    travel_city_id: UUID | None = None
    travel_city_name: str | None = None
    travel_started_at: datetime | None = None
    travel_until: datetime | None = None
    extension_used: bool = False
    can_extend: bool = Field(
        False,
        description="true si actif ET extension pas encore utilisée",
    )
    activations_remaining: int = Field(
        2,
        ge=0,
        le=2,
        description="Activations restantes sur la fenêtre glissante 30j",
    )
    can_activate: bool = Field(
        ...,
        description="false si déjà actif OU plus d'activations dispo",
    )


class CityChangeBody(BaseModel):
    city_id: UUID


class CityChangeResponse(BaseModel):
    city_id: UUID
    city_changed_at: datetime
    next_change_allowed_at: datetime


__all__ = [
    "TravelDuration",
    "TravelActivateBody",
    "TravelStatusResponse",
    "CityChangeBody",
    "CityChangeResponse",
]
