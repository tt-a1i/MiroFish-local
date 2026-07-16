import json

from app.services.graph_builder import GraphBuilderService
from app.services.zep_adapter import GraphEdge, GraphNode


def test_graph_data_keeps_existing_simulation_memory_node_color_and_grays_new_node(
    tmp_path, monkeypatch
):
    """推演前已有节点只增加标签，推演中新建节点才使用灰色展示类型。"""
    simulation_dir = tmp_path / "sim-1"
    simulation_dir.mkdir()
    (simulation_dir / "graph_memory_baseline.json").write_text(
        json.dumps(
            {
                "graph_id": "graph-1",
                "captured_at": "2026-06-23T01:00:00+00:00",
                "node_uuids": ["existing-node"],
            }
        ),
        encoding="utf-8",
    )
    (simulation_dir / "graph_memory_outbox.json").write_text(
        json.dumps(
            {
                "activity-1": {
                    "graph_id": "graph-1",
                    "status": "sent",
                    "episode_uuid": "simulation-episode",
                    "sent_at": "2026-06-23T01:01:00+00:00",
                    "activity": {"episode_text": "既有主体讨论新增话题"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.graph_builder.Config.OASIS_SIMULATION_DATA_DIR", str(tmp_path)
    )

    class FakeClient:
        def get_all_nodes(self, graph_id):
            return [
                GraphNode(
                    "existing-node",
                    "既有主体",
                    ["Entity", "Person"],
                    "推演前已存在",
                    {},
                    "2026-06-23T00:30:00+00:00",
                ),
                GraphNode(
                    "new-node",
                    "新增话题",
                    ["Entity", "Topic"],
                    "由推演写入",
                    {},
                    "2026-06-23T01:02:00+00:00",
                ),
            ]

        def get_all_edges(self, graph_id):
            return [
                GraphEdge(
                    "simulation-edge",
                    "讨论",
                    "既有主体讨论新增话题",
                    "existing-node",
                    "new-node",
                    {},
                    episodes=["simulation-episode"],
                )
            ]

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder.client = FakeClient()

    graph_data = builder.get_graph_data("graph-1")
    existing_node = next(node for node in graph_data["nodes"] if node["uuid"] == "existing-node")
    new_node = next(node for node in graph_data["nodes"] if node["uuid"] == "new-node")

    assert existing_node["is_simulation_memory"] is True
    assert existing_node["is_new_simulation_memory"] is False
    assert "FutureSimulationMemory" in existing_node["display_labels"]
    assert existing_node["display_type"] == "Person"

    assert new_node["is_simulation_memory"] is True
    assert new_node["is_new_simulation_memory"] is True
    assert "FutureSimulationMemory" in new_node["display_labels"]
    assert new_node["display_type"] == "FutureSimulationMemory"


def test_simulation_start_captures_graph_node_baseline(tmp_path, monkeypatch):
    """Step 3 启动前必须持久化当时已存在的节点 UUID。"""
    from app.services.simulation_runner import SimulationRunner

    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))

    class FakeClient:
        def get_all_nodes(self, graph_id):
            assert graph_id == "graph-1"
            return [
                GraphNode("existing-b", "乙", ["Entity", "Person"], "", {}),
                GraphNode("existing-a", "甲", ["Entity", "Person"], "", {}),
            ]

    monkeypatch.setattr(
        "app.services.simulation_runner.get_zep_client", lambda backend=None: FakeClient()
    )

    assert SimulationRunner._capture_graph_memory_baseline(
        "sim-1", "graph-1", backend="graphiti"
    ) is True

    baseline = json.loads(
        (tmp_path / "sim-1" / "graph_memory_baseline.json").read_text(encoding="utf-8")
    )
    assert baseline["graph_id"] == "graph-1"
    assert baseline["node_uuids"] == ["existing-a", "existing-b"]
    assert baseline["captured_at"]


def test_graph_data_uses_timestamps_when_historical_simulation_has_no_baseline(
    tmp_path, monkeypatch
):
    """旧推演缺少快照时，仍不能将明确早于写回的节点置灰。"""
    simulation_dir = tmp_path / "legacy-sim"
    simulation_dir.mkdir()
    (simulation_dir / "graph_memory_outbox.json").write_text(
        json.dumps(
            {
                "activity-1": {
                    "graph_id": "graph-1",
                    "status": "sent",
                    "episode_uuid": "legacy-episode",
                    "sent_at": "2026-06-23T01:01:00+00:00",
                    "activity": {"episode_text": "旧节点和新节点的关系"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.graph_builder.Config.OASIS_SIMULATION_DATA_DIR", str(tmp_path)
    )

    class FakeClient:
        def get_all_nodes(self, graph_id):
            return [
                GraphNode(
                    "old-node", "旧节点", ["Entity", "Person"], "", {},
                    "2026-06-23T00:30:00+00:00",
                ),
                GraphNode(
                    "new-node", "新节点", ["Entity", "Topic"], "", {},
                    "2026-06-23T01:02:00+00:00",
                ),
            ]

        def get_all_edges(self, graph_id):
            return [
                GraphEdge(
                    "legacy-edge", "关联", "旧节点和新节点的关系", "old-node", "new-node", {},
                    episodes=["legacy-episode"],
                )
            ]

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder.client = FakeClient()

    nodes = {node["uuid"]: node for node in builder.get_graph_data("graph-1")["nodes"]}

    assert nodes["old-node"]["is_new_simulation_memory"] is False
    assert nodes["old-node"]["display_type"] == "Person"
    assert nodes["new-node"]["is_new_simulation_memory"] is True
    assert nodes["new-node"]["display_type"] == "FutureSimulationMemory"
