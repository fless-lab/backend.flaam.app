from __future__ import annotations

import uuid

from geoalchemy2 import Geometry
from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class Quartier(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "quartiers"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    city_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cities.id"), nullable=False
    )
    # Centroïde — historiquement la seule représentation géographique.
    # Conservé comme fallback ET comme cache du centre du polygone si
    # area est défini (calculé au seed).
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    # Zone géographique réelle (#199 R&D Phase 1). Polygone WGS84.
    # Nullable : un quartier peut exister avec juste lat/lng (legacy).
    # Quand `geolocated_quartiers_enabled=True`, l'algo de proximity
    # privilégie ce champ s'il est renseigné (overlap d'aires + distance
    # centroïdes), sinon retombe sur lat/lng.
    area = mapped_column(Geometry(geometry_type="POLYGON", srid=4326), nullable=True)

    city = relationship("City", back_populates="quartiers")
