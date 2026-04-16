from __future__ import annotations

"""Routes Contacts masqués (§5.12). 3 endpoints."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.models.user import User
from app.schemas.contacts import (
    BlacklistDeleteResponse,
    BlacklistImportBody,
    BlacklistImportResponse,
    BlacklistListResponse,
)
from app.services import contacts_service

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.post(
    "/blacklist",
    response_model=BlacklistImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_blacklist(
    body: BlacklistImportBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await contacts_service.import_blacklist(
        user=user, phone_hashes=body.phone_hashes, db=db
    )


@router.get("/blacklist", response_model=BlacklistListResponse)
async def list_blacklist(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    hashes = await contacts_service.list_blacklist(user=user, db=db)
    return {"phone_hashes": hashes, "count": len(hashes)}


@router.delete(
    "/blacklist/{phone_hash}",
    response_model=BlacklistDeleteResponse,
)
async def delete_blacklist_entry(
    phone_hash: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    removed = await contacts_service.remove_from_blacklist(
        user=user, phone_hash=phone_hash.lower(), db=db
    )
    return {"status": "deleted" if removed else "not_found"}


__all__ = ["router"]
