"""
Graph builder service
Interface 2: Build Standalone Graph using Zep API

Supports dual backends:
- Zep Cloud (default)
- Graphiti + Neo4j local deployment
"""

import os
import uuid
import time
import threading
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from ..config import Config
from ..models.task import TaskManager, TaskStatus
from .text_processor import TextProcessor
from .zep_factory import get_zep_client
from .zep_adapter import ZepClientAdapter


@dataclass
class GraphInfo:
    """Graph information"""
    graph_id: str
    node_count: int
    edge_count: int
    entity_types: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "entity_types": self.entity_types,
        }


class GraphBuilderService:
    """
    Graph builder service
    Responsible for calling Zep API to build knowledge graphs

    Supports dual backends:
    - Zep Cloud: Uses zep-cloud SDK
    - Graphiti: Uses graphiti-core + Neo4j
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize graph builder service

        Args:
            api_key: Zep API Key (only needed for cloud mode, optional)
        """
        # Use singleton to get adapter (avoid repeated initialization)
        self.client: ZepClientAdapter = get_zep_client()
        self.task_manager = TaskManager()

        # Record backend type (for conditional logic)
        self._backend = Config.ZEP_BACKEND
    
    def build_graph_async(
        self,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str = "MiroFish Graph",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        batch_size: int = 3
    ) -> str:
        """
        Build graph asynchronously

        Args:
            text: Input text
            ontology: Ontology definition (output from interface 1)
            graph_name: Graph name
            chunk_size: Text chunk size
            chunk_overlap: Chunk overlap size
            batch_size: Number of chunks per batch

        Returns:
            Task ID
        """
        # Create task
        task_id = self.task_manager.create_task(
            task_type="graph_build",
            metadata={
                "graph_name": graph_name,
                "chunk_size": chunk_size,
                "text_length": len(text),
            }
        )
        
        # Execute build in background thread
        thread = threading.Thread(
            target=self._build_graph_worker,
            args=(task_id, text, ontology, graph_name, chunk_size, chunk_overlap, batch_size)
        )
        thread.daemon = True
        thread.start()
        
        return task_id
    
    def _build_graph_worker(
        self,
        task_id: str,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str,
        chunk_size: int,
        chunk_overlap: int,
        batch_size: int
    ):
        """Graph build worker thread"""
        try:
            self.task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                progress=5,
                message="Starting graph build..."
            )
            
            # 1. Create graph
            graph_id = self.create_graph(graph_name)
            self.task_manager.update_task(
                task_id,
                progress=10,
                message=f"Graph created: {graph_id}"
            )
            
            # 2. Set ontology
            self.set_ontology(graph_id, ontology)
            self.task_manager.update_task(
                task_id,
                progress=15,
                message="Ontology set"
            )
            
            # 3. Split text into chunks
            chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)
            total_chunks = len(chunks)
            self.task_manager.update_task(
                task_id,
                progress=20,
                message=f"Text split into {total_chunks} chunks"
            )
            
            # 4. Send data in batches
            episode_uuids = self.add_text_batches(
                graph_id, chunks, batch_size,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=20 + int(prog * 0.4),  # 20-60%
                    message=msg
                )
            )
            
            # 5. Wait for Zep processing to complete
            self.task_manager.update_task(
                task_id,
                progress=60,
                message="Waiting for Zep to process data..."
            )
            
            self._wait_for_episodes(
                episode_uuids,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=60 + int(prog * 0.3),  # 60-90%
                    message=msg
                )
            )
            
            # 6. Get graph info
            self.task_manager.update_task(
                task_id,
                progress=90,
                message="Getting graph info..."
            )
            
            graph_info = self._get_graph_info(graph_id)
            
            # Done
            self.task_manager.complete_task(task_id, {
                "graph_id": graph_id,
                "graph_info": graph_info.to_dict(),
                "chunks_processed": total_chunks,
            })
            
        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.task_manager.fail_task(task_id, error_msg)
    
    def create_graph(self, name: str) -> str:
        """Create graph (public method)"""
        graph_id = f"mirofish_{uuid.uuid4().hex[:16]}"

        self.client.create_graph(
            graph_id=graph_id,
            name=name,
            description="MiroFish Social Simulation Graph"
        )

        return graph_id
    
    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        """
        Set graph ontology (public method)

        Choose processing method based on backend type:
        - Zep Cloud: Dynamically create Pydantic models, call Zep API
        - Graphiti: Cache ontology definition (MVP stage no-op)
        """
        if self._backend == 'graphiti':
            # Graphiti backend: pass raw ontology directly, adapter will cache or no-op
            self.client.set_ontology(
                graph_ids=[graph_id],
                entities=ontology.get("entity_types", []),
                edges=ontology.get("edge_types", []),
            )
            return

        # Zep Cloud backend: need to dynamically create Pydantic models
        import warnings
        from typing import Optional
        from pydantic import Field
        from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel
        from zep_cloud import EntityEdgeSourceTarget

        # Suppress Pydantic v2 warnings about Field(default=None)
        # This is required by the Zep SDK; warnings come from dynamic class creation and can be safely ignored
        warnings.filterwarnings('ignore', category=UserWarning, module='pydantic')

        # Zep reserved names that cannot be used as attribute names
        RESERVED_NAMES = {'uuid', 'name', 'group_id', 'name_embedding', 'summary', 'created_at'}

        def safe_attr_name(attr_name: str) -> str:
            """Convert reserved names to safe names"""
            if attr_name.lower() in RESERVED_NAMES:
                return f"entity_{attr_name}"
            return attr_name

        # Dynamically create entity types
        entity_types = {}
        for entity_def in ontology.get("entity_types", []):
            name = entity_def["name"]
            description = entity_def.get("description", f"A {name} entity.")

            # Create attribute dict and type annotations (required by Pydantic v2)
            attrs = {"__doc__": description}
            annotations = {}

            for attr_def in entity_def.get("attributes", []):
                attr_name = safe_attr_name(attr_def["name"])  # Use safe name
                attr_desc = attr_def.get("description", attr_name)
                # Zep API requires Field description, this is mandatory
                attrs[attr_name] = Field(description=attr_desc, default=None)
                annotations[attr_name] = Optional[EntityText]  # Type annotation

            attrs["__annotations__"] = annotations

            # Dynamically create class
            entity_class = type(name, (EntityModel,), attrs)
            entity_class.__doc__ = description
            entity_types[name] = entity_class

        # Dynamically create edge types
        edge_definitions = {}
        for edge_def in ontology.get("edge_types", []):
            name = edge_def["name"]
            description = edge_def.get("description", f"A {name} relationship.")

            # Create attribute dict and type annotations
            attrs = {"__doc__": description}
            annotations = {}

            for attr_def in edge_def.get("attributes", []):
                attr_name = safe_attr_name(attr_def["name"])  # Use safe name
                attr_desc = attr_def.get("description", attr_name)
                # Zep API requires Field description, this is mandatory
                attrs[attr_name] = Field(description=attr_desc, default=None)
                annotations[attr_name] = Optional[str]  # Edge attributes use str type

            attrs["__annotations__"] = annotations

            # Dynamically create class
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            edge_class = type(class_name, (EdgeModel,), attrs)
            edge_class.__doc__ = description

            # Build source_targets
            source_targets = []
            for st in edge_def.get("source_targets", []):
                source_targets.append(
                    EntityEdgeSourceTarget(
                        source=st.get("source", "Entity"),
                        target=st.get("target", "Entity")
                    )
                )

            if source_targets:
                edge_definitions[name] = (edge_class, source_targets)

        # Call adapter to set ontology
        if entity_types or edge_definitions:
            self.client.set_ontology(
                graph_ids=[graph_id],
                entities=entity_types if entity_types else None,
                edges=edge_definitions if edge_definitions else None,
            )
    
    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = 3,
        progress_callback: Optional[Callable] = None
    ) -> List[str]:
        """Add text to graph in batches, return list of all episode UUIDs"""
        episode_uuids = []
        total_chunks = len(chunks)

        for i in range(0, total_chunks, batch_size):
            batch_chunks = chunks[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total_chunks + batch_size - 1) // batch_size

            if progress_callback:
                progress = (i + len(batch_chunks)) / total_chunks
                progress_callback(
                    f"Sending batch {batch_num}/{total_batches} ({len(batch_chunks)} chunks)...",
                    progress
                )

            # Build episode data (adapter format)
            episodes = [
                {"data": chunk, "type": "text"}
                for chunk in batch_chunks
            ]

            # Send to backend
            try:
                batch_uuids = self.client.add_episode_batch(
                    graph_id=graph_id,
                    episodes=episodes
                )
                episode_uuids.extend(batch_uuids)

                # Avoid sending requests too fast
                time.sleep(1)

            except Exception as e:
                if progress_callback:
                    progress_callback(f"Batch {batch_num} send failed: {str(e)}", 0)
                raise

        return episode_uuids
    
    def _wait_for_episodes(
        self,
        episode_uuids: List[str],
        progress_callback: Optional[Callable] = None,
        timeout: int = 600
    ):
        """Wait for all episodes to finish processing (by querying each episode's processed status)"""
        if not episode_uuids:
            if progress_callback:
                progress_callback("No waiting needed (no episodes)", 1.0)
            return

        # Graphiti processes synchronously, return directly
        if self._backend == 'graphiti':
            if progress_callback:
                progress_callback(f"Processing complete: {len(episode_uuids)}/{len(episode_uuids)}", 1.0)
            return

        start_time = time.time()
        pending_episodes = set(episode_uuids)
        completed_count = 0
        total_episodes = len(episode_uuids)

        if progress_callback:
            progress_callback(f"Starting to wait for {total_episodes} text chunks to process...", 0)

        while pending_episodes:
            if time.time() - start_time > timeout:
                if progress_callback:
                    progress_callback(
                        f"Some text chunks timed out, completed {completed_count}/{total_episodes}",
                        completed_count / total_episodes
                    )
                break

            # Check each episode's processing status
            for ep_uuid in list(pending_episodes):
                try:
                    status = self.client.get_episode_status(ep_uuid)
                    if status.processed:
                        pending_episodes.remove(ep_uuid)
                        completed_count += 1

                except Exception as e:
                    # Ignore individual query errors, continue
                    pass

            elapsed = int(time.time() - start_time)
            if progress_callback:
                progress_callback(
                    f"Processing... {completed_count}/{total_episodes} done, {len(pending_episodes)} pending ({elapsed}s)",
                    completed_count / total_episodes if total_episodes > 0 else 0
                )

            if pending_episodes:
                time.sleep(3)  # Check every 3 seconds

        if progress_callback:
            progress_callback(f"Processing complete: {completed_count}/{total_episodes}", 1.0)
    
    def _get_graph_info(self, graph_id: str) -> GraphInfo:
        """Get graph information"""
        # Use adapter to get nodes and edges
        nodes = self.client.get_all_nodes(graph_id)
        edges = self.client.get_all_edges(graph_id)

        # Collect entity types
        entity_types = set()
        for node in nodes:
            if node.labels:
                for label in node.labels:
                    if label not in ["Entity", "Node"]:
                        entity_types.add(label)

        return GraphInfo(
            graph_id=graph_id,
            node_count=len(nodes),
            edge_count=len(edges),
            entity_types=list(entity_types)
        )
    
    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """
        Get complete graph data (including detailed information)

        Args:
            graph_id: Graph ID

        Returns:
            Dict containing nodes and edges, including time info, attributes, and other detailed data
        """
        # Use adapter to get nodes and edges
        nodes = self.client.get_all_nodes(graph_id)
        edges = self.client.get_all_edges(graph_id)

        # Create node mapping for node name lookup
        node_map = {}
        for node in nodes:
            node_map[node.uuid] = node.name or ""

        nodes_data = []
        for node in nodes:
            nodes_data.append({
                "uuid": node.uuid,
                "name": node.name,
                "labels": node.labels or [],
                "summary": node.summary or "",
                "attributes": node.attributes or {},
                "created_at": node.created_at,
            })

        edges_data = []
        for edge in edges:
            edges_data.append({
                "uuid": edge.uuid,
                "name": edge.name or "",
                "fact": edge.fact or "",
                "fact_type": edge.name or "",
                "source_node_uuid": edge.source_node_uuid,
                "target_node_uuid": edge.target_node_uuid,
                "source_node_name": node_map.get(edge.source_node_uuid, ""),
                "target_node_name": node_map.get(edge.target_node_uuid, ""),
                "attributes": edge.attributes or {},
                "created_at": edge.created_at,
                "valid_at": edge.valid_at,
                "invalid_at": None,  # Not yet supported by adapter
                "expired_at": None,  # Not yet supported by adapter
                "episodes": edge.episodes or [],
            })

        return {
            "graph_id": graph_id,
            "nodes": nodes_data,
            "edges": edges_data,
            "node_count": len(nodes_data),
            "edge_count": len(edges_data),
        }
    
    def delete_graph(self, graph_id: str):
        """Delete graph"""
        self.client.delete_graph(graph_id)

