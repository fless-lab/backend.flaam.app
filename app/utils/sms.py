from __future__ import annotations

"""
SMS / WhatsApp delivery — spec §35.

MVP : Termii UNIQUEMENT. Interface abstraite pour ajouter
Twilio/Vonage plus tard sans refactor.

Stratégie OTP :
- SMS en PRIMARY (dual SIM Togo safe, ne dépend pas de WhatsApp)
- WhatsApp en fallback si l'utilisateur demande "renvoyer par WhatsApp"
  (côté client, après 30 s sans réception SMS)

Stratégie messages événements :
- WhatsApp primary (plus riche, 4× moins cher)
- SMS fallback

Tarifs Termii (avril 2026) :
- SMS : $0.0250 / msg (~15 FCFA)
- WhatsApp : $0.0060 / msg (~3.6 FCFA)
- Voice : $0.0102 / min
- Email : $0.0010 / msg
- Plan Starter : $0/mois, sandbox inclus pour le dev

Mode dev (SMS_SIMULATE=true) : on logue le code OTP sans appeler l'API.
Mode prod (SMS_SIMULATE=false) : appelle Termii. Si TERMII_SANDBOX=true,
pointe vers l'environnement sandbox Termii.
"""

from abc import ABC, abstractmethod
from typing import Literal

import httpx
import structlog

from app.core.config import get_settings

log = structlog.get_logger()
settings = get_settings()

Channel = Literal["sms", "whatsapp"]


class SMSDeliveryError(Exception):
    """Échec d'envoi SMS/WhatsApp."""


class BaseSMSProvider(ABC):
    """Interface commune tous providers SMS/WhatsApp."""

    name: str

    @abstractmethod
    async def send_otp(self, phone: str, code: str, channel: Channel) -> dict:
        """Envoie un code OTP. Retourne {'message_id': str, 'provider': str}."""

    @abstractmethod
    async def send_text(self, phone: str, text: str, channel: Channel) -> dict:
        """Envoie un message libre (rappel event, notif). Même contrat de retour."""


class TermiiProvider(BaseSMSProvider):
    """
    Client Termii (https://api.ng.termii.com).

    En sandbox (plan Starter), les vrais appels API sont acceptés mais
    aucun SMS n'est réellement envoyé. Idéal pour CI et dev.
    """

    name = "termii"

    def __init__(self) -> None:
        self.api_key = settings.termii_api_key
        self.sender_id = settings.termii_sender_id
        self.base_url = settings.termii_base_url.rstrip("/")
        self.sandbox = settings.termii_sandbox

    async def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={**payload, "api_key": self.api_key})
        if resp.status_code >= 400:
            raise SMSDeliveryError(
                f"Termii {path} failed: {resp.status_code} {resp.text}"
            )
        return resp.json()

    async def send_otp(self, phone: str, code: str, channel: Channel) -> dict:
        # Termii expose /api/sms/send pour SMS, /api/sms/send pour WhatsApp
        # via le paramètre "channel": "dnd" (sms) ou "whatsapp".
        termii_channel = "whatsapp" if channel == "whatsapp" else "generic"
        text = f"Ton code Flaam : {code}. Valable 10 minutes."
        endpoint = "/api/sms/send"
        payload = {
            "to": phone,
            "from": self.sender_id,
            "sms": text,
            "type": "plain",
            "channel": termii_channel,
        }
        if self.sandbox:
            payload["sandbox"] = True
        result = await self._post(endpoint, payload)
        return {"message_id": result.get("message_id", ""), "provider": self.name}

    async def send_text(self, phone: str, text: str, channel: Channel) -> dict:
        termii_channel = "whatsapp" if channel == "whatsapp" else "generic"
        payload = {
            "to": phone,
            "from": self.sender_id,
            "sms": text,
            "type": "plain",
            "channel": termii_channel,
        }
        if self.sandbox:
            payload["sandbox"] = True
        result = await self._post("/api/sms/send", payload)
        return {"message_id": result.get("message_id", ""), "provider": self.name}


class SimulatedProvider(BaseSMSProvider):
    """Dev only : ne fait qu'émettre un log structuré avec le code OTP."""

    name = "simulated"

    async def send_otp(self, phone: str, code: str, channel: Channel) -> dict:
        log.info("sms_simulated_otp", phone=phone, code=code, channel=channel)
        return {"message_id": "simulated", "provider": self.name}

    async def send_text(self, phone: str, text: str, channel: Channel) -> dict:
        log.info("sms_simulated_text", phone=phone, text=text, channel=channel)
        return {"message_id": "simulated", "provider": self.name}


def _default_provider() -> BaseSMSProvider:
    if settings.sms_simulate:
        return SimulatedProvider()
    return TermiiProvider()


class SMSDeliveryService:
    """
    Façade orchestrant l'envoi. Route les OTP sur SMS par défaut
    (primary), WhatsApp sur demande explicite (fallback client).

    L'ajout de futurs providers se fait en étendant `self.providers`
    et en ajustant la stratégie ici — aucune route ne doit changer.
    """

    def __init__(self, provider: BaseSMSProvider | None = None) -> None:
        self.provider = provider or _default_provider()

    async def send_otp(self, phone: str, code: str, channel: Channel = "sms") -> dict:
        return await self.provider.send_otp(phone, code, channel)

    async def send_text(
        self, phone: str, text: str, channel: Channel = "whatsapp"
    ) -> dict:
        return await self.provider.send_text(phone, text, channel)


sms_service = SMSDeliveryService()
