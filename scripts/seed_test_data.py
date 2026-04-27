from __future__ import annotations

"""
Seed de données de test pour le compte d'un développeur (identifié par
son numéro de téléphone). Permet de tester rapidement l'app mobile en
beta sur Waydroid avec un environnement riche : spots, feed, likes
reçus, matchs, messages et une proposition de meetup active.

Usage :
    docker compose exec api python -m scripts.seed_test_data +22899000999

Idempotent : on teste l'existence par phone_hash / (city_id, name) /
(user_a_id, user_b_id) / couples user_quartier / user_spot. Pas d'upsert —
on skip simplement si déjà présent.

Pré-requis :
    - seed_base_data a déjà tourné (TG/Lomé + 5 quartiers + 3 spots)
    - Le compte cible existe (il doit s'être connecté au moins une fois
      via l'app pour passer l'OTP)
"""

import asyncio
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone

from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session
from app.models.city import City
from app.models.match import Match
from app.models.message import Message
from app.models.photo import Photo
from app.models.profile import Profile
from app.models.quartier import Quartier
from app.models.spot import Spot
from app.models.user import User
from app.models.user_quartier import UserQuartier
from app.models.user_spot import UserSpot
from app.utils.phone import InvalidPhoneError, hash_phone, normalize_phone


# ══════════════════════════════════════════════════════════════════════
# Catalogue de spots additionnels (Lomé)
# ══════════════════════════════════════════════════════════════════════
#
# Coordonnées approximatives autour du centre de Lomé (6.1375°N, 1.2123°E).
# Les catégories sont celles acceptées par SPOT_SOCIAL_WEIGHTS
# (voir app/core/constants.py) : bar, maquis, restaurant, cafe, club,
# gym, coworking, market, park, beach, cultural.
EXTRA_SPOTS = [
    {"name": "Le Galion", "category": "restaurant",
     "latitude": 6.1265, "longitude": 1.2185,
     "address": "Boulevard du Mono, Lomé"},
    {"name": "Marly Hotel Terrace", "category": "bar",
     "latitude": 6.1342, "longitude": 1.2172,
     "address": "Rue Kpalimé, Lomé"},
    {"name": "Alt Munchen", "category": "bar",
     "latitude": 6.1412, "longitude": 1.2145,
     "address": "Rue du Commerce, Lomé"},
    {"name": "Tropical Cafe", "category": "cafe",
     "latitude": 6.1558, "longitude": 1.2096,
     "address": "Avenue de la Libération, Tokoin"},
    {"name": "Chez Yolande", "category": "restaurant",
     "latitude": 6.1389, "longitude": 1.2225,
     "address": "Bè-Klikamé, Lomé"},
    {"name": "Shot", "category": "club",
     "latitude": 6.1475, "longitude": 1.2132,
     "address": "Hédzranawoé, Lomé"},
    {"name": "Green Garden", "category": "cafe",
     "latitude": 6.1622, "longitude": 1.2034,
     "address": "Djidjolé, Lomé"},
    {"name": "Thermo Fit Kegue", "category": "gym",
     "latitude": 6.1829, "longitude": 1.2193,
     "address": "Kégué, Lomé"},
    {"name": "UniSport Lomé", "category": "gym",
     "latitude": 6.1705, "longitude": 1.2159,
     "address": "Tokoin-Casablanca, Lomé"},
    {"name": "Papaye", "category": "maquis",
     "latitude": 6.1351, "longitude": 1.2265,
     "address": "Bè-Apéyémé, Lomé"},
    {"name": "Pili Pili", "category": "maquis",
     "latitude": 6.1298, "longitude": 1.2198,
     "address": "Nyékonakpoè, Lomé"},
    {"name": "La Kôra", "category": "maquis",
     "latitude": 6.1368, "longitude": 1.2148,
     "address": "Kodjoviakopé, Lomé"},
    {"name": "INA Lomé", "category": "other",
     "latitude": 6.1302, "longitude": 1.2087,
     "address": "Institut National des Arts, Lomé"},
    {"name": "Musée National", "category": "other",
     "latitude": 6.1318, "longitude": 1.2156,
     "address": "Palais des Congrès, Lomé"},
    {"name": "Palais de Lomé", "category": "other",
     "latitude": 6.1288, "longitude": 1.2132,
     "address": "Boulevard du 13 Janvier, Lomé"},
]


# ══════════════════════════════════════════════════════════════════════
# Utilisateurs factices
# ══════════════════════════════════════════════════════════════════════

PROMPT_POOL = [
    {"question": "Mon maquis préféré",
     "answer": "Chez Yolande, le poulet braisé est légendaire."},
    {"question": "Un dimanche idéal",
     "answer": "Plage de Baguida, grillades et coucher de soleil."},
    {"question": "Ce que je cherche",
     "answer": "Quelqu'un qui aime rire et sortir sans chichis."},
    {"question": "Mon hot take",
     "answer": "Le fufu bat le ryz pour moi, désolée pas désolée."},
    {"question": "Ma dernière série binge-watchée",
     "answer": "Queen Sono — je veux aller à Jo'burg maintenant."},
    {"question": "Mon sport du weekend",
     "answer": "Footing le long du Boulevard du Mono, 6h du mat."},
    {"question": "Un talent caché",
     "answer": "Je fais le meilleur attiéké-poisson de Lomé."},
    {"question": "Mon rêve voyage",
     "answer": "Cap Skirring au Sénégal, un jour."},
]

TAG_POOL = [
    "foot", "cinema", "lecture", "musique", "cuisine", "gaming",
    "voyage", "art", "mode", "tech", "photo", "dance",
    "nature", "fitness", "startup", "afrobeats", "jazz", "basket",
]

FAKE_USERS = [
    {"name": "Ama", "gender": "woman", "seeking": "men",
     "age": 26, "sector": "commerce",
     "intention": "serious", "role": "like_received"},
    {"name": "Kofi", "gender": "man", "seeking": "women",
     "age": 29, "sector": "tech",
     "intention": "serious", "role": "like_received"},
    {"name": "Esi", "gender": "woman", "seeking": "men",
     "age": 24, "sector": "creative",
     "intention": "getting_to_know", "role": "like_received"},
    {"name": "Kwame", "gender": "man", "seeking": "women",
     "age": 31, "sector": "finance",
     "intention": "serious", "role": "match_with_messages"},
    {"name": "Akosua", "gender": "woman", "seeking": "men",
     "age": 27, "sector": "health",
     "intention": "getting_to_know", "role": "match_icebreaker"},
    {"name": "Yaa", "gender": "woman", "seeking": "men",
     "age": 25, "sector": "education",
     "intention": "open", "role": "feed"},
    {"name": "Mensah", "gender": "man", "seeking": "women",
     "age": 28, "sector": "public_admin",
     "intention": "serious", "role": "feed"},
    {"name": "Efua", "gender": "woman", "seeking": "men",
     "age": 30, "sector": "tech",
     "intention": "serious", "role": "match_meetup"},
]

# Numéros de test au format +22879000001..+22879000008 (8 chiffres
# après +228, cf. normalize_phone qui accepte 8-15 chiffres).
FAKE_PHONE_TEMPLATE = "+22879{:06d}"


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def _birthdate_for_age(age: int) -> date:
    """Génère une date de naissance cohérente avec l'âge cible."""
    today = date.today()
    return date(today.year - age, 6, 15)


def _pick(lst: list, n: int, offset: int = 0) -> list:
    """Sélection déterministe (pas de random — idempotence)."""
    if not lst:
        return []
    return [lst[(offset + i) % len(lst)] for i in range(n)]


def _pravatar_url(idx: int, size: int = 600) -> str:
    return f"https://i.pravatar.cc/{size}?img={idx}"


async def _get_lome(db: AsyncSession) -> City:
    row = await db.execute(
        select(City).where(
            City.country_code == "TG", City.name == "Lomé"
        )
    )
    city = row.scalar_one_or_none()
    if city is None:
        raise RuntimeError(
            "Ville Lomé introuvable. Lance d'abord `python -m scripts.seed_base_data`."
        )
    return city


async def _get_quartiers(db: AsyncSession, city_id: uuid.UUID) -> list[Quartier]:
    rows = await db.execute(
        select(Quartier).where(Quartier.city_id == city_id).order_by(Quartier.name)
    )
    quartiers = list(rows.scalars().all())
    if not quartiers:
        raise RuntimeError(
            "Aucun quartier en DB. Lance d'abord `python -m scripts.seed_base_data`."
        )
    return quartiers


# ══════════════════════════════════════════════════════════════════════
# 1. Target user — lookup + ensure matchable
# ══════════════════════════════════════════════════════════════════════

async def find_target_user(db: AsyncSession, phone_e164: str) -> User:
    ph_hash = hash_phone(phone_e164)
    row = await db.execute(select(User).where(User.phone_hash == ph_hash))
    user = row.scalar_one_or_none()
    if user is None:
        raise SystemExit(
            f"User with phone {phone_e164} not found. "
            f"Sign in first with the app to create the account."
        )
    return user


async def ensure_target_matchable(
    db: AsyncSession, user: User, lome: City, quartiers: list[Quartier]
) -> Profile:
    """
    S'assure que le compte cible a un profil complet (display_name,
    birth_date, gender, seeking_gender, city = Lomé, onboarding=completed),
    au moins une photo, et deux quartiers liés. Ne touche pas aux champs
    déjà renseignés.
    """
    # City
    if user.city_id != lome.id:
        user.city_id = lome.id
    user.is_visible = True
    user.is_active = True
    user.is_phone_verified = True
    user.is_selfie_verified = True
    user.onboarding_step = "completed"
    if user.language is None:
        user.language = "fr"

    # Profile — on ne passe PAS par user.profile (lazy-load interdit en async)
    profile_row = await db.execute(
        select(Profile).where(Profile.user_id == user.id)
    )
    profile = profile_row.scalar_one_or_none()
    if profile is None:
        profile = Profile(
            user_id=user.id,
            display_name="Raouf (test)",
            birth_date=_birthdate_for_age(28),
            gender="man",
            seeking_gender="women",
            intention="serious",
            sector="tech",
            prompts=[p for p in PROMPT_POOL[:3]],
            tags=["tech", "foot", "cinema", "cuisine"],
            languages=["fr", "en"],
            seeking_age_min=18,
            seeking_age_max=35,
            profile_completeness=0.8,
        )
        db.add(profile)
        await db.flush()
    else:
        # Ne remplir que les trous
        if not profile.display_name:
            profile.display_name = "Raouf (test)"
        if profile.birth_date is None:
            profile.birth_date = _birthdate_for_age(28)
        if not profile.gender:
            profile.gender = "man"
        if not profile.seeking_gender:
            profile.seeking_gender = "women"
        if not profile.prompts:
            profile.prompts = [p for p in PROMPT_POOL[:3]]
        if not profile.tags:
            profile.tags = ["tech", "foot", "cinema", "cuisine"]
        if not profile.languages:
            profile.languages = ["fr", "en"]

    # Photo (au moins une)
    photos_row = await db.execute(
        select(Photo).where(Photo.user_id == user.id, Photo.is_deleted.is_(False))
    )
    has_photo = photos_row.scalars().first() is not None
    if not has_photo:
        url = _pravatar_url(12)
        db.add(
            Photo(
                user_id=user.id,
                original_url=url,
                thumbnail_url=url,
                medium_url=url,
                blurred_url=url,
                display_order=0,
                is_verified_selfie=True,
                content_hash=f"seed-target-{user.id.hex[:16]}",
                width=600,
                height=600,
                file_size_bytes=50_000,
                moderation_status="approved",
                dominant_color="#D85A30",
            )
        )

    # 2 quartiers (lives + hangs) sur les deux premiers
    await _ensure_user_quartier(db, user.id, quartiers[0].id, "lives", is_primary=True)
    if len(quartiers) > 1:
        await _ensure_user_quartier(db, user.id, quartiers[1].id, "hangs")

    await db.flush()
    return profile


# ══════════════════════════════════════════════════════════════════════
# 2. Spots
# ══════════════════════════════════════════════════════════════════════

async def seed_extra_spots(
    db: AsyncSession, lome: City
) -> tuple[list[Spot], int]:
    created: list[Spot] = []
    added = 0
    for spec in EXTRA_SPOTS:
        row = await db.execute(
            select(Spot).where(Spot.city_id == lome.id, Spot.name == spec["name"])
        )
        existing = row.scalar_one_or_none()
        if existing is not None:
            created.append(existing)
            continue
        geom = from_shape(
            Point(spec["longitude"], spec["latitude"]), srid=4326
        )
        spot = Spot(
            name=spec["name"],
            category=spec["category"],
            city_id=lome.id,
            location=geom,
            latitude=spec["latitude"],
            longitude=spec["longitude"],
            address=spec["address"],
            is_verified=True,
            is_active=True,
        )
        db.add(spot)
        created.append(spot)
        added += 1
    await db.flush()
    return created, added


# ══════════════════════════════════════════════════════════════════════
# 3. Fake users
# ══════════════════════════════════════════════════════════════════════

async def _ensure_user_quartier(
    db: AsyncSession,
    user_id: uuid.UUID,
    quartier_id: uuid.UUID,
    relation_type: str,
    is_primary: bool = False,
) -> None:
    row = await db.execute(
        select(UserQuartier).where(
            UserQuartier.user_id == user_id,
            UserQuartier.quartier_id == quartier_id,
            UserQuartier.relation_type == relation_type,
        )
    )
    existing = row.scalar_one_or_none()
    if existing is not None:
        return
    db.add(
        UserQuartier(
            user_id=user_id,
            quartier_id=quartier_id,
            relation_type=relation_type,
            is_primary=is_primary,
            is_active_in_matching=True,
        )
    )


async def _ensure_user_spot(
    db: AsyncSession, user_id: uuid.UUID, spot_id: uuid.UUID
) -> None:
    row = await db.execute(
        select(UserSpot).where(
            UserSpot.user_id == user_id, UserSpot.spot_id == spot_id
        )
    )
    existing = row.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if existing is None:
        db.add(
            UserSpot(
                user_id=user_id,
                spot_id=spot_id,
                checkin_count=1,
                first_checkin_at=now,
                last_checkin_at=now,
                fidelity_level="declared",
                fidelity_score=0.6,
                is_visible=True,
                is_active_in_matching=True,
            )
        )


async def seed_fake_user(
    db: AsyncSession,
    spec: dict,
    idx: int,
    lome: City,
    quartiers: list[Quartier],
    existing_spots: list[Spot],
) -> User:
    """
    Crée (ou retrouve) un fake user complet : User + Profile + Photos
    + 2 UserQuartier + 1-2 UserSpot.
    """
    phone = FAKE_PHONE_TEMPLATE.format(idx)
    ph_hash = hash_phone(phone)
    row = await db.execute(select(User).where(User.phone_hash == ph_hash))
    user = row.scalar_one_or_none()

    if user is None:
        user = User(
            phone_hash=ph_hash,
            phone_country_code="228",
            is_phone_verified=True,
            is_selfie_verified=True,
            is_active=True,
            is_visible=True,
            is_premium=False,
            is_banned=False,
            is_deleted=False,
            city_id=lome.id,
            onboarding_step="completed",
            language="fr",
            account_created_count=1,
        )
        db.add(user)
        await db.flush()

    # Profile — direct query par user_id (pas de user.profile lazy-load)
    profile_row = await db.execute(
        select(Profile).where(Profile.user_id == user.id)
    )
    existing_profile = profile_row.scalar_one_or_none()
    if existing_profile is None:
        prompts = _pick(PROMPT_POOL, 3, offset=idx)
        tags = _pick(TAG_POOL, 5, offset=idx * 2)
        profile = Profile(
            user_id=user.id,
            display_name=spec["name"],
            birth_date=_birthdate_for_age(spec["age"]),
            gender=spec["gender"],
            seeking_gender=spec["seeking"],
            intention=spec["intention"],
            sector=spec["sector"],
            prompts=prompts,
            tags=tags,
            languages=["fr", "en"],
            seeking_age_min=18,
            seeking_age_max=40,
            profile_completeness=0.85,
        )
        db.add(profile)
        await db.flush()

    # Photos (2-3 par user)
    photos_row = await db.execute(
        select(Photo).where(Photo.user_id == user.id, Photo.is_deleted.is_(False))
    )
    existing_photo_count = len(list(photos_row.scalars().all()))
    target_count = 3
    for order in range(existing_photo_count, target_count):
        img_idx = ((idx - 1) * 3 + order) % 70 + 1  # pravatar a ~70 images
        url = _pravatar_url(img_idx)
        db.add(
            Photo(
                user_id=user.id,
                original_url=url,
                thumbnail_url=url,
                medium_url=url,
                blurred_url=url,
                display_order=order,
                is_verified_selfie=(order == 0),
                content_hash=f"seed-{spec['name'].lower()}-{order}",
                width=600,
                height=600,
                file_size_bytes=50_000,
                moderation_status="approved",
                dominant_color="#D85A30",
            )
        )

    # Quartiers : 2 assignments par user, offset déterministe
    q_a = quartiers[idx % len(quartiers)]
    q_b = quartiers[(idx + 1) % len(quartiers)]
    await _ensure_user_quartier(db, user.id, q_a.id, "lives", is_primary=True)
    await _ensure_user_quartier(db, user.id, q_b.id, "hangs")

    # Spots : on partage les 3 premiers spots seed (Café 21, Salle Olympe,
    # Chez Tonton) entre les fake users pour créer des "spots en commun".
    if existing_spots:
        shared = existing_spots[idx % len(existing_spots)]
        await _ensure_user_spot(db, user.id, shared.id)
        if idx % 2 == 0 and len(existing_spots) > 1:
            extra = existing_spots[(idx + 1) % len(existing_spots)]
            await _ensure_user_spot(db, user.id, extra.id)

    await db.flush()
    return user


# ══════════════════════════════════════════════════════════════════════
# 4. Likes / Matches / Messages / Meetup
# ══════════════════════════════════════════════════════════════════════

async def _find_match(
    db: AsyncSession, a_id: uuid.UUID, b_id: uuid.UUID
) -> Match | None:
    """
    Un Match est stocké avec (user_a, user_b) dans le sens du liker
    original. On cherche les deux orientations.
    """
    row = await db.execute(
        select(Match).where(
            ((Match.user_a_id == a_id) & (Match.user_b_id == b_id))
            | ((Match.user_a_id == b_id) & (Match.user_b_id == a_id))
        )
    )
    return row.scalar_one_or_none()


async def create_like_received(
    db: AsyncSession, liker: User, target: User
) -> Match:
    """
    liker a liké target → Match(user_a=liker, user_b=target, status=pending).
    target voit ce like dans /matches/likes-received.
    """
    existing = await _find_match(db, liker.id, target.id)
    if existing is not None:
        return existing
    now = datetime.now(timezone.utc)
    match = Match(
        user_a_id=liker.id,
        user_b_id=target.id,
        status="pending",
        geo_score=0.65,
        lifestyle_score=0.7,
        was_wildcard=False,
        created_at=now - timedelta(hours=6),
        updated_at=now - timedelta(hours=6),
    )
    db.add(match)
    await db.flush()
    return match


async def create_mutual_match(
    db: AsyncSession,
    a: User,
    b: User,
    matched_hours_ago: int = 48,
) -> Match:
    """
    Match réciproque matched (a a liké b en premier puis b a liké back).
    """
    existing = await _find_match(db, a.id, b.id)
    now = datetime.now(timezone.utc)
    matched_at = now - timedelta(hours=matched_hours_ago)
    if existing is not None:
        if existing.status != "matched":
            existing.status = "matched"
            existing.matched_at = matched_at
        return existing
    match = Match(
        user_a_id=a.id,
        user_b_id=b.id,
        status="matched",
        matched_at=matched_at,
        geo_score=0.75,
        lifestyle_score=0.8,
        was_wildcard=False,
        created_at=matched_at,
        updated_at=matched_at,
    )
    db.add(match)
    await db.flush()
    return match


async def _add_message_idempotent(
    db: AsyncSession,
    match: Match,
    sender_id: uuid.UUID,
    message_type: str,
    content: str | None,
    client_message_id: str,
    offset_minutes: int,
    meetup_data: dict | None = None,
    status: str = "delivered",
) -> Message:
    """
    Insère un Message en utilisant client_message_id pour l'idempotence
    (index unique partiel (sender_id, client_message_id) WHERE NOT NULL).
    """
    row = await db.execute(
        select(Message).where(
            Message.sender_id == sender_id,
            Message.client_message_id == client_message_id,
        )
    )
    existing = row.scalar_one_or_none()
    if existing is not None:
        return existing
    now = datetime.now(timezone.utc)
    created_at = now + timedelta(minutes=offset_minutes)
    msg = Message(
        match_id=match.id,
        sender_id=sender_id,
        message_type=message_type,
        content=content,
        meetup_data=meetup_data,
        status=status,
        client_message_id=client_message_id,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(msg)
    await db.flush()
    match.last_message_at = created_at
    return msg


async def seed_match_with_messages(
    db: AsyncSession, target: User, kwame: User
) -> int:
    """
    Match de 2 jours avec 4 messages alternés. Dernier envoyé par Kwame
    (non lu côté target).
    """
    match = await create_mutual_match(db, target, kwame, matched_hours_ago=48)
    msgs = [
        (target.id, "text",
         "Salut! Vu qu'on est tous les deux à Tokoin, tu connais Café 21 ?",
         "seed-match-kwame-1", -120, "read"),
        (kwame.id, "text",
         "Oui, j'y suis souvent le samedi ! Tu as essayé leur poulet braisé ?",
         "seed-match-kwame-2", -90, "read"),
        (target.id, "text",
         "Pas encore. Ça te dit qu'on y aille ?",
         "seed-match-kwame-3", -60, "read"),
        (kwame.id, "text",
         "Demain 18h ça te va ?",
         "seed-match-kwame-4", -30, "delivered"),
    ]
    added = 0
    for sender_id, mtype, content, cmid, off, status in msgs:
        existed_before = (
            await db.execute(
                select(Message).where(
                    Message.sender_id == sender_id,
                    Message.client_message_id == cmid,
                )
            )
        ).scalar_one_or_none()
        if existed_before is None:
            added += 1
        await _add_message_idempotent(
            db, match, sender_id, mtype, content, cmid, off, status=status
        )
    return added


async def seed_match_icebreaker_only(
    db: AsyncSession, target: User, akosua: User
) -> None:
    """
    Match tout frais sans message — l'ice-breaker est généré à la volée
    par icebreaker_service au moment de GET /matches/{id}. Pas de row
    Message persistée au MVP (voir match_service.get_match_detail).
    """
    await create_mutual_match(db, target, akosua, matched_hours_ago=1)


async def seed_meetup_scenario(
    db: AsyncSession, target: User, efua: User, cafe_21: Spot
) -> None:
    """
    Match avec Efua + 2 messages texte + 1 proposition de meetup active
    pour Café 21 demain 18h.
    """
    match = await create_mutual_match(db, target, efua, matched_hours_ago=6)

    await _add_message_idempotent(
        db, match, efua.id, "text",
        "Hey ! Ravie du match. Tu connais bien Tokoin ?",
        "seed-meetup-efua-1", -240, status="read",
    )
    await _add_message_idempotent(
        db, match, target.id, "text",
        "Salut Efua ! Oui j'y vis. Café 21 c'est mon QG.",
        "seed-meetup-efua-2", -200, status="read",
    )

    tomorrow = date.today() + timedelta(days=1)
    meetup_data = {
        "spot_id": str(cafe_21.id),
        "spot_name": cafe_21.name,
        "proposed_date": tomorrow.isoformat(),
        "proposed_time": time(18, 0).isoformat(timespec="minutes"),
        "note": "On se voit là-bas ?",
        "status": "proposed",
        "counter_date": None,
        "counter_time": None,
    }
    await _add_message_idempotent(
        db, match, efua.id, "meetup",
        "On se voit là-bas ?",
        "seed-meetup-efua-3", -60,
        meetup_data=meetup_data,
        status="delivered",
    )


# ══════════════════════════════════════════════════════════════════════
# 5. Orchestration
# ══════════════════════════════════════════════════════════════════════

async def main(phone_e164: str) -> None:
    try:
        normalized = normalize_phone(phone_e164)
    except InvalidPhoneError as e:
        print(f"ERROR: phone invalide: {e}", file=sys.stderr)
        sys.exit(1)

    async with async_session() as db:
        lome = await _get_lome(db)
        quartiers = await _get_quartiers(db, lome.id)

        # 1. Target user
        target = await find_target_user(db, normalized)
        profile = await ensure_target_matchable(db, target, lome, quartiers)
        display_name = profile.display_name

        # 2. Spots (extras + on récupère les 3 spots seed originaux)
        base_spots_row = await db.execute(
            select(Spot).where(
                Spot.city_id == lome.id,
                Spot.name.in_(("Café 21", "Salle Olympe", "Chez Tonton")),
            )
        )
        base_spots = list(base_spots_row.scalars().all())
        extra_spots, n_added = await seed_extra_spots(db, lome)
        spot_names = [s.name for s in extra_spots[:5]]
        cafe_21 = next((s for s in base_spots if s.name == "Café 21"), None)
        if cafe_21 is None:
            raise RuntimeError(
                "Café 21 manquant. Lance `python -m scripts.seed_base_data` d'abord."
            )

        # 3. Fake users
        created_users: dict[str, User] = {}
        for idx, spec in enumerate(FAKE_USERS, start=1):
            user = await seed_fake_user(
                db, spec, idx, lome, quartiers, base_spots
            )
            created_users[spec["name"]] = user

        # 4. Scénarios de test
        # 4a. 3 likes reçus
        for name in ("Ama", "Kofi", "Esi"):
            await create_like_received(db, created_users[name], target)

        # 4b. Match avec Kwame + 4 messages
        n_msgs_added = await seed_match_with_messages(
            db, target, created_users["Kwame"]
        )

        # 4c. Match tout frais avec Akosua (pas de message, ice-breaker à la volée)
        await seed_match_icebreaker_only(db, target, created_users["Akosua"])

        # 4d. Yaa + Mensah : dans le feed (pas de Match row → feed_service
        #     les pickera s'ils matchent les filtres seeking_gender).
        #     Rien à faire de plus : ils sont matchables par construction.

        # 4e. Match Efua + meetup proposé
        await seed_meetup_scenario(db, target, created_users["Efua"], cafe_21)

        await db.commit()

        # Résumé
        print(f"OK: test data seeded for {normalized} (Display name: {display_name})")
        preview = ", ".join(spot_names) + ("..." if len(extra_spots) > 5 else "")
        print(f"   Spots added: {n_added} (of {len(EXTRA_SPOTS)} in catalog: {preview})")
        print(f"   Fake users: {len(FAKE_USERS)} ({', '.join(u['name'] for u in FAKE_USERS)})")
        print(f"   Likes received: 3 (Ama, Kofi, Esi)")
        print(f"   Matches: 2 (Kwame +{n_msgs_added} msgs this run, Akosua ice-breaker)")
        print(f"   Feed profiles: 2 (Yaa, Mensah)")
        print(f"   Active meetup: Efua @ Cafe 21 tomorrow 18h")
        print()
        print("Run again to refresh (skips already-existing items).")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "Usage: python -m scripts.seed_test_data <phone_e164>\n"
            "Example: python -m scripts.seed_test_data +22899000999",
            file=sys.stderr,
        )
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
