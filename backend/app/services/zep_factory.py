"""
Zep client factory

Automatically selects Zep Cloud or Graphiti local implementation based on configuration.
Backend selection is controlled via the ZEP_BACKEND environment variable.
"""

import logging
from functools import lru_cache
from typing import Optional

from ..config import Config
from .zep_adapter import ZepClientAdapter

logger = logging.getLogger('mirofish.zep_factory')


def create_zep_client(
    backend: Optional[str] = None,
    api_key: Optional[str] = None,
    neo4j_uri: Optional[str] = None,
    neo4j_user: Optional[str] = None,
    neo4j_password: Optional[str] = None,
) -> ZepClientAdapter:
    """
    Create Zep client instance

    Select implementation based on backend parameter or ZEP_BACKEND env var:
    - 'cloud': Use Zep Cloud (requires ZEP_API_KEY)
    - 'graphiti': Use Graphiti + Neo4j local deployment

    Args:
        backend: Backend selection ('cloud' | 'graphiti'), defaults to env var
        api_key: Zep Cloud API Key (only needed for cloud mode)
        neo4j_uri: Neo4j URI (only needed for graphiti mode)
        neo4j_user: Neo4j username
        neo4j_password: Neo4j password

    Returns:
        ZepClientAdapter instance
    """
    # Determine backend type
    backend = backend or Config.ZEP_BACKEND

    if backend == 'graphiti':
        return _create_graphiti_client(neo4j_uri, neo4j_user, neo4j_password)
    else:
        return _create_cloud_client(api_key)


def _create_cloud_client(api_key: Optional[str] = None) -> ZepClientAdapter:
    """Create Zep Cloud client"""
    from .zep_cloud_impl import ZepCloudClient

    key = api_key or Config.ZEP_API_KEY
    if not key:
        raise ValueError(
            "ZEP_API_KEY not configured. Using Zep Cloud requires setting the ZEP_API_KEY environment variable."
        )

    logger.info("Creating Zep Cloud client")
    return ZepCloudClient(api_key=key)


def _create_graphiti_client(
    neo4j_uri: Optional[str] = None,
    neo4j_user: Optional[str] = None,
    neo4j_password: Optional[str] = None,
) -> ZepClientAdapter:
    """Create Graphiti local client"""
    from .zep_graphiti_impl import GraphitiClient

    uri = neo4j_uri or Config.NEO4J_URI
    user = neo4j_user or Config.NEO4J_USER
    password = neo4j_password or Config.NEO4J_PASSWORD

    if not all([uri, user, password]):
        raise ValueError(
            "Neo4j configuration incomplete. Using Graphiti requires setting NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD."
        )

    logger.info(f"Creating Graphiti local client: {uri}")
    return GraphitiClient(
        neo4j_uri=uri,
        neo4j_user=user,
        neo4j_password=password,
    )


# ============================================================
# Singleton cache (optional, for scenarios requiring shared client instances)
# ============================================================

import threading

_client_instance: Optional[ZepClientAdapter] = None
_client_lock = threading.Lock()


def get_zep_client() -> ZepClientAdapter:
    """
    Get the globally shared Zep client instance (thread-safe)

    Creates the instance on first call, returns the same instance on subsequent calls.
    Suitable for scenarios requiring connection reuse (e.g., Neo4j connection pool).

    Uses double-checked locking to ensure thread safety with optimal performance.

    Note: If you need an independent instance, call create_zep_client() directly.
    """
    global _client_instance
    if _client_instance is None:
        with _client_lock:
            # Double-check: prevent multiple threads from passing the first check simultaneously
            if _client_instance is None:
                _client_instance = create_zep_client()
    return _client_instance


def reset_zep_client():
    """
    Reset global client instance (thread-safe)

    Used for testing or scenarios requiring re-initialization.
    """
    global _client_instance
    with _client_lock:
        if _client_instance is not None:
            # Attempt to close connection
            if hasattr(_client_instance, 'close'):
                try:
                    _client_instance.close()
                except Exception:
                    pass
            _client_instance = None
            logger.info("Global Zep client instance has been reset")
