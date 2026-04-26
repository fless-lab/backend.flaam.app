from __future__ import annotations

"""
Instant match service — création d'un match direct via scan QR IRL.

Le scan d'un QR (encodé `qr_token` rotatif) crée un match `status=matched`
sans réciprocité, parce que l'effort IRL (la rencontre physique) tient
lieu de "double opt-in".

Vérifications appliquées (dans l'ordre, échec à la première qui plante) :
  1. Token valide + non expiré + ≠ self-scan.
  2. Toggle `target.flame_scan_enabled = true`.
  3. Hard filters : same city / both selfie verified / not banned-deleted /
     not blocked bidir / gender compat bidir / age compat bidir.
  4. Idempotence 24h : même paire scanner/target déjà matchée via QR <24h ?
     → renvoyer le match existant (idempotent).
  5. Rate limits : scans envoyés <= FLAME_SCANS_SENT_PER_DAY (5 pour tous,
     anti-spam) ; scans reçus <= target.flame_scans_received_max
     (max FLAME_SCANS_RECEIVED_PER_DAY, baissable par l'user).
  6. Proximity check (anti-faux-positifs WhatsApp share) :
     (a) event_id partagé + 2 EventCheckin <flame_scan_checkin_window_min → OK ;
     (b) sinon, scanner_lat/lng vs target.last_lat/lng — Haversine ≤ flame_scan_max_distance_m → OK ;
     (c) sinon → 422 too_far_from_target / location_required.

Création :
  - Match(origin="instant_qr", status="matched", matched_at=now,
          event_id=event_id?, geo_score/lifestyle_score=NULL)
  - Push WS + FCM (à wire avec push_service existant)
  - Réponse {match_id, other_user, icebreaker}
"""

import math
from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppException

log = structlog.get_logger()
from app.models.block import Block
from app.models.event_registration import EventRegistration
from app.models.match import Match
from app.models.profile import Profile
from app.models.user import User
from app.services import flame_service


settings = get_settings()


# ── Helpers ─────────────────────────────────────────────────────────


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance en mètres entre deux points GPS."""
    R = 6_371_000  # rayon Terre en m
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _today_start_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


# ── Filtres ─────────────────────────────────────────────────────────


def _check_safety_filters(scanner: User, target: User) -> None:
    """
    Filtres SAFETY uniquement — IRL > algo (cf. roadmap-irl-loop.md).

    Insta-match BYPASS volontairement : gender, age, city, intention,
    seeking_gender. Raison produit : si l'user a montré son QR IRL,
    c'est une validation plus forte que l'algo. Bloquer sur ces critères
    crée un malaise IRL ("désolé t'es trop âgée").

    On garde uniquement les filtres de SÉCURITÉ vraie :
    - self-scan (technique)
    - banned / deleted (sanction admin)
    - target inactive/invisible (l'user a explicitement masqué son
      compte → on respecte sa volonté)
    - profile incomplet (target n'a pas fini onboarding)

    selfie_verified n'est PAS un blocker — c'est un SIGNAL (visible dans
    la response sous target_verified). Sauf si le target a explicitement
    activé `flame_scan_verified_only=true` côté ses paramètres flame.
    """
    if scanner.id == target.id:
        raise AppException(400, "self_scan")
    if scanner.is_banned or scanner.is_deleted:
        raise AppException(403, "scanner_unavailable")
    if (
        target.is_banned
        or target.is_deleted
        or not target.is_active
        or not target.is_visible
    ):
        raise AppException(403, "target_unavailable")
    if target.profile is None:
        raise AppException(400, "target_profile_incomplete")

    # Verified-only : si target l'a activé, scanner doit être verified.
    if (
        getattr(target, "flame_scan_verified_only", False)
        and not scanner.is_selfie_verified
    ):
        raise AppException(403, "target_requires_verified")


async def _check_block(scanner_id: UUID, target_id: UUID, db: AsyncSession) -> None:
    res = await db.execute(
        select(Block).where(
            or_(
                and_(Block.blocker_id == scanner_id, Block.blocked_id == target_id),
                and_(Block.blocker_id == target_id, Block.blocked_id == scanner_id),
            ),
        ),
    )
    if res.first() is not None:
        raise AppException(403, "blocked")


# ── Rate limits ─────────────────────────────────────────────────────


async def _count_instant_today(user_id: UUID, role: str, db: AsyncSession) -> int:
    """role ∈ {'scanner', 'target'}. Compte les instant matches initiés / reçus aujourd'hui (UTC)."""
    today = _today_start_utc()
    column = Match.user_a_id if role == "scanner" else Match.user_b_id
    result = await db.execute(
        select(func.count(Match.id)).where(
            column == user_id,
            Match.origin == "instant_qr",
            Match.created_at >= today,
        ),
    )
    return result.scalar_one() or 0


async def _check_rate_limits(scanner: User, target: User, db: AsyncSession) -> None:
    sent = await _count_instant_today(scanner.id, "scanner", db)
    if sent >= settings.flame_scans_sent_per_day:
        raise AppException(429, f"flame_scans_sent_today_exceeded:{settings.flame_scans_sent_per_day}")
    received = await _count_instant_today(target.id, "target", db)
    if received >= target.flame_scans_received_max:
        # Le scanner reçoit l'erreur, le target n'est pas notifié — politesse silencieuse.
        raise AppException(429, "target_received_today_exceeded")


# ── Proximity check ─────────────────────────────────────────────────


async def _check_proximity(
    scanner: User,
    target: User,
    scanner_lat: float | None,
    scanner_lng: float | None,
    event_id: UUID | None,
    db: AsyncSession,
) -> None:
    """
    Anti-faux-positifs : empêcher qu'un QR partagé par WhatsApp à un pote
    distant soit utilisé pour matcher des inconnus à distance.

    Validation layered :
      (a) event_id + 2 EventCheckin/EventRegistration récents → OK
      (b) scanner_lat/lng vs target.last_lat/lng — Haversine ≤ max_distance → OK
      (c) sinon → 422
    """
    window = timedelta(minutes=settings.flame_scan_checkin_window_min)
    now = datetime.now(timezone.utc)

    # (a) event_id partagé : vérifier que les 2 ont une registration récente
    # (EventCheckin distinct sera ajouté task #103 — pour l'instant on
    # se contente d'EventRegistration récente comme proxy).
    if event_id is not None:
        result = await db.execute(
            select(EventRegistration.user_id).where(
                EventRegistration.event_id == event_id,
                EventRegistration.user_id.in_([scanner.id, target.id]),
                EventRegistration.created_at >= now - window,
            ),
        )
        users_registered = {row[0] for row in result.all()}
        if scanner.id in users_registered and target.id in users_registered:
            return  # OK via event

    # (b) Haversine scanner_lat/lng vs target.last_lat/lng
    if scanner_lat is None or scanner_lng is None:
        raise AppException(422, "location_required")

    if (
        target.last_lat is None
        or target.last_lng is None
        or target.last_location_at is None
    ):
        raise AppException(422, "target_location_unknown")

    target_loc_at = target.last_location_at
    if target_loc_at.tzinfo is None:
        target_loc_at = target_loc_at.replace(tzinfo=timezone.utc)
    if now - target_loc_at > window:
        raise AppException(422, "target_location_stale")

    distance = _haversine_m(scanner_lat, scanner_lng, target.last_lat, target.last_lng)
    if distance > settings.flame_scan_max_distance_m:
        raise AppException(422, f"too_far_from_target:{int(distance)}m")


# ── Idempotence ─────────────────────────────────────────────────────


async def _find_existing_match(
    scanner_id: UUID, target_id: UUID, db: AsyncSession,
) -> Match | None:
    """
    Cherche un match entre cette paire (TOUTES origines confondues).

    Cas couverts :
    - Match instant_qr récent → idempotent, on retourne tel quel.
    - Match feed_like pending (l'un avait liké via le feed sans réponse) →
      on UPGRADE à matched + origin=instant_qr (le scan IRL fait office
      de double opt-in instantané).
    - Match feed_like matched déjà → idempotent, on retourne.
    - Match unmatched → on respecte la décision, raise blocked-like.
    """
    result = await db.execute(
        select(Match).where(
            or_(
                and_(Match.user_a_id == scanner_id, Match.user_b_id == target_id),
                and_(Match.user_a_id == target_id, Match.user_b_id == scanner_id),
            ),
        ).limit(1),
    )
    return result.scalar_one_or_none()


# ── Public API ──────────────────────────────────────────────────────


async def _log_attempt(
    scanner_id: UUID,
    target_id: UUID | None,
    status: str,
    scanner_lat: float | None,
    scanner_lng: float | None,
    event_id: UUID | None,
    db: AsyncSession,
) -> None:
    """Log une tentative de scan pour l'historique sécurité."""
    from app.models.flame_scan_attempt import FlameScanAttempt
    attempt = FlameScanAttempt(
        scanner_id=scanner_id,
        target_id=target_id,
        status=status,
        scanner_lat=scanner_lat,
        scanner_lng=scanner_lng,
        event_id=event_id,
        at=datetime.now(timezone.utc),
    )
    db.add(attempt)
    await db.commit()


async def create_instant_match(
    scanner: User,
    scanned_qr_token: str,
    scanner_lat: float | None,
    scanner_lng: float | None,
    event_id: UUID | None,
    db: AsyncSession,
) -> tuple[Match, User]:
    """
    Crée un match direct via scan QR. Retourne (match, target_user).
    Idempotent <24h : si match récent existe, le renvoie.

    Log la tentative dans flame_scan_attempts (succès et échecs) pour
    l'historique sécurité user.
    """
    try:
        return await _create_instant_match_inner(
            scanner, scanned_qr_token, scanner_lat, scanner_lng, event_id, db,
        )
    except AppException as e:
        # Échec : on essaie de retrouver le target_id (le service a déjà
        # tenté le lookup, mais l'exception peut survenir avant). On log
        # avec target_id=None si pas résolu — l'historique sécurité du
        # target le verra quand même (les logs réussis remontent à lui).
        target = await flame_service.find_user_by_token(scanned_qr_token, db)
        target_id = target.id if target else None
        await _log_attempt(
            scanner.id, target_id,
            str(e.detail), scanner_lat, scanner_lng, event_id, db,
        )
        raise


async def _create_instant_match_inner(
    scanner: User,
    scanned_qr_token: str,
    scanner_lat: float | None,
    scanner_lng: float | None,
    event_id: UUID | None,
    db: AsyncSession,
) -> tuple[Match, User]:
    """Logique principale (sans wrapping log). Le wrapper externe loggue."""
    # 1. Lookup target via token (incl. expiration check)
    target = await flame_service.find_user_by_token(scanned_qr_token, db)
    if target is None:
        raise AppException(404, "qr_invalid_or_expired")

    # Hot-load target.profile
    if target.profile is None:
        prof_result = await db.execute(
            select(Profile).where(Profile.user_id == target.id),
        )
        target.profile = prof_result.scalar_one_or_none()

    # 2. Toggle
    if not target.flame_scan_enabled:
        raise AppException(403, "flame_scan_disabled")

    # 3. Safety filters (IRL > algo : on retire age/gender/city volontairement)
    _check_safety_filters(scanner, target)
    await _check_block(scanner.id, target.id, db)

    # 4. Match existant entre cette paire ?
    existing = await _find_existing_match(scanner.id, target.id, db)
    if existing is not None:
        # Cas 1: déjà unmatched → respecter la décision (refus comme un block)
        if existing.status == "unmatched":
            raise AppException(403, "previously_unmatched")
        # Cas 2: déjà matched (peu importe origin) → idempotent
        if existing.status == "matched":
            await _log_attempt(
                scanner.id, target.id, "idempotent",
                scanner_lat, scanner_lng, event_id, db,
            )
            return existing, target
        # Cas 3: pending (un like sans réponse via le feed) → UPGRADE
        # Le scan IRL fait office de "double opt-in instantané".
        if existing.status == "pending":
            existing.status = "matched"
            existing.origin = "instant_qr"
            existing.matched_at = datetime.now(timezone.utc)
            if event_id is not None:
                existing.event_id = event_id
            await db.commit()
            await db.refresh(existing)
            await _log_attempt(
                scanner.id, target.id, "matched",
                scanner_lat, scanner_lng, event_id, db,
            )
            return existing, target

    # 5. Rate limits
    await _check_rate_limits(scanner, target, db)

    # 6. Proximity
    await _check_proximity(scanner, target, scanner_lat, scanner_lng, event_id, db)

    # 7. Création du match
    now = datetime.now(timezone.utc)
    match = Match(
        user_a_id=scanner.id,
        user_b_id=target.id,
        status="matched",
        matched_at=now,
        origin="instant_qr",
        event_id=event_id,
    )
    db.add(match)
    await db.commit()
    await db.refresh(match)
    await _log_attempt(
        scanner.id, target.id, "matched",
        scanner_lat, scanner_lng, event_id, db,
    )
    # Side-effect : confirme le voyage du scanner si le scan a eu lieu
    # dans la ville de destination. Silencieux si pas en voyage / loin.
    if scanner_lat is not None and scanner_lng is not None:
        from app.services import travel_service as _travel
        await _travel.try_confirm_travel(scanner, scanner_lat, scanner_lng, db)
    # Push WS aux 2 users en simultané (animation match synchronisée).
    # Best-effort, no-op si l'un ou l'autre n'est pas connecté.
    await _broadcast_instant_match(scanner, target, match)
    return match, target


async def _broadcast_instant_match(
    scanner: User, target: User, match: Match,
) -> None:
    """Pousse `instant_match_created` aux 2 user_ids via WebSocket.

    Permet aux 2 phones de déclencher l'animation MatchOverlay en même
    temps (delta typique <200ms). Inclut le `partner_seed` (= partner.user_id)
    pour que la collision animation rende le sceau de l'autre.
    """
    # Import local pour éviter cycle import (instant_match ⇄ ws.chat).
    from app.ws.chat import connection_manager

    def _payload_for(viewer: User, partner: User) -> dict:
        partner_name = (
            partner.profile.display_name if partner.profile else None
        )
        return {
            "type": "instant_match_created",
            "match_id": str(match.id),
            "partner_id": str(partner.id),
            "partner_seed": str(partner.id),
            "partner_name": partner_name,
            "partner_verified": partner.is_selfie_verified,
            "created_at": match.created_at.isoformat(),
        }

    try:
        await connection_manager.send_to(
            scanner.id, _payload_for(scanner, target),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "instant_match_ws_push_failed",
            user_id=str(scanner.id),
            err=str(exc),
        )
    try:
        await connection_manager.send_to(
            target.id, _payload_for(target, scanner),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "instant_match_ws_push_failed",
            user_id=str(target.id),
            err=str(exc),
        )


def build_icebreaker(event_id: UUID | None) -> str:
    """
    Copy human-centered, pas Tinder-bombast (cf. roadmap-irl-loop.md).
    "Vous avez allumé la flamme. Maintenant à vous de l'entretenir."
    """
    if event_id is not None:
        return "Vous vous êtes croisés — maintenant à vous de l'entretenir."
    return "Vous avez allumé la flamme. Maintenant à vous de l'entretenir."
