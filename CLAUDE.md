# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Structure

`frontend/` is a Vue 3 + Vite frontend. Main code under `src/views/`, `src/components/`, `src/api/`, `src/router/`.
`backend/` is a Flask service. Core modules under `app/api/`, `app/services/`, `app/models/`, `app/utils/`. Entry point: `backend/run.py`.
Example data and scripts: `examples/`, `demo.py`, `backend/scripts/`. Static assets: `docs/`, `static/`.
Env templates: `.env.example` and `.env.local.example`.

## Architecture Overview

MiroFish is a multi-agent social simulation engine. The 5-step pipeline: **Seed Input → Graph Building (GraphRAG) → Env Setup (Agent Profile Generation) → Parallel Simulation (OASIS) → Report Generation → Interactive Q&A**.

The core architectural decision of this fork is the **Zep backend abstraction**: the memory/knowledge-graph layer supports both Zep Cloud (original) and local Graphiti + Neo4j (new), switchable via `ZEP_BACKEND` env var.

### Zep Backend Adapter Pattern

`ZepClientAdapter` (`backend/app/services/zep_adapter.py`) defines the abstract interface. Two implementations:

- `ZepCloudClient` (`zep_cloud_impl.py`) — wraps `zep-cloud` SDK
- `GraphitiClient` (`zep_graphiti_impl.py`) — local graphiti-core + Neo4j

`ZepFactory` (`zep_factory.py`) handles instantiation based on config. A thread-safe singleton (`get_zep_client()`) uses double-checked locking for connection reuse. Use `create_zep_client()` when an independent instance is needed.

### Critical: neo4j Version Conflict

`graphiti-core>=0.25.0` requires neo4j driver 5.26+, while `camel-oasis==0.2.5` pins neo4j 5.23.0. These **cannot coexist in the same venv**. The project declares this conflict in `pyproject.toml` (`tool.uv.conflicts`). The OASIS simulation runner spawns a **separate subprocess** with its own venv (`.venv-simulation`) to work around this. See `backend/app/services/simulation_runner.py` and `simulation_ipc.py`.

### Env Var Mapping

`Config` (`backend/app/config.py`) auto-maps `LLM_API_KEY`/`LLM_BASE_URL` → `OPENAI_API_KEY`/`OPENAI_BASE_URL` when the latter aren't explicitly set, because graphiti-core reads `OPENAI_*` env vars.

### Flask App Structure

Factory pattern in `backend/app/__init__.py` (`create_app()`). Three blueprints:

- `/api/graph` — graph building, entity reading, search
- `/api/simulation` — OASIS simulation control
- `/api/report` — report generation and interactive chat

Routes are thin; business logic lives in `app/services/`.

### Frontend

Vue 3 + Vue Router (hash-free history mode). The 5-step wizard is `MainView.vue` with step components (`Step1GraphBuild`, `Step2EnvSetup`, `Step3Simulation`, `Step4Report`, `Step5Interaction`). API calls go through thin modules in `src/api/`.

## Build, Test, and Development Commands

Run from repo root:

```bash
npm run setup:all       # Install root, frontend, and backend deps
npm run dev             # Start both frontend (:3000) and backend (:5001) concurrently
npm run frontend        # Start Vite dev server only
npm run backend         # Start Flask backend via `uv run python run.py`
npm run build           # Build frontend for production
python demo.py          # Load example seed, quick smoke test of main pipeline

# Docker — Neo4j for graphiti local mode
docker-compose -f docker-compose.local.yml up -d   # Start Neo4j 5.26
docker-compose -f docker-compose.local.yml down     # Stop Neo4j
docker-compose -f docker-compose.local.yml ps       # Check service health

# Tests (backend only for now)
cd backend && uv run pytest backend/scripts/test_profile_format.py
```

## Coding Style & Naming Conventions

- **Frontend**: 2-space indent, ES module, Vue SFC. Component files PascalCase (`GraphPanel.vue`). API modules lowercase (`api/report.js`).
- **Backend**: PEP 8, 4-space indent, snake_case. Business logic in `app/services/`, routes only handle request orchestration.
- **Language**: Comments, docs, and user-facing text in Chinese, consistent with existing code.
- No unified lint script exists; at minimum ensure `npm run build` passes before committing.

## Testing Guidelines

Backend test framework is `pytest` + `pytest-asyncio`. New backend tests go in `backend/tests/`, named `test_*.py`. Frontend has no automated tests yet; for UI changes include manual verification steps and screenshots in PRs, covering at minimum upload, graph building, and simulation entry.

## Commit & Pull Request Guidelines

Follow Conventional Commits (history already uses `feat:`, `fix(backend):`, `docs:`). Recommended format: `type(scope): summary`. PRs must include: change purpose, affected scope, verification commands, env prerequisites. Include screenshots for UI changes. If config or local deployment flow changes, update `README.md`, `LOCAL-STARTUP.md`, or example env files accordingly.

## Important Constraints

- **Python 3.11 required** — camel-oasis enforces 3.10–3.11
- **Never commit `.env`** or real API keys
- **Dual venv setup** for graphiti mode: main `.venv` (graphiti + neo4j 6.x) and `.venv-simulation` (camel-oasis + neo4j 5.23)
- Graphiti search uses **cross-encoder reranking** by default, but falls back to RRF when the LLM API doesn't support `logprobs` (e.g., DashScope). See `graphiti_patch.py` for the auto-detection logic.
- Simulation runs in a **subprocess** (not thread) due to the neo4j driver conflict — IPC is managed via JSONL files in `simulation_ipc.py`.
- Before switching to graphiti mode, confirm `ZEP_BACKEND=graphiti` and Neo4j config in `docker-compose.local.yml` are ready. For cloud mode, verify `LLM_API_KEY` and `ZEP_API_KEY`.
