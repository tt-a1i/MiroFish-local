# Artiffusion-Inc/MiroFish-local

Fork of [tt-a1i/MiroFish-local](https://github.com/tt-a1i/MiroFish-local) -- local selfhosted version of [MiroFish](https://github.com/666ghj/MiroFish).

## Patches vs Upstream MiroFish-local

| Patch | File | Description |
|-------|------|-------------|
| LLM list-to-dict | `backend/app/services/graphiti_patch.py` | Reasoning models return JSON arrays; wrap in ExtractedEntities dict format |
| Reasoning tag strip | `backend/app/services/graphiti_patch.py` | Strip thinking tags + `<reasoning>` from graphiti json.loads |
| LLM client tag strip | `backend/app/utils/llm_client.py` | Strip thinking tags from LLM responses, markdown cleanup |
| Dockerfile + CI | `Containerfile`, `.github/workflows/` | Multi-stage build, GHCR autobuild |

## Architecture

```
ZEP_BACKEND=graphiti:
  LLM (9router/ollama) -> graphiti-core -> Neo4j 5
  graphiti_patch.py intercepts:
    1. bulk_utils (Neo4j attribute sanitization)
    2. node_operations (list-to-dict normalisation)
    3. json.loads (reasoning tag stripping)
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ZEP_BACKEND` | `cloud` | `graphiti` for local Neo4j |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection |
| `NEO4J_USER` | `neo4j` | |
| `NEO4J_PASSWORD` | `password` | |
| `LLM_API_KEY` | -- | OpenAI-compatible API key |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | API base URL |
| `LLM_MODEL_NAME` | `gpt-4o-mini` | Model name |
| `GRAPHITI_EMBEDDING_MODEL` | -- | Embedding model for graphiti |

## Docker Image

GHCR: `ghcr.io/artiffusion-inc/mirofish-local:latest`

Build: `docker build -f Containerfile -t mirofish-local .`

## Known Issues

- graphiti-core ExtractedEntities crashes when LLM returns list -- Patch 2 fixes
- Reasoning models inject thinking tags in content -- Patch 3 fixes
- Neo4j rejects nested dict/list attributes -- Patch 1 fixes (upstream Issue #683)
