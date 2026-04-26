from __future__ import annotations

"""Schemas Pydantic pour le mode voyage (§profile travel)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class TravelActivateBody(BaseModel):
    """Activer le mode voyage.

    `duration_days` : 1..30. Le mobile expose des presets 3/7/14/30 +
    un picker "Personnaliser" (slider 1-30). Aucune durée >30j ;
    au-delà l'user doit changer sa ville principale.
    """
    city_id: UUID
    duration_days: int = Field(7, ge=1, le=30)


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
    gps_confirmed: bool = Field(
        False,
        description="True si présence GPS validée dans la ville de destination < 24h",
    )
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
    "TravelActivateBody",
    "TravelStatusResponse",
    "CityChangeBody",
    "CityChangeResponse",
]
