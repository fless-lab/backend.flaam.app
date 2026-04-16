from __future__ import annotations

"""
Tests des middlewares transverses (§16, §35.4).

Couvre :
  - RequestIDMiddleware : génération + propagation du X-Request-ID
  - SecurityHeadersMiddleware : headers de sécurité sur toutes les réponses
  - RequestLoggingMiddleware : chaîne complète fonctionnelle (smoke)
"""

import pytest

from app.core.middleware import REQUEST_ID_HEADER

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_request_id_is_generated_when_absent(client):
    """Sans X-Request-ID entrant, le middleware en génère un et le renvoie."""
    resp = await client.get("/countries")
    assert resp.status_code == 200
    rid = resp.headers.get(REQUEST_ID_HEADER)
    assert rid is not None
    assert len(rid) >= 16  # uuid4 hex = 32 chars


async def test_request_id_is_echoed_when_provided(client):
    """Si le client fournit un X-Request-ID, il est conservé dans la réponse."""
    my_rid = "test-req-id-abc123"
    resp = await client.get(
        "/countries", headers={REQUEST_ID_HEADER: my_rid}
    )
    assert resp.status_code == 200
    assert resp.headers[REQUEST_ID_HEADER] == my_rid


async def test_security_headers_present_on_response(client):
    """§35.4 : headers de sécurité obligatoires sur toute réponse."""
    resp = await client.get("/countries")
    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "geolocation=()" in resp.headers["Permissions-Policy"]


async def test_hsts_absent_on_http(client):
    """HSTS n'est set QUE sur HTTPS (le testserver est en http)."""
    resp = await client.get("/countries")
    assert resp.status_code == 200
    assert "Strict-Transport-Security" not in resp.headers


async def test_security_headers_on_error_response(client):
    """Les headers de sécurité sont présents même sur les réponses d'erreur."""
    resp = await client.get("/admin/reports")  # pas de JWT → 401/403
    assert resp.status_code in (401, 403)
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert REQUEST_ID_HEADER in resp.headers


async def test_middleware_chain_does_not_break_normal_request(client, auth_headers):
    """Smoke : la chaîne complète laisse passer une requête authentifiée."""
    resp = await client.get("/profiles/me", headers=auth_headers)
    # 200 (profil existe) ou 404 (pas encore créé) — peu importe, le but
    # est de vérifier que la chaîne n'intercepte pas.
    assert resp.status_code in (200, 404)
    assert REQUEST_ID_HEADER in resp.headers
    assert resp.headers["X-Frame-Options"] == "DENY"
