from __future__ import annotations

"""
Middleware idempotency (spec §34).

Vérifie X-Idempotency-Key sur POST/PATCH/DELETE. Si la clé a déjà été
traitée, retourne la réponse originale depuis Redis (TTL 24h).

Clé Redis :
    idempotency:{method}:{path}:{user_or_ip}:{key}

Ne cache PAS :
- 204 No Content (pas de body)
- Erreurs 4xx/5xx
- Requêtes sans X-Idempotency-Key
- Méthodes non mutatives (GET, HEAD, OPTIONS)

Coexistence : les endpoints qui ont leur propre dédup (feed/like,
feed/skip, messages/send) continuent de fonctionner. Le middleware
cache la 1ère réponse et la rejoue au retry — pas de double effet
DB car le service a déjà sa propre dédup.
"""

import json

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.db.redis import redis_pool


MUTATIVE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
HEADER_NAME = "X-Idempotency-Key"
KEY_TEMPLATE = "idempotency:{method}:{path}:{owner}:{key}"
TTL_SECONDS = 24 * 3600


def _owner_from_request(request: Request) -> str:
    """
    L'owner est le user_id quand le JWT est présent, sinon l'IP.

    On ne décode PAS le JWT ici : on utilise simplement le token comme
    partie de la clé. Si un attaquant vole un token, il n'aura pas plus
    de pouvoir qu'avec le token lui-même.
    """
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return "tok:" + auth.split(" ", 1)[1].strip()[:32]
    client = request.client
    return f"ip:{client.host if client else 'unknown'}"


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in MUTATIVE_METHODS:
            return await call_next(request)

        key = request.headers.get(HEADER_NAME)
        if not key:
            return await call_next(request)

        try:
            redis = redis_pool.client
        except RuntimeError:
            # Redis non initialisé (tests unitaires sans fixtures) :
            # on bypasse proprement.
            return await call_next(request)

        owner = _owner_from_request(request)
        redis_key = KEY_TEMPLATE.format(
            method=request.method,
            path=request.url.path,
            owner=owner,
            key=key,
        )

        cached = await redis.get(redis_key)
        if cached:
            try:
                payload = json.loads(cached)
                return Response(
                    content=payload["body"],
                    status_code=payload["status"],
                    media_type=payload.get("media_type", "application/json"),
                    headers={"X-Idempotent-Replay": "true"},
                )
            except (json.JSONDecodeError, KeyError, TypeError):
                # Cache corrompu : on purge et on continue.
                await redis.delete(redis_key)

        response = await call_next(request)

        # Cache uniquement les succès 2xx avec body.
        if not (200 <= response.status_code < 300):
            return response
        if response.status_code == 204:
            return response

        # StreamingResponse : on consomme le body et on reconstruit.
        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        media_type = response.media_type or "application/json"
        try:
            await redis.set(
                redis_key,
                json.dumps(
                    {
                        "status": response.status_code,
                        "body": body.decode("utf-8"),
                        "media_type": media_type,
                    }
                ),
                ex=TTL_SECONDS,
            )
        except Exception:
            # On ne bloque pas la réponse si Redis fail.
            pass

        return Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=media_type,
        )


__all__ = ["IdempotencyMiddleware"]
