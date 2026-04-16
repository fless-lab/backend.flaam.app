from __future__ import annotations

"""
Tests i18n (§21 Session 11).

Valide le module ``app.core.i18n`` :
- ``t(key, lang, **kwargs)`` : traduction, fallback langue, fallback cle.
- ``detect_lang(request)`` : lecture du header Accept-Language.
"""

from types import SimpleNamespace

import pytest

from app.core.i18n import MESSAGES, detect_lang, t


def test_t_returns_fr_by_default():
    msg = t("otp_invalid")
    assert msg == MESSAGES["otp_invalid"]["fr"]


def test_t_returns_en_when_requested():
    msg = t("otp_invalid", "en")
    assert msg == MESSAGES["otp_invalid"]["en"]


def test_t_formats_kwargs():
    msg = t("otp_rate_limited", "fr", retry_after=30)
    assert "30" in msg


def test_t_falls_back_to_fr_on_unknown_lang():
    msg = t("otp_invalid", "de")
    assert msg == MESSAGES["otp_invalid"]["fr"]


def test_t_returns_key_on_unknown_key():
    assert t("nonexistent_key_xyz") == "nonexistent_key_xyz"


def test_t_returns_raw_template_on_missing_kwarg():
    # Si on oublie un kwarg, on retourne le template brut (pas d'exception)
    msg = t("otp_rate_limited", "fr")
    assert "{retry_after}" in msg


def test_detect_lang_defaults_to_fr():
    req = SimpleNamespace(headers={})
    assert detect_lang(req) == "fr"


def test_detect_lang_detects_en():
    req = SimpleNamespace(headers={"accept-language": "en-US,en;q=0.9"})
    assert detect_lang(req) == "en"


def test_detect_lang_detects_fr_explicit():
    req = SimpleNamespace(headers={"accept-language": "fr-FR,fr;q=0.9"})
    assert detect_lang(req) == "fr"


def test_detect_lang_handles_none_header():
    req = SimpleNamespace(headers=None)
    assert detect_lang(req) == "fr"


def test_all_keys_have_fr_and_en():
    """Toute cle doit avoir au minimum fr + en (coherence)."""
    for key, entry in MESSAGES.items():
        assert "fr" in entry, f"{key} missing fr"
        assert "en" in entry, f"{key} missing en"
