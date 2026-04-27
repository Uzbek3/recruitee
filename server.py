"""
Serveur MCP Recruitee.

Expose 3 outils à l'agent (ChatGPT/Claude) :
  - get_application(candidate_id)        -> infos candidat (nom, email, CV, offres liées, étape)
  - get_job(offer_id)                    -> fiche de poste (titre, description, exigences)
  - update_candidate_note_and_score(...) -> écrit le score + la note dans Recruitee

Authentification :
  - Le serveur exige un Bearer token (MCP_AUTH_TOKEN) sur chaque requête entrante.
  - Le serveur appelle Recruitee avec ton token personnel (RECRUITEE_TOKEN).

Variables d'environnement requises :
  - RECRUITEE_TOKEN       : ton Personal API token Recruitee
  - RECRUITEE_COMPANY_ID  : ton company_id Recruitee (entier)
  - MCP_AUTH_TOKEN        : un long secret aléatoire que ChatGPT enverra au MCP
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("recruitee-mcp")


# --- Configuration ---------------------------------------------------------

def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Variable d'environnement manquante : {name}")
    return v


RECRUITEE_TOKEN = _require_env("RECRUITEE_TOKEN")
RECRUITEE_COMPANY_ID = _require_env("RECRUITEE_COMPANY_ID")
MCP_AUTH_TOKEN = _require_env("MCP_AUTH_TOKEN")

BASE_URL = f"https://api.recruitee.com/c/{RECRUITEE_COMPANY_ID}"
RECRUITEE_HEADERS = {
    "Authorization": f"Bearer {RECRUITEE_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


# --- Helpers ---------------------------------------------------------------

def _extract_cv_url(candidate: dict[str, Any]) -> str | None:
    """Cherche l'URL du CV dans la réponse candidat Recruitee."""
    # Champ 'cv' direct
    cv = candidate.get("cv")
    if isinstance(cv, dict) and cv.get("url"):
        return cv["url"]
    if isinstance(cv, list):
        for f in cv:
            if isinstance(f, dict) and f.get("url"):
                return f["url"]
    # Fallback : pièces jointes typées CV
    for f in candidate.get("attachments", []) or []:
        if not isinstance(f, dict):
            continue
        kind = (f.get("kind") or "").lower()
        source = (f.get("source") or "").lower()
        if kind == "document" and source == "candidate":
            return f.get("file_url") or f.get("url")
    return None


# --- MCP server + outils ---------------------------------------------------

mcp = FastMCP("recruitee")


@mcp.tool()
async def get_application(candidate_id: int) -> dict[str, Any]:
    """
    Récupère les informations d'un candidat Recruitee.

    Args:
        candidate_id: ID Recruitee du candidat.

    Returns:
        Dictionnaire avec id, name, emails, phones, cv_url, offer_ids, stage.
    """
    log.info("get_application(candidate_id=%s)", candidate_id)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{BASE_URL}/candidates/{candidate_id}", headers=RECRUITEE_HEADERS)
        r.raise_for_status()
        data = r.json()

    candidate = data.get("candidate", data)
    placements = candidate.get("placements") or []
    first_stage = (placements[0].get("stage") if placements else {}) or {}

    return {
        "id": candidate.get("id"),
        "name": candidate.get("name"),
        "emails": candidate.get("emails", []),
        "phones": candidate.get("phones", []),
        "cv_url": _extract_cv_url(candidate),
        "offer_ids": [p.get("offer_id") for p in placements if p.get("offer_id")],
        "stage": first_stage.get("name"),
        "source": candidate.get("source"),
        "created_at": candidate.get("created_at"),
    }


@mcp.tool()
async def get_job(offer_id: int) -> dict[str, Any]:
    """
    Récupère la fiche d'une offre d'emploi Recruitee.

    Args:
        offer_id: ID Recruitee de l'offre.

    Returns:
        Dictionnaire avec id, title, description, requirements, status, department.
    """
    log.info("get_job(offer_id=%s)", offer_id)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{BASE_URL}/offers/{offer_id}", headers=RECRUITEE_HEADERS)
        r.raise_for_status()
        data = r.json()

    offer = data.get("offer", data)
    return {
        "id": offer.get("id"),
        "title": offer.get("title"),
        "description": offer.get("description"),
        "requirements": offer.get("requirements"),
        "status": offer.get("status"),
        "department": offer.get("department"),
        "kind": offer.get("kind"),
    }


@mcp.tool()
async def update_candidate_note_and_score(
    candidate_id: int,
    score: int,
    threshold_status: str,
    candidate_note: str,
) -> dict[str, Any]:
    """
    Ajoute une note structurée à la fiche candidat Recruitee.

    Args:
        candidate_id: ID Recruitee du candidat.
        score: score de présélection sur 100 (ex. 72).
        threshold_status: "above_50" ou "below_50".
        candidate_note: commentaire d'analyse à conserver dans la fiche.

    Returns:
        {"ok": True, "note_id": ...} en cas de succès.
    """
    log.info("update_candidate_note_and_score(candidate_id=%s, score=%s)", candidate_id, score)
    body_text = (
        f"Score : {score}%\n"
        f"Seuil 50% : {threshold_status}\n\n"
        f"{candidate_note}"
    )
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE_URL}/candidates/{candidate_id}/notes",
            headers=RECRUITEE_HEADERS,
            json={"note": {"body": body_text}},
        )
        r.raise_for_status()
        data = r.json()

    return {"ok": True, "note_id": (data.get("note") or {}).get("id")}


# --- Bearer auth (entre ChatGPT/Claude et notre serveur MCP) ---------------

class BearerAuthMiddleware:
    """
    Middleware ASGI pur (compatible SSE / streaming) qui exige
    `Authorization: Bearer <MCP_AUTH_TOKEN>` sur toutes les requêtes
    sauf /healthz.
    """

    def __init__(self, app, expected_token: str):
        self.app = app
        self._expected = expected_token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("path") == "/healthz":
            await self.app(scope, receive, send)
            return

        token: str | None = None
        for key, value in scope.get("headers", []):
            if key == b"authorization":
                v = value.decode("latin-1")
                if v.startswith("Bearer "):
                    token = v.removeprefix("Bearer ").strip()
                break

        if token != self._expected:
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


# --- Point d'entrée ASGI ---------------------------------------------------

# FastMCP fournit une app Starlette qui sert /sse + le canal de messages MCP.
app = mcp.sse_app()
app.add_middleware(BearerAuthMiddleware, expected_token=MCP_AUTH_TOKEN)


# Route santé pour Render (et pour vérifier que le serveur tourne)
from starlette.routing import Route  # noqa: E402

async def healthz(_request):
    return JSONResponse({"status": "ok"})


app.router.routes.append(Route("/healthz", healthz, methods=["GET"]))


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    log.info("Démarrage du serveur MCP Recruitee sur 0.0.0.0:%s", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
