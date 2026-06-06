# MiroFish-local (Artiffusion Fork)

Fork of [tt-a1i/MiroFish-local](https://github.com/tt-a1i/MiroFish-local) — selfhosted MiroFish with Graphiti+Neo4j replacing Zep Cloud. AGPL-3.0.

## Directory Structure

```
backend/
  app/
    __init__.py          # Flask app factory (create_app), registers blueprints
    config.py            # Config class (env vars: LLM_*, NEO4J_*, ZEP_*)
    api/                 # Flask blueprints
      graph.py           #   /api/graph/* — ontology gen, graph build, entity CRUD
      simulation.py      #   /api/simulation/* — OASIS social media simulation
      report.py          #   /api/report/* — ReACT report generation
    services/            # Business logic
      graphiti_patch.py  #   ★ Monkey-patches for graphiti-core (3 patches)
      zep_adapter.py     #   Abstract ZepClientAdapter interface
      zep_factory.py     #   Factory: ZEP_BACKEND → cloud | graphiti
      zep_cloud_impl.py  #   Zep Cloud adapter (~220 lines)
      zep_graphiti_impl.py # ★ Graphiti+Neo4j adapter (~900 lines, main local impl)
      graph_builder.py   #   Orchestrates graph build via zep_adapter
      ontology_generator.py # LLM-driven entity/relation type generation
      report_agent.py    #   ReACT report agent with tool calls (~2500 lines)
      simulation_runner.py # Process-based OASIS simulation runner
      simulation_manager.py # Simulation state management
      simulation_config_generator.py # LLM-driven sim config
      simulation_ipc.py #   IPC client/server for simulation processes
      oasis_profile_generator.py # LLM-driven agent profile generation
      zep_tools.py       #   Zep search/retrieval tools for report agent
      zep_entity_reader.py # Entity/node reading from Zep
      zep_graph_memory_updater.py # Graph memory updates
      text_processor.py  # Text chunking + preprocessing
    models/              # SQLAlchemy-style dataclasses (Project, Task)
    utils/
      llm_client.py      # ★ OpenAI-compatible client with reasoning tag strip
      file_parser.py     # PDF/MD/TXT file parser
      logger.py          # Logging setup
      retry.py           # Retry utility
  pyproject.toml         # Dependencies (extras: graphiti, oasis — MUTUALLY EXCLUSIVE)
  uv.lock
  run.py                 # Entry point (Flask dev server on :5001)
frontend/                # Vue 3 + Vite (port 3000, proxies /api → :5001)
  src/                   # Components, views, API client, store
  vite.config.js         # Proxy + locale aliases
Containerfile            # Multi-stage build (graphiti venv + isolated oasis venv)
.containerignore
docker-compose.local.yml # Neo4j-only (for local dev without full infra)
```

## Architecture

```
Frontend (Vue 3, Vite :3000) → /api/* → Flask Backend (:5001)
                                        │
                                        ├─ ZEP_BACKEND=cloud → Zep Cloud API
                                        └─ ZEP_BACKEND=graphiti → Neo4j 5 + graphiti-core
                                                                   │
                                        graphiti_patch.py ────────┤
                                          Patch 1: Neo4j attr sanitize (Issue #683)
                                          Patch 2: LLM list→dict (ExtractedEntities)
                                          Patch 3: Reasoning tag strip (json.loads)
```

**graphiti-core + Neo4j flow:**
1. `ontology_generator.py` → LLM generates entity/relation types
2. `graph_builder.py` → `zep_factory.create_zep_client()` → `GraphitiClient`
3. `GraphitiClient.build_graph()` → graphiti-core extracts entities → Neo4j
4. **graphiti_patch.py** intercepts graphiti-core to fix LLM output issues
5. `report_agent.py` → reads graph → generates report via ReACT + tools

## Patches vs Upstream MiroFish-local

| Patch | File | What | Why |
|-------|------|------|-----|
| LLM list→dict | `graphiti_patch.py` | Intercept `node_operations._extract_nodes_*`, wrap list responses in `{"extracted_entities": [...]}` | Reasoning models (deepseek-v4, glm-5, qwen3) return JSON arrays; `ExtractedEntities(**result)` requires dict |
| Reasoning tag strip | `graphiti_patch.py` | Patch `json.loads` in graphiti_core LLM client module | `content` may contain thinking tags before JSON; causes JSONDecodeError |
| LLM client cleanup | `llm_client.py` | Strip thinking tags from `chat()`, markdown cleanup in `chat_json()` | Same issue at application level |
| Containerfile | `Containerfile` | Multi-stage with `--extra graphiti` + isolated oasis venv | graphiti/oasis have neo4j version conflict |

## Dependencies (uv extras)

**graphiti** and **oasis** are **mutually exclusive** (neo4j version conflict):

| Extra | Install | neo4j | Notes |
|-------|---------|-------|-------|
| `graphiti` | `uv sync --extra graphiti` | >=5.26.0 | Main backend, knowledge graph |
| `oasis` | `uv sync --extra oasis` | ==5.23.0 | Social media simulation only |

Containerfile installs both in **separate venvs**: `.venv` (graphiti) + `.venv-simulation` (oasis).

## Environment Variables

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `ZEP_BACKEND` | `cloud` | yes | `graphiti` for local Neo4j |
| `LLM_API_KEY` | — | yes | OpenAI-compatible API key |
| `LLM_BASE_URL` | — | yes | API base URL (e.g. 9router) |
| `LLM_MODEL_NAME` | `gpt-4o-mini` | yes | Model for ontology/graph/report |
| `NEO4J_URI` | `bolt://localhost:7687` | graphiti | Neo4j Bolt URI |
| `NEO4J_USER` | `neo4j` | graphiti | |
| `NEO4J_PASSWORD` | — | graphiti | |
| `GRAPHITI_EMBEDDING_MODEL` | — | graphiti | Embedding model (e.g. `jina/jina-embeddings-v3`) |
| `GRAPHITI_LLM_MODEL` | — | graphiti | Override model for graphiti-core (defaults to LLM_MODEL_NAME) |
| `ZEP_API_KEY` | — | cloud only | Zep Cloud API key |

graphiti-core reads `OPENAI_API_KEY`/`OPENAI_BASE_URL` — config.py auto-maps from `LLM_*` if unset.

## Docker Image

```
ghcr.io/artiffusion-inc/mirofish-local:latest
```

Build: `docker build -f Containerfile -t mirofish-local .`

Ports: 3000 (Vite frontend), 5001 (Flask backend)

## Key API Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/graph/project/list` | GET | List projects |
| `/api/graph/ontology/generate` | POST | Generate entity/relation types from text |
| `/api/graph/build` | POST | Build knowledge graph from text + ontology |
| `/api/graph/entity/list` | GET | List graph entities |
| `/api/graph/entity/search` | GET | Search entities |
| `/api/simulation/start` | POST | Start OASIS simulation |
| `/api/simulation/status` | GET | Simulation status |
| `/api/report/generate` | POST | Generate report from graph |

## Known Issues

- **graphiti-core `ExtractedEntities` crash**: LLM returns list → Patch 2 wraps in dict
- **Reasoning tags in content**: thinking/reasoning tags before JSON → Patch 3 strips
- **Neo4j nested attributes**: LLM generates nested dicts → Patch 1 serializes to JSON strings (Issue #683)
- **neo4j version conflict**: graphiti needs >=5.26, oasis needs ==5.23 → separate venvs in Containerfile