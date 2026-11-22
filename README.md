# AutoSRE AI — Autonomous AI On-Call Engineer

AutoSRE AI is a production-style hackathon platform that demonstrates a controlled incident lifecycle using real HTTP observations and persisted evidence:

`failure injection → monitoring → deduplicated incident → evidence-based investigation → repair proposal → constrained tests → human approval → recovery verification`

![Status](https://img.shields.io/badge/status-hackathon_MVP-40d9ff) ![Python](https://img.shields.io/badge/Python-3.12+-3776AB) ![React](https://img.shields.io/badge/React-TypeScript-61DAFB)

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

Open the command center at http://localhost:5173 and API documentation at http://localhost:8000/docs. Click **Inject demo failure**; the dashboard runs enough real checks to cross the threshold. Open the incident and progress through Investigate, Propose repair, Run sandbox tests, Approve, and Recover.

## Architecture

- `frontend/`: responsive React/Vite operator console with live API actions and Recharts signals.
- `backend/`: FastAPI, SQLAlchemy/SQLite persistence, continuous health monitoring, deterministic evidence-based agent fallback, policy-gated repairs, audit events, incident reports.
- `demo-app/`: isolated FastAPI service with division-by-zero, missing configuration, database, HTTP 500, and latency faults.
- `sandbox/`: unprivileged test image. The API's MVP policy runner validates only allowlisted patch targets and test specifications; it never executes model-supplied commands.

The demo recovery endpoint only controls the isolated demo application. It cannot deploy to production. All mutating platform endpoints require `X-API-Key`; recovery additionally requires a passing test and recorded human approval.

## Local development

Backend:

```bash
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt
$env:PYTHONPATH="backend"
.venv/Scripts/uvicorn app.main:app --reload --port 8000
```

Demo app and frontend:

```bash
$env:PYTHONPATH="demo-app"
.venv/Scripts/uvicorn demo_service:app --app-dir demo-app --port 9000
cd frontend
npm install
npm run dev
```

Run tests:

```bash
$env:PYTHONPATH="backend;demo-app"
pytest -q backend/tests demo-app/test_app.py
cd frontend
npm run build
```

## Configuration

See `.env.example`. Required for normal local use: `AUTOSRE_API_KEY`. SQLite and the deterministic investigation provider work without external credentials. Setting `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` activates an OpenAI-compatible structured investigation request; failures safely fall back to evidence-based heuristics. `GITHUB_*` settings reserve least-privilege repair-repository integration; no token is committed or passed to a sandbox.

## Security boundaries

- Service URLs and inputs are validated with Pydantic.
- Agent evidence is treated as untrusted data, not authorization.
- The repair runner accepts no arbitrary shell command.
- Recovery is impossible until a test passes and a named operator approves.
- Demo containers drop Linux capabilities and disable privilege escalation.
- Persistent audit records distinguish proposed, tested, approved, deployed, and resolved states.

## API highlights

Use Swagger at `/docs` for the complete contract. Important routes include `/api/services`, `/api/services/{id}/check`, `/api/incidents`, `/investigate`, `/repairs`, `/test`, `/approval`, `/recover`, `/report`, and `/api/dashboard`.

## Known MVP boundaries

The safe demo repair uses a pre-approved recovery procedure rather than applying arbitrary generated source. The provider settings and repair-repository settings are present, but GitHub branch/commit/PR mutation is intentionally not activated without a separately authorized repair repository and token. Sandbox validation is deterministic and constrained; Docker supplies the isolation boundary for project tests and can be extended to compile generated patches.
