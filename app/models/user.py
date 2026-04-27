from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    phone_hash: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    phone_country_code: Mapped[str] = mapped_column(String(5), nullable=False)

    is_phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_selfie_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_id_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)

    # Mode de recherche géo (§search-area).
    # - "whole_city" (default) : l'user est ouvert à toute la ville. Le
    #   geo_scorer renvoie un score neutre (0.5) pour cet user — il n'est
    #   ni avantagé ni pénalisé par la géo. Bonus lives/works restent
    #   actifs si l'user a déclaré ces relations par ailleurs.
    # - "specific_quartiers" : l'user a coché des quartiers ciblés (stockés
    #   en UserQuartier relation_type='interested'). Le score géo passe
    #   au calcul Jaccard normal.
    feed_search_mode: Mapped[str] = mapped_column(
        String(30),
        default="whole_city",
        server_default="whole_city",
        nullable=False,
    )
    ban_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Insta-match QR — toggle sécurité (default true). Si false, les
    # scans externes sur ce user retournent 403 flame_scan_disabled.
    flame_scan_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False,
    )
    # Plafond max scans reçus / jour. User peut baisser dans
    # [1, FLAME_SCANS_RECEIVED_PER_DAY env var]. Default 10.
    flame_scans_received_max: Mapped[int] = mapped_column(
        Integer, default=10, server_default="10", nullable=False,
    )
    # Si true, refuse les scans d'un scanner non vérifié (selfie). Default
    # false pour adoption ; recommandé ON pour les femmes (UI mobile).
    flame_scan_verified_only: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False,
    )

    # Localisation éphémère pour proximity check au scan. Set quand
    # le mobile envoie sa position (PATCH /flame/me). Considérée
    # obsolète si > flame_scan_checkin_window_min (default 120 min).
    last_lat: Mapped[float | None] = mapped_column(nullable=True)
    last_lng: Mapped[float | None] = mapped_column(nullable=True)
    last_location_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Admin flag — jamais modifiable via endpoint utilisateur.
    # Promotion manuelle via psql ou script seed uniquement.
    is_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Soft-delete RGPD (§17). `deleted_at` set ⇒ pipeline RGPD déclenché.
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # city_id est renseigné à l'étape CITY_SELECTION de l'onboarding
    # (spec §13). À la création par OTP verify, il est null.
    city_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cities.id"), nullable=True
    )

    # ── Mode voyage ─────────────────────────────────────────────
    # Voyage = TEMPORAIRE. La ville principale (city_id) ne change pas.
    # Règles produit :
    #   - durées proposées : 3, 7, 14, 30 jours (default 7)
    #   - max 2 activations sur les 30 derniers jours
    #   - prolongation +7 jours, 1× par session
    #   - au-delà → l'user doit changer sa ville principale
    travel_city_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cities.id", name="fk_users_travel_city_id"),
        nullable=True,
    )
    travel_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    travel_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Compteur d'activations sur fenêtre glissante 30j. Reset quand
    # window_start est plus vieux que 30 jours.
    travel_activations_count_30d: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False,
    )
    travel_window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Prolongation +7j (1× par session, reset à la désactivation).
    travel_extension_used: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False,
    )
    # Confirmation GPS passive : set quand on détecte la position de
    # l'user dans un rayon de 30km du centre de travel_city. Reset à
    # la désactivation. Le badge mobile affiche "Confirmé" si < 24h.
    travel_gps_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Cooldown changement ville principale (1×/30j) ───────────
    city_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    onboarding_step: Mapped[str] = mapped_column(
        String(30),
        default="city_selection",
        server_default="city_selection",
        nullable=False,
    )

    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_feed_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    language: Mapped[str] = mapped_column(String(5), default="fr")
    account_created_count: Mapped[int] = mapped_column(default=1)

    # ── Programme ambassadrices (MàJ 7) ──
    # Les ambassadrices reçoivent 50 codes d'invitation et bypassent la
    # waitlist pour elles-mêmes.
    is_ambassador: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # ── Email (optionnel, encouragé pour recovery + notifications) ──
    email: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True
    )
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── MFA optionnel (PIN 6 chiffres hashé bcrypt) ──
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_pin_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Anti-bruteforce : compteur d'échecs consécutifs + lock temporaire.
    # 5 échecs → lock 15 min ; 10 échecs → lock 1h. Reset à chaque succès.
    mfa_failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    mfa_locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Recovery email (peut différer de l'email principal) ──
    recovery_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Onboarding source (MàJ 8 — 3 portes d'entrée) ──
    # classic : Play Store direct (porte 1)
    # invite  : code d'invitation (porte 2)
    # event   : pré-inscription via page web event (porte 3, ghost user)
    onboarding_source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="classic", server_default="classic"
    )
    # L'event qui a amené ce user (porte 3 uniquement, sinon null).
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "events.id",
            name="fk_users_source_event_id",
            use_alter=True,
        ),
        nullable=True,
    )
    # first_name : utilisé uniquement pour les ghost users (Porte 3).
    # Après conversion, c'est Profile.display_name qui fait foi.
    first_name: Mapped[str | None] = mapped_column(String(50), nullable=True)

    city = relationship("City", lazy="selectin", foreign_keys=[city_id])
    travel_city = relationship(
        "City", lazy="selectin", foreign_keys=[travel_city_id]
    )
    profile = relationship(
        "Profile", back_populates="user", uselist=False, lazy="selectin"
    )
    photos = relationship(
        "Photo",
        back_populates="user",
        lazy="selectin",
        order_by="Photo.display_order",
    )
    devices = relationship("Device", back_populates="user", lazy="selectin")
    user_quartiers = relationship(
        "UserQuartier", back_populates="user", lazy="selectin"
    )
    user_spots = relationship("UserSpot", back_populates="user", lazy="selectin")
    subscription = relationship(
        "Subscription", back_populates="user", uselist=False, lazy="selectin"
    )
    notification_prefs = relationship(
        "NotificationPreference",
        back_populates="user",
        uselist=False,
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_users_city_active", "city_id", "last_active_at"),
        Index("ix_users_city_visible", "city_id", "is_visible", "is_active"),
    )
