# Recruitee MCP

Serveur MCP qui expose 3 outils Recruitee à un agent (ChatGPT custom connector ou Claude) :

- `get_application(candidate_id)`
- `get_job(offer_id)`
- `update_candidate_note_and_score(candidate_id, score, threshold_status, candidate_note)`

## 1. Pré-requis Recruitee

1. Crée un **Personal API token** : Recruitee > *Settings* > *Apps and plugins* > *Personal API tokens* > **+ New token**.
2. Note ton **company_id** : il apparaît dans l'URL de ton dashboard (ex. `app.recruitee.com/#/dashboard/12345/...` → `12345`).

## 2. Tester en local

```bash
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# remplis RECRUITEE_TOKEN, RECRUITEE_COMPANY_ID, MCP_AUTH_TOKEN

# génère un MCP_AUTH_TOKEN aléatoire
python -c "import secrets; print(secrets.token_urlsafe(48))"

# charge .env puis lance
export $(grep -v '^#' .env | xargs)
python server.py
```

Le serveur écoute sur `http://localhost:8000/sse`. Vérifie qu'il tourne :

```bash
curl http://localhost:8000/healthz
# {"status":"ok"}
```

## 3. Déployer sur Render (gratuit, HTTPS auto)

1. Pousse ce dossier sur un repo GitHub.
2. Sur [render.com](https://render.com) → *New* → *Blueprint* → sélectionne ton repo.
   Render détecte `render.yaml` et configure tout.
3. Dans les *Environment Variables* du service, renseigne :
   - `RECRUITEE_TOKEN`
   - `RECRUITEE_COMPANY_ID`
   - `MCP_AUTH_TOKEN` (Render le génère, **copie-le, tu en auras besoin**)
4. Attends le premier déploiement. L'URL ressemblera à `https://recruitee-mcp-xxxx.onrender.com`.
5. Vérifie : `curl https://recruitee-mcp-xxxx.onrender.com/healthz`.

## 4. Brancher dans le formulaire MCP (ChatGPT)

| Champ | Valeur |
|---|---|
| Nom | `Recruitee` |
| Description | `Lit les candidatures Recruitee et écrit la présélection (score + note)` |
| URL du serveur MCP | `https://recruitee-mcp-xxxx.onrender.com/sse` |
| Authentification | Token d'accès / clé API |
| Schéma d'en-tête | Porteur (Bearer) |
| Token | la valeur de `MCP_AUTH_TOKEN` (pas le token Recruitee) |

## 5. Et pour le déclenchement automatique ?

Le MCP répond aux demandes de l'agent. Il ne se réveille pas tout seul à chaque
nouvelle candidature. Pour ça il faut, en v2, brancher un **webhook Recruitee**
(*Settings* > *Apps and plugins* > *Webhooks*, événement `candidate_created`)
sur un petit endpoint qui démarre l'agent. C'est une étape séparée.
