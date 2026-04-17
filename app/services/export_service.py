from __future__ import annotations

"""
Export RGPD — Article 20 portabilite (§17, S13).

Genere un ZIP contenant toutes les donnees personnelles de l'utilisateur :
- profile.json (profil complet)
- account.json (date inscription, derniere connexion, etc.)
- messages.json (messages envoyes ET recus)
- matches.json (historique)
- behavior.json (logs comportementaux)
- photos/ (fichiers reels copiés si disponibles sur disque)
"""

import json
import os
import shutil
import zipfile
from pathlib import Path
from uuid import UUID

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.behavior_log import BehaviorLog
from app.models.match import Match
from app.models.message import Message
from app.models.photo import Photo
from app.models.user import User
from app.services.photo_service import get_photo_disk_path

log = structlog.get_logger()


def _write_json(directory: str, filename: str, data) -> None:
    path = os.path.join(directory, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


async def generate_user_export(user_id: UUID, db: AsyncSession) -> str:
    """
    Genere un ZIP avec toutes les donnees de l'utilisateur.
    Retourne le chemin du fichier ZIP.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise ValueError(f"User {user_id} not found")

    export_dir = f"/tmp/flaam_export_{user_id}"
    os.makedirs(export_dir, exist_ok=True)

    try:
        # 1. Profile
        profile_data: dict = {}
        if user.profile:
            profile_data = {
                "display_name": user.profile.display_name,
                "gender": user.profile.gender,
                "birth_date": str(user.profile.birth_date) if user.profile.birth_date else None,
                "intention": user.profile.intention,
                "sector": getattr(user.profile, "sector", None),
                "tags": user.profile.tags or [],
                "prompts": user.profile.prompts or [],
                "languages": user.profile.languages or [],
                "quartiers": [
                    {
                        "name": uq.quartier.name if hasattr(uq, "quartier") and uq.quartier else str(uq.quartier_id),
                        "type": uq.relation_type,
                    }
                    for uq in (user.user_quartiers or [])
                ],
                "spots": [
                    {
                        "name": us.spot.name if hasattr(us, "spot") and us.spot else str(us.spot_id),
                        "checkins": us.checkin_count,
                    }
                    for us in (user.user_spots or [])
                ],
            }
        _write_json(export_dir, "profile.json", profile_data)

        # 2. Account info
        account_data = {
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_active": user.last_active_at.isoformat() if user.last_active_at else None,
            "is_premium": user.is_premium,
            "city": user.city.name if user.city else None,
            "language": user.language,
        }
        _write_json(export_dir, "account.json", account_data)

        # 3. Photos
        photo_dir = os.path.join(export_dir, "photos")
        os.makedirs(photo_dir, exist_ok=True)
        photos_result = await db.execute(
            select(Photo).where(
                Photo.user_id == user_id,
                Photo.is_deleted.is_(False),
            )
        )
        photos = list(photos_result.scalars().all())
        for photo in photos:
            disk_path = get_photo_disk_path(photo)
            if os.path.exists(disk_path):
                shutil.copy2(disk_path, photo_dir)

        # 4. Messages
        messages_result = await db.execute(
            select(Message).where(
                Message.sender_id == user_id
            ).union_all(
                select(Message).join(
                    Match, Match.id == Message.match_id
                ).where(
                    or_(
                        Match.user_a_id == user_id,
                        Match.user_b_id == user_id,
                    ),
                    Message.sender_id != user_id,
                )
            ).order_by(Message.created_at)
        )
        messages = list(messages_result.scalars().all())
        messages_data = [
            {
                "match_id": str(m.match_id),
                "sent_by_me": m.sender_id == user_id,
                "content": m.content,
                "type": m.message_type,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ]
        _write_json(export_dir, "messages.json", messages_data)

        # 5. Matches
        matches_result = await db.execute(
            select(Match).where(
                or_(Match.user_a_id == user_id, Match.user_b_id == user_id)
            ).order_by(Match.created_at)
        )
        matches = list(matches_result.scalars().all())
        matches_data = [
            {
                "match_id": str(m.id),
                "status": m.status,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "partner_id": str(
                    m.user_b_id if m.user_a_id == user_id else m.user_a_id
                ),
            }
            for m in matches
        ]
        _write_json(export_dir, "matches.json", matches_data)

        # 6. Behavior logs
        logs_result = await db.execute(
            select(BehaviorLog).where(
                BehaviorLog.user_id == user_id
            ).order_by(BehaviorLog.created_at)
        )
        logs = list(logs_result.scalars().all())
        logs_data = [
            {
                "event_type": bl.event_type,
                "created_at": bl.created_at.isoformat() if bl.created_at else None,
                "data": bl.extra_data,
            }
            for bl in logs
        ]
        _write_json(export_dir, "behavior.json", logs_data)

        # ZIP tout
        zip_path = f"/tmp/flaam_export_{user_id}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(export_dir):
                for file in files:
                    filepath = os.path.join(root, file)
                    arcname = os.path.relpath(filepath, export_dir)
                    zf.write(filepath, arcname)

        log.info(
            "export_generated",
            user_id=str(user_id),
            zip_path=zip_path,
        )
        return zip_path

    finally:
        # Cleanup du repertoire temporaire (le ZIP reste)
        if os.path.isdir(export_dir):
            shutil.rmtree(export_dir, ignore_errors=True)


__all__ = ["generate_user_export"]
