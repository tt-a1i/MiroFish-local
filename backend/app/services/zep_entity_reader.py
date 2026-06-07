"""
Zep entity reader and filter service
Reads nodes from Zep graph, filters nodes matching predefined entity types

Supports dual backends:
- Zep Cloud (default)
- Graphiti + Neo4j local deployment
"""

import time
from typing import Dict, Any, List, Optional, Set, Callable, TypeVar
from dataclasses import dataclass, field

from ..config import Config
from ..utils.logger import get_logger
from .zep_factory import get_zep_client
from .zep_adapter import ZepClientAdapter

logger = get_logger('mirofish.zep_entity_reader')

# For generic return type
T = TypeVar('T')


@dataclass
class EntityNode:
    """Entity node data structure"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    # Related edge information
    related_edges: List[Dict[str, Any]] = field(default_factory=list)
    # Related node information
    related_nodes: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes,
            "related_edges": self.related_edges,
            "related_nodes": self.related_nodes,
        }
    
    def get_entity_type(self) -> Optional[str]:
        """Get entity type (excluding default Entity label)"""
        for label in self.labels:
            if label not in ["Entity", "Node"]:
                return label
        return None


@dataclass
class FilteredEntities:
    """Filtered entity collection"""
    entities: List[EntityNode]
    entity_types: Set[str]
    total_count: int
    filtered_count: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "entity_types": list(self.entity_types),
            "total_count": self.total_count,
            "filtered_count": self.filtered_count,
        }


class ZepEntityReader:
    """
    Zep entity reader and filter service

    Main features:
    1. Read all nodes from Zep graph
    2. Filter nodes matching predefined entity types (nodes whose Labels are not just Entity)
    3. Get related edges and associated node information for each entity
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize entity reader service (using adapter factory)"""
        # Use singleton to get adapter (avoid repeated initialization)
        self.client: ZepClientAdapter = get_zep_client()
    
    def _call_with_retry(
        self, 
        func: Callable[[], T], 
        operation_name: str,
        max_retries: int = 3,
        initial_delay: float = 2.0
    ) -> T:
        """
        Zep API call with retry mechanism

        Args:
            func: Function to execute (parameterless lambda or callable)
            operation_name: Operation name, used for logging
            max_retries: Maximum retry count (default 3, i.e. at most 3 attempts)
            initial_delay: Initial delay in seconds

        Returns:
            API call result
        """
        last_exception = None
        delay = initial_delay
        
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Zep {operation_name} attempt {attempt + 1} failed: {str(e)[:100]}, "
                        f"retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    delay *= 2  # Exponential backoff
                else:
                    logger.error(f"Zep {operation_name} still failed after {max_retries} attempts: {str(e)}")
        
        raise last_exception
    
    def get_all_nodes(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        Get all nodes in a graph (with retry mechanism)

        Args:
            graph_id: Graph ID

        Returns:
            List of nodes
        """
        logger.info(f"Getting all nodes for graph {graph_id}...")

        # Call adapter API with retry mechanism
        nodes = self._call_with_retry(
            func=lambda: self.client.get_all_nodes(graph_id),
            operation_name=f"get nodes (graph={graph_id})"
        )

        nodes_data = []
        for node in nodes:
            nodes_data.append({
                "uuid": node.uuid,
                "name": node.name or "",
                "labels": node.labels or [],
                "summary": node.summary or "",
                "attributes": node.attributes or {},
            })

        logger.info(f"Retrieved {len(nodes_data)} nodes in total")
        return nodes_data
    
    def get_all_edges(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        Get all edges in a graph (with retry mechanism)

        Args:
            graph_id: Graph ID

        Returns:
            List of edges
        """
        logger.info(f"Getting all edges for graph {graph_id}...")

        # Call adapter API with retry mechanism
        edges = self._call_with_retry(
            func=lambda: self.client.get_all_edges(graph_id),
            operation_name=f"get edges (graph={graph_id})"
        )

        edges_data = []
        for edge in edges:
            edges_data.append({
                "uuid": edge.uuid,
                "name": edge.name or "",
                "fact": edge.fact or "",
                "source_node_uuid": edge.source_node_uuid,
                "target_node_uuid": edge.target_node_uuid,
                "attributes": edge.attributes or {},
            })

        logger.info(f"Retrieved {len(edges_data)} edges in total")
        return edges_data
    
    def get_node_edges(self, node_uuid: str) -> List[Dict[str, Any]]:
        """
        Get all edges related to a specified node (with retry mechanism)

        Args:
            node_uuid: Node UUID

        Returns:
            List of edges
        """
        try:
            # Call adapter API with retry mechanism
            edges = self._call_with_retry(
                func=lambda: self.client.get_node_edges(node_uuid),
                operation_name=f"get node edges (node={node_uuid[:8]}...)"
            )

            edges_data = []
            for edge in edges:
                edges_data.append({
                    "uuid": edge.uuid,
                    "name": edge.name or "",
                    "fact": edge.fact or "",
                    "source_node_uuid": edge.source_node_uuid,
                    "target_node_uuid": edge.target_node_uuid,
                    "attributes": edge.attributes or {},
                })

            return edges_data
        except Exception as e:
            logger.warning(f"Failed to get edges for node {node_uuid}: {str(e)}")
            return []
    
    def filter_defined_entities(
        self, 
        graph_id: str,
        defined_entity_types: Optional[List[str]] = None,
        enrich_with_edges: bool = True
    ) -> FilteredEntities:
        """
        Filter nodes matching predefined entity types

        Filtering logic:
        - If a node's Labels only contain "Entity", it doesn't match our predefined types, skip it
        - If a node's Labels contain labels other than "Entity" and "Node", it matches predefined types, keep it

        Args:
            graph_id: Graph ID
            defined_entity_types: List of predefined entity types (optional; if provided, only keep these types)
            enrich_with_edges: Whether to get related edge information for each entity

        Returns:
            FilteredEntities: Filtered entity collection
        """
        logger.info(f"Starting entity filtering for graph {graph_id}...")

        # Get all nodes
        all_nodes = self.get_all_nodes(graph_id)
        total_count = len(all_nodes)
        
        # Get all edges (for subsequent association lookup)
        all_edges = self.get_all_edges(graph_id) if enrich_with_edges else []

        # Build mapping from node UUID to node data
        node_map = {n["uuid"]: n for n in all_nodes}
        
        # Filter entities matching criteria
        filtered_entities = []
        entity_types_found = set()
        
        for node in all_nodes:
            labels = node.get("labels", [])
            
            # Filtering logic: Labels must include labels other than "Entity" and "Node"
            custom_labels = [l for l in labels if l not in ["Entity", "Node"]]
            
            if not custom_labels:
                # Only default labels, skip
                continue
            
            # If predefined types are specified, check for match
            if defined_entity_types:
                matching_labels = [l for l in custom_labels if l in defined_entity_types]
                if not matching_labels:
                    continue
                entity_type = matching_labels[0]
            else:
                entity_type = custom_labels[0]
            
            entity_types_found.add(entity_type)
            
            # Create entity node object
            entity = EntityNode(
                uuid=node["uuid"],
                name=node["name"],
                labels=labels,
                summary=node["summary"],
                attributes=node["attributes"],
            )
            
            # Get related edges and nodes
            if enrich_with_edges:
                related_edges = []
                related_node_uuids = set()
                
                for edge in all_edges:
                    if edge["source_node_uuid"] == node["uuid"]:
                        related_edges.append({
                            "direction": "outgoing",
                            "edge_name": edge["name"],
                            "fact": edge["fact"],
                            "target_node_uuid": edge["target_node_uuid"],
                        })
                        related_node_uuids.add(edge["target_node_uuid"])
                    elif edge["target_node_uuid"] == node["uuid"]:
                        related_edges.append({
                            "direction": "incoming",
                            "edge_name": edge["name"],
                            "fact": edge["fact"],
                            "source_node_uuid": edge["source_node_uuid"],
                        })
                        related_node_uuids.add(edge["source_node_uuid"])
                
                entity.related_edges = related_edges
                
                # Get basic info of associated nodes
                related_nodes = []
                for related_uuid in related_node_uuids:
                    if related_uuid in node_map:
                        related_node = node_map[related_uuid]
                        related_nodes.append({
                            "uuid": related_node["uuid"],
                            "name": related_node["name"],
                            "labels": related_node["labels"],
                            "summary": related_node.get("summary", ""),
                        })
                
                entity.related_nodes = related_nodes
            
            filtered_entities.append(entity)
        
        # Fallback: if no nodes with business labels found, return all Entity nodes
        # This is common with the Graphiti backend (known issue: custom labels are not applied to Neo4j nodes)
        if not filtered_entities and total_count > 0 and not defined_entity_types:
            logger.warning(
                f"Graph {graph_id} has no nodes with business labels, falling back to returning all {total_count} nodes. "
                "This may be because the Graphiti backend is used and ontology is not configured."
            )
            for node in all_nodes:
                entity = EntityNode(
                    uuid=node["uuid"],
                    name=node["name"],
                    labels=node.get("labels", ["Entity"]),
                    summary=node["summary"],
                    attributes=node["attributes"],
                )
                # Get related edges and nodes
                if enrich_with_edges:
                    related_edges = []
                    related_node_uuids = set()
                    for edge in all_edges:
                        if edge["source_node_uuid"] == node["uuid"]:
                            related_edges.append({
                                "direction": "outgoing",
                                "edge_name": edge["name"],
                                "fact": edge["fact"],
                                "target_node_uuid": edge["target_node_uuid"],
                            })
                            related_node_uuids.add(edge["target_node_uuid"])
                        elif edge["target_node_uuid"] == node["uuid"]:
                            related_edges.append({
                                "direction": "incoming",
                                "edge_name": edge["name"],
                                "fact": edge["fact"],
                                "source_node_uuid": edge["source_node_uuid"],
                            })
                            related_node_uuids.add(edge["source_node_uuid"])
                    entity.related_edges = related_edges
                    related_nodes = []
                    for related_uuid in related_node_uuids:
                        if related_uuid in node_map:
                            related_node = node_map[related_uuid]
                            related_nodes.append({
                                "uuid": related_node["uuid"],
                                "name": related_node["name"],
                                "labels": related_node["labels"],
                                "summary": related_node.get("summary", ""),
                            })
                    entity.related_nodes = related_nodes
                filtered_entities.append(entity)
            entity_types_found.add("Entity")

        logger.info(f"Filtering complete: total nodes {total_count}, matching {len(filtered_entities)}, "
                   f"entity types: {entity_types_found}")

        return FilteredEntities(
            entities=filtered_entities,
            entity_types=entity_types_found,
            total_count=total_count,
            filtered_count=len(filtered_entities),
        )
    
    def get_entity_with_context(
        self,
        graph_id: str,
        entity_uuid: str
    ) -> Optional[EntityNode]:
        """
        Get a single entity and its full context (edges and associated nodes, with retry mechanism)

        Args:
            graph_id: Graph ID
            entity_uuid: Entity UUID

        Returns:
            EntityNode or None
        """
        try:
            # Get node with retry mechanism
            node = self._call_with_retry(
                func=lambda: self.client.get_node(entity_uuid),
                operation_name=f"get node details (uuid={entity_uuid[:8]}...)"
            )

            if not node:
                return None

            # Get node's edges
            edges = self.get_node_edges(entity_uuid)

            # Get all nodes for association lookup
            all_nodes = self.get_all_nodes(graph_id)
            node_map = {n["uuid"]: n for n in all_nodes}

            # Process related edges and nodes
            related_edges = []
            related_node_uuids = set()

            for edge in edges:
                if edge["source_node_uuid"] == entity_uuid:
                    related_edges.append({
                        "direction": "outgoing",
                        "edge_name": edge["name"],
                        "fact": edge["fact"],
                        "target_node_uuid": edge["target_node_uuid"],
                    })
                    related_node_uuids.add(edge["target_node_uuid"])
                else:
                    related_edges.append({
                        "direction": "incoming",
                        "edge_name": edge["name"],
                        "fact": edge["fact"],
                        "source_node_uuid": edge["source_node_uuid"],
                    })
                    related_node_uuids.add(edge["source_node_uuid"])

            # Get associated node information
            related_nodes = []
            for related_uuid in related_node_uuids:
                if related_uuid in node_map:
                    related_node = node_map[related_uuid]
                    related_nodes.append({
                        "uuid": related_node["uuid"],
                        "name": related_node["name"],
                        "labels": related_node["labels"],
                        "summary": related_node.get("summary", ""),
                    })

            return EntityNode(
                uuid=node.uuid,
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {},
                related_edges=related_edges,
                related_nodes=related_nodes,
            )

        except Exception as e:
            logger.error(f"Failed to get entity {entity_uuid}: {str(e)}")
            return None
    
    def get_entities_by_type(
        self, 
        graph_id: str, 
        entity_type: str,
        enrich_with_edges: bool = True
    ) -> List[EntityNode]:
        """
        Get all entities of a specified type

        Args:
            graph_id: Graph ID
            entity_type: Entity type (e.g., "Student", "PublicFigure", etc.)
            enrich_with_edges: Whether to get related edge information

        Returns:
            List of entities
        """
        result = self.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=[entity_type],
            enrich_with_edges=enrich_with_edges
        )
        return result.entities


