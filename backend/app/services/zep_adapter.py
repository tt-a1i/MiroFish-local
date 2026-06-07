"""
Zep client adapter interface

Defines a unified Zep client abstraction interface, supporting cloud/graphiti dual implementation switching.
MVP goal: run the core pipeline of 'build graph -> read entities -> search -> report' without depending on Zep Cloud.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ============================================================
# Data structure definitions
# ============================================================

@dataclass
class GraphNode:
    """Graph node (aligned with zep-cloud Node structure)"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    created_at: Optional[str] = None


@dataclass
class GraphEdge:
    """Graph edge (aligned with zep-cloud Edge structure)"""
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    attributes: Dict[str, Any]
    created_at: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    expired_at: Optional[str] = None
    episodes: List[str] = field(default_factory=list)
    fact_type: Optional[str] = None


@dataclass
class SearchResult:
    """Search result"""
    nodes: List[GraphNode]
    edges: List[GraphEdge]


@dataclass
class EpisodeStatus:
    """Episode processing status"""
    uuid: str
    processed: bool


# ============================================================
# Adapter abstract interface
# ============================================================

class ZepClientAdapter(ABC):
    """
    Unified Zep client interface

    Implementations:
    - ZepCloudClient: Wraps existing zep-cloud SDK
    - GraphitiClient: Local Graphiti + Neo4j implementation

    MVP scope:
    - create_graph: Create graph
    - set_ontology: Set ontology (Graphiti can no-op)
    - add_episode: Add a single episode
    - add_episode_batch: Batch add
    - search: Semantic/hybrid search
    - get_all_nodes: Get all nodes in a graph
    - get_all_edges: Get all edges in a graph
    - get_node: Get a single node
    - get_node_edges: Get edges related to a node
    - delete_graph: Delete graph
    - wait_for_episode: Wait for episode processing to complete (Graphiti processes synchronously, returns directly)
    """

    # ==================== Graph operations ====================

    @abstractmethod
    def create_graph(self, graph_id: str, name: str, description: str) -> None:
        """
        Create knowledge graph

        Args:
            graph_id: Graph unique identifier
            name: Graph name
            description: Graph description
        """
        ...

    @abstractmethod
    def delete_graph(self, graph_id: str) -> None:
        """
        Delete graph

        Args:
            graph_id: Graph ID
        """
        ...

    @abstractmethod
    def set_ontology(
        self,
        graph_ids: List[str],
        entities: Optional[Dict[str, Any]] = None,
        edges: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Set graph ontology (entity types, edge type definitions)

        MVP note: Graphiti implementation can no-op or just log for prompt hints.

        Args:
            graph_ids: List of graph IDs
            entities: Entity type definitions {type_name: EntityModelClass}
            edges: Edge type definitions {edge_name: (EdgeModelClass, [source_targets])}
        """
        ...

    # ==================== Episode operations ====================

    @abstractmethod
    def add_episode(self, graph_id: str, data: str, episode_type: str = "text") -> str:
        """
        Add a single episode to graph

        Args:
            graph_id: Graph ID
            data: Episode content
            episode_type: Type, default "text"

        Returns:
            Episode UUID
        """
        ...

    @abstractmethod
    def add_episode_batch(
        self,
        graph_id: str,
        episodes: List[Dict[str, Any]]
    ) -> List[str]:
        """
        Batch add episodes to graph

        Args:
            graph_id: Graph ID
            episodes: List of episodes, each containing {"data": str, "type": str}

        Returns:
            List of episode UUIDs
        """
        ...

    @abstractmethod
    def get_episode_status(self, episode_uuid: str) -> EpisodeStatus:
        """
        Get episode processing status

        Args:
            episode_uuid: Episode UUID

        Returns:
            EpisodeStatus containing uuid and processed state
        """
        ...

    def wait_for_episode(self, episode_uuid: str, timeout: int = 300) -> bool:
        """
        Wait for episode processing to complete

        Default implementation: poll get_episode_status until processed=True or timeout.
        Graphiti implementation can override to return True directly (synchronous processing).

        Args:
            episode_uuid: Episode UUID
            timeout: Timeout in seconds

        Returns:
            Whether processing completed
        """
        import time
        start_time = time.time()
        while time.time() - start_time < timeout:
            status = self.get_episode_status(episode_uuid)
            if status.processed:
                return True
            time.sleep(2)
        return False

    # ==================== Node operations ====================

    @abstractmethod
    def get_all_nodes(self, graph_id: str) -> List[GraphNode]:
        """
        Get all nodes in a graph

        Args:
            graph_id: Graph ID

        Returns:
            List of nodes
        """
        ...

    @abstractmethod
    def get_node(self, node_uuid: str) -> Optional[GraphNode]:
        """
        Get single node details

        Args:
            node_uuid: Node UUID

        Returns:
            Node object, or None if not found
        """
        ...

    @abstractmethod
    def get_node_edges(self, node_uuid: str) -> List[GraphEdge]:
        """
        Get all edges related to a node

        Args:
            node_uuid: Node UUID

        Returns:
            List of edges (including edges where this node is source or target)
        """
        ...

    # ==================== Edge operations ====================

    @abstractmethod
    def get_all_edges(self, graph_id: str) -> List[GraphEdge]:
        """
        Get all edges in a graph

        Args:
            graph_id: Graph ID

        Returns:
            List of edges
        """
        ...

    # ==================== Search operations ====================

    @abstractmethod
    def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
        reranker: str = "cross_encoder"
    ) -> SearchResult:
        """
        Graph hybrid search

        Args:
            graph_id: Graph ID
            query: Search query
            limit: Maximum number of results to return
            scope: Search scope - "edges" | "nodes" | "both"
            reranker: Reranking strategy - "cross_encoder" | "rrf" | "none"

        Returns:
            SearchResult containing matching nodes and edges
        """
        ...
