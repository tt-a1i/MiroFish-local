"""
Graphiti-core Monkey Patches

Patch 1: Neo4j property sanitization (graphiti-core Issue #683)
LLM-generated nested attributes cause Neo4j write failures
(Neo4j property values only accept primitive types or arrays thereof)

Patch 2: LLM response normalisation
Reasoning models (deepseek-v4, glm-5, qwen3, kimi-k2) through proxy/ollama
sometimes return a JSON array instead of {"extracted_entities": [...]}.
This patch intercepts node_operations to wrap list responses in the expected dict format.

Patch 3: Reasoning tags stripping
Models returning <think> tags or reasoning_content via API need stripping
before JSON parsing to avoid JSONDecodeError.
"""

import json
import functools
import re
from typing import Any, Dict

from ..utils.logger import get_logger

logger = get_logger('mirofish.graphiti_patch')

_patch_applied = False
_node_ops_patch_applied = False

# Regex to strip <think>...</think> and <reasoning>...</reasoning> blocks
_THINK_PATTERN = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_REASONING_PATTERN = re.compile(r"<reasoning>.*?</reasoning>\s*", re.DOTALL)


def sanitize_for_neo4j(value: Any, path: str = "") -> Any:
    """Recursively sanitize values for Neo4j property constraints.

    Neo4j only accepts: str, int, float, bool, None, and arrays of primitives.
    Nested dicts/lists are serialized to JSON strings.
    """
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as e:
            logger.warning(f"Cannot serialize dict attr {path}: {e}")
            return str(value)
    if isinstance(value, (list, tuple)):
        is_simple = all(isinstance(v, (str, int, float, bool, type(None))) for v in value)
        if is_simple:
            return list(value)
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as e:
            logger.warning(f"Cannot serialize list attr {path}: {e}")
            return str(value)
    return str(value)


def sanitize_attributes(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize entire attributes dict for Neo4j."""
    if not attrs:
        return {}
    return {k: sanitize_for_neo4j(v, path=k) for k, v in attrs.items()}


def _normalise_llm_response(llm_response):
    """Normalise LLM response to dict format expected by ExtractedEntities.

    Some reasoning models return a list instead of {"extracted_entities": [...]}.
    """
    if isinstance(llm_response, list):
        logger.warning("LLM returned list instead of dict, wrapping in ExtractedEntities format")
        return {"extracted_entities": llm_response}
    if isinstance(llm_response, dict) and "extracted_entities" not in llm_response:
        if "name" in llm_response and "entity_type_id" in llm_response:
            logger.warning("LLM returned flat entity dict, wrapping in list")
            return {"extracted_entities": [llm_response]}
    return llm_response


def apply_node_operations_patch() -> bool:
    """Patch node_operations to normalise list LLM responses."""
    global _node_ops_patch_applied
    if _node_ops_patch_applied:
        return True

    try:
        from graphiti_core.utils.maintenance import node_operations
        from graphiti_core.prompts.extract_nodes import ExtractedEntities

        @functools.wraps(node_operations._extract_nodes_single)
        async def patched_extract_nodes_single(llm_client, episode, context):
            llm_response = await node_operations._call_extraction_llm(llm_client, episode, context)
            llm_response = _normalise_llm_response(llm_response)
            return ExtractedEntities(**llm_response).extracted_entities

        @functools.wraps(node_operations._extract_nodes_chunked)
        async def patched_extract_nodes_chunked(llm_client, chunk, base_context, episode):
            chunk_context = {**base_context, 'episode_content': chunk}
            llm_response = await node_operations._call_extraction_llm(llm_client, episode, chunk_context)
            llm_response = _normalise_llm_response(llm_response)
            return ExtractedEntities(**llm_response).extracted_entities

        node_operations._extract_nodes_single = patched_extract_nodes_single
        node_operations._extract_nodes_chunked = patched_extract_nodes_chunked

        _node_ops_patch_applied = True
        logger.info("Graphiti node_operations patch applied (LLM list-to-dict normalisation)")
        return True

    except ImportError as e:
        logger.warning(f"Cannot import graphiti_core node_operations: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to apply node_operations patch: {e}")
        return False


def apply_patch() -> bool:
    """Apply all monkey-patches to graphiti-core."""
    global _patch_applied

    if _patch_applied:
        logger.debug("Graphiti patches already applied, skipping")
        return True

    # Patch 1: Neo4j attribute sanitization
    try:
        from graphiti_core.utils import bulk_utils

        original_add_nodes_and_edges_bulk_tx = bulk_utils.add_nodes_and_edges_bulk_tx

        @functools.wraps(original_add_nodes_and_edges_bulk_tx)
        async def patched_add_nodes_and_edges_bulk_tx(
            tx,
            episodic_nodes,
            episodic_edges,
            entity_nodes,
            entity_edges,
            embedder,
            driver,
        ):
            for node in entity_nodes:
                if hasattr(node, 'attributes') and node.attributes:
                    node.attributes = sanitize_attributes(node.attributes)
            for edge in entity_edges:
                if hasattr(edge, 'attributes') and edge.attributes:
                    edge.attributes = sanitize_attributes(edge.attributes)
            return await original_add_nodes_and_edges_bulk_tx(
                tx, episodic_nodes, episodic_edges, entity_nodes, entity_edges, embedder, driver,
            )

        bulk_utils.add_nodes_and_edges_bulk_tx = patched_add_nodes_and_edges_bulk_tx
        logger.info("Graphiti bulk_utils patch applied (Neo4j attribute sanitization)")
    except ImportError as e:
        logger.warning(f"Cannot import graphiti_core.utils.bulk_utils: {e}")
    except Exception as e:
        logger.error(f"Failed to apply bulk_utils patch: {e}")

    # Patch 2: LLM response normalisation (list -> dict)
    apply_node_operations_patch()

    # Patch 3: Strip reasoning tags from json.loads in graphiti_core
    try:
        original_json_loads = json.loads

        def patched_json_loads(s, *args, **kwargs):
            if isinstance(s, str):
                s = _THINK_PATTERN.sub("", s)
                s = _REASONING_PATTERN.sub("", s)
                s = s.strip()
            return original_json_loads(s, *args, **kwargs)

        import graphiti_core.llm_client.openai_generic_client as gc_module
        gc_module.json.loads = patched_json_loads
        logger.info("Graphiti json.loads patch applied (reasoning tag stripping)")
    except ImportError as e:
        logger.warning(f"Cannot import graphiti_core.llm_client for json patch: {e}")
    except Exception as e:
        logger.error(f"Failed to apply json.loads patch: {e}")

    _patch_applied = True
    return True
