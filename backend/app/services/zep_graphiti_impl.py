"""
Graphiti local client implementation

Uses graphiti-core + Neo4j to implement a local knowledge graph service.
Replaces Zep Cloud, implements the ZepClientAdapter interface.

MVP scope:
- Graph creation/deletion (isolated by group_id)
- Episode addition (single/batch)
- Node/edge retrieval
- Semantic search

Ontology is a no-op in the MVP phase.
"""

import asyncio
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from functools import lru_cache

from .zep_adapter import (
    ZepClientAdapter,
    GraphNode,
    GraphEdge,
    SearchResult,
    EpisodeStatus,
)

logger = logging.getLogger('mirofish.graphiti_client')


# ============================================================================
# Single background thread + dedicated event loop (Plan A)
# ============================================================================
# All Graphiti/Neo4j async operations run in this dedicated thread's event loop
# Flask threads submit tasks via run_coroutine_threadsafe and wait for results
# ============================================================================

_async_loop: Optional[asyncio.AbstractEventLoop] = None
_async_thread: Optional[threading.Thread] = None
_init_lock = threading.Lock()


def _start_async_loop():
    """Start the event loop in a background thread"""
    global _async_loop
    _async_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_async_loop)
    logger.info("Graphiti dedicated event loop started")
    _async_loop.run_forever()


def _ensure_async_loop():
    """Ensure the background event loop is started"""
    global _async_thread
    if _async_thread is None or not _async_thread.is_alive():
        with _init_lock:
            if _async_thread is None or not _async_thread.is_alive():
                _async_thread = threading.Thread(
                    target=_start_async_loop,
                    daemon=True,
                    name="graphiti-async-loop"
                )
                _async_thread.start()
                # Wait for the loop to start
                while _async_loop is None:
                    import time
                    time.sleep(0.01)


def _run_async(coro):
    """
    Run an async coroutine in a synchronous context

    Uses the dedicated background thread's event loop, submitting tasks via run_coroutine_threadsafe.
    This ensures the Neo4j driver stays bound to the same loop, avoiding cross-loop issues.
    """
    _ensure_async_loop()
    future = asyncio.run_coroutine_threadsafe(coro, _async_loop)
    return future.result(timeout=300)  # 5 minute timeout


class DashScopeEmbedderWrapper:
    """
    DashScope-compatible Embedder wrapper

    DashScope API has a batch size limit (max 10), and graphiti-core's OpenAIEmbedder
    sends all input at once. This wrapper chunks requests accordingly.

    Note: This class dynamically inherits from EmbedderClient to satisfy Pydantic type checks.
    """

    def __init__(self, embedder: Any, max_batch_size: int = 10):
        self._embedder = embedder
        self.max_batch_size = max_batch_size
        # Copy original embedder attributes for compatibility
        if hasattr(embedder, 'config'):
            self.config = embedder.config

    async def create(self, input_data) -> list[float]:
        """Single embedding request (pass-through)"""
        return await self._embedder.create(input_data)

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        """Batch embedding request (chunked processing)"""
        if len(input_data_list) <= self.max_batch_size:
            return await self._embedder.create_batch(input_data_list)

        # Chunked processing
        results = []
        for i in range(0, len(input_data_list), self.max_batch_size):
            chunk = input_data_list[i : i + self.max_batch_size]
            chunk_results = await self._embedder.create_batch(chunk)
            results.extend(chunk_results)
        return results


def _create_dashscope_embedder_wrapper(base_embedder: Any, max_batch_size: int = 10) -> Any:
    """
    Create a DashScope-compatible Embedder wrapper

    Dynamically inherits from EmbedderClient to satisfy graphiti-core's Pydantic type checks.
    """
    try:
        from graphiti_core.embedder.client import EmbedderClient

        class _DashScopeEmbedderClient(EmbedderClient):
            """Dynamically generated EmbedderClient subclass"""

            def __init__(self, embedder: Any, batch_size: int):
                self._embedder = embedder
                self.max_batch_size = batch_size
                if hasattr(embedder, 'config'):
                    self.config = embedder.config

            async def create(self, input_data) -> list[float]:
                return await self._embedder.create(input_data)

            async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
                if len(input_data_list) <= self.max_batch_size:
                    return await self._embedder.create_batch(input_data_list)

                results = []
                for i in range(0, len(input_data_list), self.max_batch_size):
                    chunk = input_data_list[i : i + self.max_batch_size]
                    chunk_results = await self._embedder.create_batch(chunk)
                    results.extend(chunk_results)
                return results

        return _DashScopeEmbedderClient(base_embedder, max_batch_size)

    except ImportError:
        # fallback: return plain wrapper
        return DashScopeEmbedderWrapper(base_embedder, max_batch_size)


class GraphitiClient(ZepClientAdapter):
    """
    Graphiti local client implementation

    Uses the graphiti-core library to connect to the Neo4j graph database.
    Multi-graph isolation is achieved via the group_id parameter (maps to MiroFish's graph_id).
    """

    def __init__(
        self,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        llm_client: Optional[Any] = None,
        embedder: Optional[Any] = None,
    ):
        """
        Initialize the Graphiti client

        Args:
            neo4j_uri: Neo4j Bolt connection URI (e.g. bolt://localhost:7687)
            neo4j_user: Neo4j username
            neo4j_password: Neo4j password
            llm_client: Optional LLM client (for entity extraction)
            embedder: Optional Embedder (for semantic search)
        """
        self.neo4j_uri = neo4j_uri
        self.neo4j_user = neo4j_user
        self.neo4j_password = neo4j_password
        self._llm_client = llm_client
        self._embedder = embedder

        # Lazy-initialize Graphiti instance
        self._graphiti = None
        self._driver = None
        self._initialized = False

        # Track created graph_ids (for group_id mapping)
        self._graph_metadata: Dict[str, Dict[str, Any]] = {}

        # Store ontology definitions (MVP phase: record only, not enforced)
        self._ontology_cache: Dict[str, Dict[str, Any]] = {}

    def _ensure_initialized(self):
        """Ensure Graphiti is initialized"""
        if self._initialized:
            return

        try:
            from graphiti_core import Graphiti

            # Apply Neo4j property sanitization patch (Issue #683 workaround)
            from .graphiti_patch import apply_patch
            apply_patch()

            llm_client = self._llm_client
            if llm_client is None:
                llm_client = self._build_default_llm_client()

            embedder = self._embedder
            if embedder is None:
                embedder = self._build_default_embedder()

            # Create Graphiti instance
            self._graphiti = Graphiti(
                self.neo4j_uri,
                self.neo4j_user,
                self.neo4j_password,
                llm_client=llm_client,
                embedder=embedder,
            )

            # Initialize indices and constraints
            _run_async(self._graphiti.build_indices_and_constraints())

            # Get the underlying Neo4j driver for direct queries
            self._driver = self._graphiti.driver

            self._initialized = True
            logger.info("Graphiti client initialized successfully")

        except ImportError as e:
            raise ImportError(
                "graphiti-core is not installed. Run: pip install graphiti-core"
            ) from e
        except Exception as e:
            logger.error(f"Graphiti initialization failed: {e}")
            raise

    def _build_default_llm_client(self) -> Any:
        """
        Build Graphiti default LLM client (OpenAI-compatible)

        Graphiti defaults to `gpt-4.1-mini`, which is usually unsuitable for
        OpenAI-compatible services like DashScope; this method prefers:
        - GRAPHITI_LLM_MODEL (if set)
        - Otherwise LLM_MODEL_NAME (consistent with MiroFish existing config)
        """
        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

        api_key = os.environ.get('OPENAI_API_KEY')
        base_url = os.environ.get('OPENAI_BASE_URL')
        model = os.environ.get('GRAPHITI_LLM_MODEL') or os.environ.get('LLM_MODEL_NAME')
        small_model = os.environ.get('GRAPHITI_LLM_SMALL_MODEL') or None

        temperature = float(os.environ.get('GRAPHITI_LLM_TEMPERATURE', '0') or '0')
        max_tokens = int(os.environ.get('GRAPHITI_LLM_MAX_TOKENS', '8192') or '8192')

        config = LLMConfig(
            api_key=api_key,
            base_url=base_url,
            model=model,
            small_model=small_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return OpenAIGenericClient(config=config)

    def _build_default_embedder(self) -> Any:
        """
        Build Graphiti default Embedder (OpenAI-compatible /embeddings)

        Default embedding model is `text-embedding-3-small` (OpenAI); under DashScope, explicit config is needed:
        - GRAPHITI_EMBEDDING_MODEL=text-embedding-v4

        Note: DashScope API has a batch size limit (max 10), handled by DashScopeEmbedderWrapper.
        """
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

        api_key = os.environ.get('OPENAI_API_KEY')
        base_url = os.environ.get('OPENAI_BASE_URL')
        embedding_model = os.environ.get('GRAPHITI_EMBEDDING_MODEL')

        if embedding_model:
            config = OpenAIEmbedderConfig(
                api_key=api_key,
                base_url=base_url,
                embedding_model=embedding_model,
            )
        else:
            config = OpenAIEmbedderConfig(
                api_key=api_key,
                base_url=base_url,
            )

        base_embedder = OpenAIEmbedder(config=config)

        # DashScope API has batch size limits, need wrapping
        if self._is_openai_compatible_only():
            logger.info("Detected non-standard OpenAI API, enabling DashScope Embedder chunked processing")
            return _create_dashscope_embedder_wrapper(base_embedder, max_batch_size=10)

        return base_embedder

    # ==================== Graph Operations ====================

    def create_graph(self, graph_id: str, name: str, description: str) -> None:
        """
        Create a graph (isolated via group_id in Graphiti)

        Graphiti has no explicit graph creation API; data is automatically isolated by group_id.
        This only records metadata; actual data is created when add_episode is called.
        """
        self._graph_metadata[graph_id] = {
            "name": name,
            "description": description,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(f"Graph metadata recorded: graph_id={graph_id}, name={name}")

    def delete_graph(self, graph_id: str) -> None:
        """
        Delete a graph (remove all data associated with the group_id)

        Uses Cypher to directly delete all nodes and edges matching the group_id in Neo4j.
        All Graphiti nodes (Entity, Episodic, etc.) carry a group_id property,
        so a single generic query covers everything.
        """
        self._ensure_initialized()

        async def _delete():
            # Delete all nodes with this group_id (cascade deletes edges)
            # Graphiti's Entity and Episodic nodes both carry group_id, no need to delete separately
            result = await self._driver.execute_query(
                """
                MATCH (n {group_id: $group_id})
                DETACH DELETE n
                RETURN count(n) as deleted_count
                """,
                group_id=graph_id,
            )
            records = result.records if hasattr(result, 'records') else result[0]
            deleted = records[0]['deleted_count'] if records else 0
            logger.debug(f"Deleted {deleted} nodes (group_id={graph_id})")

        _run_async(_delete())

        # Clear local caches
        self._graph_metadata.pop(graph_id, None)
        self._ontology_cache.pop(graph_id, None)
        logger.info(f"Graph deleted: graph_id={graph_id}")

    def set_ontology(
        self,
        graph_ids: List[str],
        entities: Optional[Dict[str, Any]] = None,
        edges: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Set graph ontology

        MVP note: Graphiti does not support the same ontology API as Zep Cloud.
        Definitions are cached here and can be used for:
        1. Prompt hints when adding episodes
        2. Type mapping during future alignment

        Full parity phase can implement:
        - Dynamic Pydantic Entity/Edge model generation passed to add_episode
        - Type constraint creation in Neo4j
        """
        for graph_id in graph_ids:
            self._ontology_cache[graph_id] = {
                "entities": entities or {},
                "edges": edges or {},
            }
            logger.info(
                f"Ontology cached (MVP no-op): graph_id={graph_id}, "
                f"entity_types={len(entities or {})}, edge_types={len(edges or {})}"
            )

    # ==================== Episode Operations ====================

    def add_episode(self, graph_id: str, data: str, episode_type: str = "text") -> str:
        """Add a single episode"""
        self._ensure_initialized()

        from graphiti_core.nodes import EpisodeType

        # Map episode_type
        source_type = EpisodeType.text
        if episode_type == "message":
            source_type = EpisodeType.message
        elif episode_type == "json":
            source_type = EpisodeType.json

        async def _add():
            result = await self._graphiti.add_episode(
                name=f"episode_{graph_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                episode_body=data,
                source=source_type,
                source_description="mirofish_simulation",
                reference_time=datetime.now(timezone.utc),
                group_id=graph_id,
            )
            return result.episode.uuid if result and result.episode else ""

        return _run_async(_add())

    def add_episode_batch(
        self,
        graph_id: str,
        episodes: List[Dict[str, Any]]
    ) -> List[str]:
        """Add episodes in batch"""
        self._ensure_initialized()

        from graphiti_core.nodes import EpisodeType
        from graphiti_core.utils.bulk_utils import RawEpisode

        # Build RawEpisode list
        raw_episodes = []
        for i, ep in enumerate(episodes):
            ep_type = ep.get("type", "text")
            source_type = EpisodeType.text
            if ep_type == "message":
                source_type = EpisodeType.message
            elif ep_type == "json":
                source_type = EpisodeType.json

            raw_episodes.append(
                RawEpisode(
                    name=f"episode_{graph_id}_{i}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    content=ep.get("data", ""),
                    source=source_type,
                    source_description="mirofish_simulation",
                    reference_time=datetime.now(timezone.utc),
                )
            )

        async def _add_bulk():
            result = await self._graphiti.add_episode_bulk(
                bulk_episodes=raw_episodes,
                group_id=graph_id,
            )
            # Return all episode UUIDs
            return [ep.uuid for ep in result.episodes] if result and result.episodes else []

        return _run_async(_add_bulk())

    def get_episode_status(self, episode_uuid: str) -> EpisodeStatus:
        """
        Get episode processing status

        Graphiti processes episodes synchronously; once added, they are considered processed.
        """
        return EpisodeStatus(uuid=episode_uuid, processed=True)

    def wait_for_episode(self, episode_uuid: str, timeout: int = 300) -> bool:
        """
        Wait for episode processing to complete

        Graphiti processes synchronously, so always returns True immediately.
        """
        return True

    # ==================== Node Operations ====================

    def get_all_nodes(self, graph_id: str) -> List[GraphNode]:
        """Get all nodes in the graph"""
        self._ensure_initialized()

        async def _get_nodes():
            # Try multiple label patterns for schema compatibility
            # Graphiti standard uses :Entity, but other labels may exist
            for label in ["Entity", "EntityNode"]:
                records, _, _ = await self._driver.execute_query(
                    f"""
                    MATCH (n:{label} {{group_id: $group_id}})
                    RETURN
                        n.uuid AS uuid,
                        n.name AS name,
                        labels(n) AS labels,
                        n.summary AS summary,
                        properties(n) AS props,
                        n.created_at AS created_at
                    """,
                    group_id=graph_id,
                )
                if records:
                    return records

            # No labels found, log warning and return empty
            logger.warning(
                f"get_all_nodes: no nodes found for group_id={graph_id}. "
                f"Possible causes: 1) graph is empty 2) Graphiti schema mismatch (tried Entity, EntityNode)"
            )
            return []

        records = _run_async(_get_nodes())
        nodes = []
        for record in records:
            props = record.get("props", {})
            # Filter out already-extracted properties
            attributes = {
                k: v for k, v in props.items()
                if k not in ["uuid", "name", "summary", "created_at", "group_id"]
            }
            created_at = record.get("created_at")
            if hasattr(created_at, 'to_native'):
                created_at = created_at.to_native().isoformat()
            elif created_at:
                created_at = str(created_at)

            nodes.append(GraphNode(
                uuid=record.get("uuid", ""),
                name=record.get("name", ""),
                labels=record.get("labels", ["Entity"]),
                summary=record.get("summary", ""),
                attributes=attributes,
                created_at=created_at,
            ))
        return nodes

    def get_node(self, node_uuid: str) -> Optional[GraphNode]:
        """Get a single node"""
        self._ensure_initialized()

        async def _get_node():
            # Find node by uuid without restricting label (more flexible)
            records, _, _ = await self._driver.execute_query(
                """
                MATCH (n {uuid: $uuid})
                RETURN
                    n.uuid AS uuid,
                    n.name AS name,
                    labels(n) AS labels,
                    n.summary AS summary,
                    properties(n) AS props,
                    n.created_at AS created_at
                LIMIT 1
                """,
                uuid=node_uuid,
            )
            return records

        records = _run_async(_get_node())
        if not records:
            logger.debug(f"get_node: node not found for uuid={node_uuid}")
            return None

        record = records[0]
        props = record.get("props", {})
        attributes = {
            k: v for k, v in props.items()
            if k not in ["uuid", "name", "summary", "created_at", "group_id"]
        }
        created_at = record.get("created_at")
        if hasattr(created_at, 'to_native'):
            created_at = created_at.to_native().isoformat()
        elif created_at:
            created_at = str(created_at)

        return GraphNode(
            uuid=record.get("uuid", ""),
            name=record.get("name", ""),
            labels=record.get("labels", ["Entity"]),
            summary=record.get("summary", ""),
            attributes=attributes,
            created_at=created_at,
        )

    def get_node_edges(self, node_uuid: str) -> List[GraphEdge]:
        """Get all edges related to a node (bidirectional)"""
        self._ensure_initialized()

        async def _get_edges():
            # Match by uuid without restricting node label, get bidirectional edges
            # Prefer r.name (actual relation name), fall back to type(r) (relation type)
            records, _, _ = await self._driver.execute_query(
                """
                MATCH (n {uuid: $uuid})-[r]-(m)
                RETURN DISTINCT
                    r.uuid AS uuid,
                    COALESCE(r.name, type(r)) AS name,
                    r.fact AS fact,
                    startNode(r).uuid AS source_uuid,
                    endNode(r).uuid AS target_uuid,
                    properties(r) AS props,
                    r.created_at AS created_at,
                    r.valid_at AS valid_at,
                    r.invalid_at AS invalid_at,
                    r.expired_at AS expired_at
                """,
                uuid=node_uuid,
            )
            return records

        records = _run_async(_get_edges())
        if not records:
            logger.debug(f"get_node_edges: no edges associated with node uuid={node_uuid}")
        return [self._record_to_edge(record) for record in records]

    # ==================== Edge Operations ====================

    def get_all_edges(self, graph_id: str) -> List[GraphEdge]:
        """Get all edges in the graph (filtered by node group_id)"""
        self._ensure_initialized()

        async def _get_edges():
            # Filter edges by node group_id, use DISTINCT to avoid duplicates
            # Note: edges themselves may not have group_id, so filter through connected nodes
            # Prefer r.name (actual relation name), fall back to type(r) (relation type)
            for label in ["Entity", "EntityNode"]:
                records, _, _ = await self._driver.execute_query(
                    f"""
                    MATCH (n:{label} {{group_id: $group_id}})-[r]-(m:{label})
                    WHERE n.group_id = m.group_id
                    RETURN DISTINCT
                        r.uuid AS uuid,
                        COALESCE(r.name, type(r)) AS name,
                        r.fact AS fact,
                        startNode(r).uuid AS source_uuid,
                        endNode(r).uuid AS target_uuid,
                        properties(r) AS props,
                        r.created_at AS created_at,
                        r.valid_at AS valid_at,
                        r.invalid_at AS invalid_at,
                        r.expired_at AS expired_at
                    """,
                    group_id=graph_id,
                )
                if records:
                    return records

            logger.warning(
                f"get_all_edges: no edges found for group_id={graph_id}. "
                f"Possible causes: 1) graph has no edges 2) Graphiti schema mismatch"
            )
            return []

        records = _run_async(_get_edges())
        return [self._record_to_edge(record) for record in records]

    # ==================== Search Operations ====================

    def _is_openai_compatible_only(self) -> bool:
        """
        Detect whether a non-standard OpenAI API is in use (e.g. DashScope, Azure)

        These APIs may not support the logprobs feature required by cross_encoder,
        requiring a fallback to RRF re-ranking.

        Set GRAPHITI_FORCE_CROSS_ENCODER=true to force cross_encoder usage
        (for compatible services confirmed to support logprobs).
        """
        import os

        # Explicit override: force cross_encoder
        if os.environ.get('GRAPHITI_FORCE_CROSS_ENCODER', '').lower() in ('true', '1', 'yes'):
            return False

        base_url = os.environ.get('OPENAI_BASE_URL', '')
        # Standard OpenAI API
        if not base_url or 'api.openai.com' in base_url:
            return False
        # Non-standard API (DashScope, Azure, local deployments, etc.)
        non_standard_indicators = [
            'dashscope', 'aliyun', 'azure', 'localhost',
            'ollama', 'vllm', 'lmstudio', 'openrouter'
        ]
        return any(indicator in base_url.lower() for indicator in non_standard_indicators)

    def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
        reranker: str = "rrf"  # Default changed to rrf for safety
    ) -> SearchResult:
        """
        Graph hybrid search

        Uses Graphiti's public search_() API (with config) for searching.
        Falls back to the simple search() API if search_() is unavailable.

        Note: reranker="cross_encoder" requires OpenAI API logprobs support;
        non-standard APIs (e.g. DashScope) are automatically downgraded to rrf.
        """
        self._ensure_initialized()

        # Non-standard OpenAI API does not support cross_encoder, force rrf
        if reranker == "cross_encoder" and self._is_openai_compatible_only():
            logger.info("Detected non-standard OpenAI API, cross_encoder downgraded to rrf")
            reranker = "rrf"

        from graphiti_core.search.search_config_recipes import (
            NODE_HYBRID_SEARCH_RRF,
            EDGE_HYBRID_SEARCH_RRF,
            COMBINED_HYBRID_SEARCH_CROSS_ENCODER,
        )

        async def _do_search():
            nodes = []
            edges = []

            # Check if search_() method exists (public advanced search API)
            has_search_method = hasattr(self._graphiti, 'search_')

            if not has_search_method:
                # Fallback: use simple search() API
                logger.info("Using graphiti.search() simple API (search_() unavailable)")
                try:
                    results = await self._graphiti.search(
                        query=query,
                        group_ids=[graph_id],
                        num_results=limit,
                    )
                    # Simple search primarily returns edges
                    if results:
                        edges = list(results) if not isinstance(results, list) else results
                    return nodes, edges
                except Exception as e:
                    logger.warning(f"graphiti.search() failed: {e}, returning empty results")
                    return [], []

            # Use search_() advanced API
            try:
                if scope == "nodes":
                    config = NODE_HYBRID_SEARCH_RRF.model_copy(deep=True)
                    config.limit = limit
                    result = await self._graphiti.search_(
                        query=query,
                        config=config,
                        group_ids=[graph_id],
                    )
                    if result and hasattr(result, 'nodes'):
                        nodes = result.nodes or []

                elif scope == "edges":
                    config = EDGE_HYBRID_SEARCH_RRF.model_copy(deep=True)
                    config.limit = limit
                    result = await self._graphiti.search_(
                        query=query,
                        config=config,
                        group_ids=[graph_id],
                    )
                    if result and hasattr(result, 'edges'):
                        edges = result.edges or []

                else:  # both
                    if reranker == "cross_encoder":
                        config = COMBINED_HYBRID_SEARCH_CROSS_ENCODER.model_copy(deep=True)
                        config.limit = limit
                        result = await self._graphiti.search_(
                            query=query,
                            config=config,
                            group_ids=[graph_id],
                        )
                        if result:
                            nodes = result.nodes or [] if hasattr(result, 'nodes') else []
                            edges = result.edges or [] if hasattr(result, 'edges') else []
                    else:
                        # Search nodes and edges separately
                        node_config = NODE_HYBRID_SEARCH_RRF.model_copy(deep=True)
                        node_config.limit = limit // 2
                        edge_config = EDGE_HYBRID_SEARCH_RRF.model_copy(deep=True)
                        edge_config.limit = limit // 2

                        node_result = await self._graphiti.search_(
                            query=query, config=node_config, group_ids=[graph_id]
                        )
                        edge_result = await self._graphiti.search_(
                            query=query, config=edge_config, group_ids=[graph_id]
                        )

                        if node_result and hasattr(node_result, 'nodes'):
                            nodes = node_result.nodes or []
                        if edge_result and hasattr(edge_result, 'edges'):
                            edges = edge_result.edges or []

            except Exception as e:
                logger.warning(f"graphiti.search_() failed: {e}, attempting fallback")
                # Fallback to simple search
                try:
                    results = await self._graphiti.search(
                        query=query,
                        group_ids=[graph_id],
                        num_results=limit,
                    )
                    if results:
                        edges = list(results) if not isinstance(results, list) else results
                except Exception as fallback_e:
                    logger.error(f"search fallback also failed: {fallback_e}")

            return nodes, edges

        raw_nodes, raw_edges = _run_async(_do_search())

        if not raw_nodes and not raw_edges:
            logger.debug(f"search: query='{query}' group_id={graph_id} no results")

        # Convert to adapter data structures
        nodes = [self._graphiti_node_to_graph_node(n) for n in raw_nodes]
        edges = [self._graphiti_edge_to_graph_edge(e) for e in raw_edges]

        return SearchResult(nodes=nodes, edges=edges)

    # ==================== Conversion Helpers ====================

    def _record_to_edge(self, record: Dict[str, Any]) -> GraphEdge:
        """Convert Neo4j query result to GraphEdge"""
        props = record.get("props", {})
        attributes = {
            k: v for k, v in props.items()
            if k not in ["uuid", "fact", "created_at", "valid_at", "invalid_at", "expired_at", "group_id"]
        }

        def _format_time(t):
            if t is None:
                return None
            if hasattr(t, 'to_native'):
                return t.to_native().isoformat()
            return str(t)

        return GraphEdge(
            uuid=record.get("uuid", ""),
            name=record.get("name", ""),
            fact=record.get("fact", ""),
            source_node_uuid=record.get("source_uuid", ""),
            target_node_uuid=record.get("target_uuid", ""),
            attributes=attributes,
            created_at=_format_time(record.get("created_at")),
            valid_at=_format_time(record.get("valid_at")),
            invalid_at=_format_time(record.get("invalid_at")),
            expired_at=_format_time(record.get("expired_at")),
            episodes=[],  # Graphiti edges may not have an episodes field
            fact_type=record.get("name", ""),
        )

    def _graphiti_node_to_graph_node(self, node: Any) -> GraphNode:
        """Convert a Graphiti node object to GraphNode"""
        created_at = getattr(node, 'created_at', None)
        if hasattr(created_at, 'isoformat'):
            created_at = created_at.isoformat()
        elif created_at:
            created_at = str(created_at)

        return GraphNode(
            uuid=getattr(node, 'uuid', ''),
            name=getattr(node, 'name', ''),
            labels=getattr(node, 'labels', ['Entity']),
            summary=getattr(node, 'summary', ''),
            attributes=getattr(node, 'attributes', {}),
            created_at=created_at,
        )

    def _graphiti_edge_to_graph_edge(self, edge: Any) -> GraphEdge:
        """Convert a Graphiti edge object to GraphEdge"""
        def _format_time(t):
            if t is None:
                return None
            if hasattr(t, 'isoformat'):
                return t.isoformat()
            return str(t)

        return GraphEdge(
            uuid=getattr(edge, 'uuid', ''),
            name=getattr(edge, 'name', '') or getattr(edge, 'fact_type', ''),
            fact=getattr(edge, 'fact', ''),
            source_node_uuid=getattr(edge, 'source_node_uuid', ''),
            target_node_uuid=getattr(edge, 'target_node_uuid', ''),
            attributes=getattr(edge, 'attributes', {}),
            created_at=_format_time(getattr(edge, 'created_at', None)),
            valid_at=_format_time(getattr(edge, 'valid_at', None)),
            invalid_at=_format_time(getattr(edge, 'invalid_at', None)),
            expired_at=_format_time(getattr(edge, 'expired_at', None)),
            episodes=getattr(edge, 'episodes', []),
            fact_type=getattr(edge, 'fact_type', '') or getattr(edge, 'name', ''),
        )

    def close(self):
        """Close the connection"""
        if self._graphiti:
            _run_async(self._graphiti.close())
            self._initialized = False
            logger.info("Graphiti connection closed")

    def __del__(self):
        """Close connection on destruction"""
        try:
            self.close()
        except Exception:
            pass
