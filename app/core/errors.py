from __future__ import annotations

"""
FlaamError — exception metier avec message i18n.

Coexiste avec AppException (§backward-compat) :
- Les nouveaux appels utilisent FlaamError(code, status_code, lang, **kwargs).
- Les anciens AppException restent en place — pas de migration forcee.
- Seules les cles presentes dans MESSAGES sont migrees en FlaamError.

Le handler est enregistre dans main.py via register_exception_handlers().
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.i18n import t


class FlaamError(Exception):
    """
    Exception metier Flaam avec message i18n.

    Usage :
        raise FlaamError("daily_likes_exhausted", 429, lang, limit=5)

    Response JSON :
        {"error": "daily_likes_exhausted", "message": "Tu as utilise..."}

    Le client mobile peut mapper le champ ``error`` pour afficher une UI
    localisee cote client, et utiliser ``message`` en fallback direct.
    """

    def __init__(
        self,
        code: str,
        status_code: int = 400,
        lang: str = "fr",
        **kwargs,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.message = t(code, lang, **kwargs)
        self.kwargs = kwargs
        super().__init__(self.message)


def register_flaam_error_handler(app: FastAPI) -> None:
    """Monte le handler FlaamError sur l'app FastAPI."""

    @app.exception_handler(FlaamError)
    async def _flaam_error_handler(
        request: Request, exc: FlaamError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": exc.message},
        )


__all__ = ["FlaamError", "register_flaam_error_handler"]
