from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppException(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        payload: dict = {"detail": exc.detail}
        # Modération : propage les messages user traduits si présents (§18).
        fr = getattr(exc, "user_message_fr", None)
        en = getattr(exc, "user_message_en", None)
        if fr:
            payload["user_message_fr"] = fr
        if en:
            payload["user_message_en"] = en
        # Rate limiter : propage Retry-After + X-RateLimit-* (§15).
        headers = getattr(exc, "headers", None)
        return JSONResponse(
            status_code=exc.status_code,
            content=payload,
            headers=headers,
        )
