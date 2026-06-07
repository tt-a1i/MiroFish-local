"""
Zep Cloud client implementation

Wraps the existing zep-cloud SDK, implementing the ZepClientAdapter interface.
Maintains consistency with existing code logic, ensuring backward compatibility.
"""

from typing import Any, Dict, List, Optional

from zep_cloud.client import Zep
from zep_cloud import EpisodeData

from .zep_adapter import (
    ZepClientAdapter,
    GraphNode,
    GraphEdge,
    SearchResult,
    EpisodeStatus,
)


class ZepCloudClient(ZepClientAdapter):
    """
    Zep Cloud client implementation

    Wraps zep-cloud SDK, converting native API responses to adapter data structures.
    """

    def __init__(self, api_key: str):
        """
        Initialize Zep Cloud client

        Args:
            api_key: Zep Cloud API Key
        """
        if not api_key:
            raise ValueError("ZEP_API_KEY not configured")
        self.client = Zep(api_key=api_key)

    # ==================== Graph operations ====================

    def create_graph(self, graph_id: str, name: str, description: str) -> None:
        """Create Zep graph"""
        self.client.graph.create(
            graph_id=graph_id,
            name=name,
            description=description
        )

    def delete_graph(self, graph_id: str) -> None:
        """Delete graph"""
        self.client.graph.delete(graph_id=graph_id)

    def set_ontology(
        self,
        graph_ids: List[str],
        entities: Optional[Dict[str, Any]] = None,
        edges: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Set graph ontology

        Passed directly to the Zep Cloud API. Entities and edges should be pre-built dynamic classes.
        """
        if entities or edges:
            self.client.graph.set_ontology(
                graph_ids=graph_ids,
                entities=entities if entities else None,
                edges=edges if edges else None,
            )

    # ==================== Episode operations ====================

    def add_episode(self, graph_id: str, data: str, episode_type: str = "text") -> str:
        """Add a single episode"""
        result = self.client.graph.add(
            graph_id=graph_id,
            type=episode_type,
            data=data
        )
        # Return episode UUID
        return getattr(result, 'uuid_', None) or getattr(result, 'uuid', '') or ''

    def add_episode_batch(
        self,
        graph_id: str,
        episodes: List[Dict[str, Any]]
    ) -> List[str]:
        """Batch add episodes"""
        episode_data_list = [
            EpisodeData(data=ep.get("data", ""), type=ep.get("type", "text"))
            for ep in episodes
        ]

        batch_result = self.client.graph.add_batch(
            graph_id=graph_id,
            episodes=episode_data_list
        )

        # Extract all episode UUIDs
        uuids = []
        if batch_result and isinstance(batch_result, list):
            for ep in batch_result:
                ep_uuid = getattr(ep, 'uuid_', None) or getattr(ep, 'uuid', None)
                if ep_uuid:
                    uuids.append(ep_uuid)
        return uuids

    def get_episode_status(self, episode_uuid: str) -> EpisodeStatus:
        """Get episode processing status"""
        episode = self.client.graph.episode.get(uuid_=episode_uuid)
        return EpisodeStatus(
            uuid=episode_uuid,
            processed=getattr(episode, 'processed', False)
        )

    # ==================== Node operations ====================

    def get_all_nodes(self, graph_id: str) -> List[GraphNode]:
        """Get all nodes in a graph"""
        nodes = self.client.graph.node.get_by_graph_id(graph_id=graph_id)
        return [self._convert_node(node) for node in nodes]

    def get_node(self, node_uuid: str) -> Optional[GraphNode]:
        """Get a single node"""
        try:
            node = self.client.graph.node.get(uuid_=node_uuid)
            return self._convert_node(node) if node else None
        except Exception:
            return None

    def get_node_edges(self, node_uuid: str) -> List[GraphEdge]:
        """Get all edges related to a node"""
        try:
            edges = self.client.graph.node.get_entity_edges(node_uuid=node_uuid)
            return [self._convert_edge(edge) for edge in edges]
        except Exception:
            return []

    # ==================== Edge operations ====================

    def get_all_edges(self, graph_id: str) -> List[GraphEdge]:
        """Get all edges in a graph"""
        edges = self.client.graph.edge.get_by_graph_id(graph_id=graph_id)
        return [self._convert_edge(edge) for edge in edges]

    # ==================== Search operations ====================

    def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
        reranker: str = "cross_encoder"
    ) -> SearchResult:
        """Graph hybrid search"""
        search_result = self.client.graph.search(
            graph_id=graph_id,
            query=query,
            limit=limit,
            scope=scope,
            reranker=reranker
        )

        nodes = []
        edges = []

        # Process nodes
        if hasattr(search_result, 'nodes') and search_result.nodes:
            nodes = [self._convert_node(n) for n in search_result.nodes]

        # Process edges
        if hasattr(search_result, 'edges') and search_result.edges:
            edges = [self._convert_edge(e) for e in search_result.edges]

        return SearchResult(nodes=nodes, edges=edges)

    # ==================== Conversion helper methods ====================

    def _convert_node(self, node: Any) -> GraphNode:
        """Convert Zep Node object to GraphNode"""
        created_at = getattr(node, 'created_at', None)
        return GraphNode(
            uuid=getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
            name=node.name or '',
            labels=node.labels or [],
            summary=node.summary or '',
            attributes=node.attributes or {},
            created_at=str(created_at) if created_at else None
        )

    def _convert_edge(self, edge: Any) -> GraphEdge:
        """Convert Zep Edge object to GraphEdge"""
        # Process time fields
        created_at = getattr(edge, 'created_at', None)
        valid_at = getattr(edge, 'valid_at', None)
        invalid_at = getattr(edge, 'invalid_at', None)
        expired_at = getattr(edge, 'expired_at', None)

        # Process episodes
        episodes = getattr(edge, 'episodes', None) or getattr(edge, 'episode_ids', None)
        if episodes and not isinstance(episodes, list):
            episodes = [str(episodes)]
        elif episodes:
            episodes = [str(e) for e in episodes]
        else:
            episodes = []

        return GraphEdge(
            uuid=getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', ''),
            name=edge.name or '',
            fact=edge.fact or '',
            source_node_uuid=edge.source_node_uuid,
            target_node_uuid=edge.target_node_uuid,
            attributes=edge.attributes or {},
            created_at=str(created_at) if created_at else None,
            valid_at=str(valid_at) if valid_at else None,
            invalid_at=str(invalid_at) if invalid_at else None,
            expired_at=str(expired_at) if expired_at else None,
            episodes=episodes,
            fact_type=getattr(edge, 'fact_type', None) or edge.name or ''
        )
