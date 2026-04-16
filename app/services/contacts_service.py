from __future__ import annotations

"""
Contacts service (spec §5.12).

Gère la blacklist client-side : l'utilisateur importe une liste de
phone_hash SHA-256 (le hashing est fait COTE CLIENT — le serveur ne
voit jamais les numéros en clair).

Les hashes blacklistés sont exclus du matching en L1 (hard filters,
déjà câblé en Session 5 dans matching_engine/hard_filters.py).
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact_blacklist import ContactBlacklist
from app.models.user import User


async def import_blacklist(
    *, user: User, phone_hashes: list[str], db: AsyncSession
) -> dict:
    """
    Upsert batch. Retourne {imported, skipped, total}.

    Implémentation : ON CONFLICT DO NOTHING sur (user_id, phone_hash)
    (index unique déjà créé dans contact_blacklist.py).
    """
    if not phone_hashes:
        return {"imported": 0, "skipped": 0, "total": 0}

    # On lit d'abord les hashes déjà présents pour pouvoir compter
    # imported vs skipped. ON CONFLICT ne retourne pas le nombre de
    # skipped en pur SQL portable.
    existing_row = await db.execute(
        select(ContactBlacklist.phone_hash).where(
            ContactBlacklist.user_id == user.id,
            ContactBlacklist.phone_hash.in_(phone_hashes),
        )
    )
    existing = {r[0] for r in existing_row.all()}

    to_insert = [
        {"user_id": user.id, "phone_hash": h}
        for h in phone_hashes
        if h not in existing
    ]
    if to_insert:
        stmt = pg_insert(ContactBlacklist).values(to_insert)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["user_id", "phone_hash"]
        )
        await db.execute(stmt)
        await db.commit()

    return {
        "imported": len(to_insert),
        "skipped": len(existing),
        "total": len(phone_hashes),
    }


async def list_blacklist(*, user: User, db: AsyncSession) -> list[str]:
    rows = await db.execute(
        select(ContactBlacklist.phone_hash).where(
            ContactBlacklist.user_id == user.id
        )
    )
    return [r[0] for r in rows.all()]


async def remove_from_blacklist(
    *, user: User, phone_hash: str, db: AsyncSession
) -> bool:
    result = await db.execute(
        select(ContactBlacklist).where(
            ContactBlacklist.user_id == user.id,
            ContactBlacklist.phone_hash == phone_hash,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        return False
    await db.delete(entry)
    await db.commit()
    return True


__all__ = [
    "import_blacklist",
    "list_blacklist",
    "remove_from_blacklist",
]
