from __future__ import annotations

"""
Middlewares transverses (§16, §35).

Ordre conseillé côté main.py (du plus externe au plus interne, donc
à ajouter dans l'ordre INVERSE avec app.add_middleware) :

    RequestID → SecurityHeaders → RequestLogging → CORS → Idempotency

Justification : RequestID doit être le plus externe pour que tous les
autres middlewares (et tous les handlers) aient accès au request_id
dans le contexte structlog.

Headers de sécurité (§35.4) : liste minimale pour une API. On ne set
pas CSP ici (l'API ne rend pas de HTML). HSTS est conditionnel en
production derrière HTTPS.
"""

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = structlog.get_logger()

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Génère ou récupère X-Request-ID, le propage dans le contexte
    structlog et le renvoie dans la réponse. Doit être le plus externe
    pour que tous les autres middlewares y aient accès.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()

        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Ajoute les headers de sécurité sur toutes les réponses (§35.4).
    """

    _HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    }

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for name, value in self._HEADERS.items():
            response.headers.setdefault(name, value)
        # HSTS uniquement sur HTTPS (reverse proxy en prod).
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log structuré de chaque requête : method, path, status, duration_ms.
    Le request_id est déjà bound par RequestIDMiddleware, donc il sort
    automatiquement dans chaque log (structlog contextvars).
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        method = request.method
        path = request.url.path
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - start) * 1000)
            log.exception(
                "http_request_failed",
                method=method,
                path=path,
                duration_ms=duration_ms,
            )
            raise
        duration_ms = int((time.perf_counter() - start) * 1000)
        log.info(
            "http_request",
            method=method,
            path=path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        return response


__all__ = [
    "RequestIDMiddleware",
    "SecurityHeadersMiddleware",
    "RequestLoggingMiddleware",
    "REQUEST_ID_HEADER",
]
