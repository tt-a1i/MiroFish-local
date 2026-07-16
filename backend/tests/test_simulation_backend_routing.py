import json

from app import create_app
from app.api import simulation as simulation_api
from app.models.project import Project, ProjectStatus
from app.services.simulation_manager import SimulationState


def test_entities_uses_graph_backend_instead_of_global_cloud(monkeypatch):
    app = create_app()
    client = app.test_client()

    project = Project(
        project_id="proj_graphiti",
        name="graphiti project",
        status=ProjectStatus.GRAPH_COMPLETED,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        graph_id="graph_123",
        graph_backend="graphiti",
    )

    captured = {}

    monkeypatch.setattr(simulation_api.Config, "ZEP_BACKEND", "cloud")
    monkeypatch.setattr(simulation_api.Config, "ZEP_API_KEY", None)
    monkeypatch.setattr(
        simulation_api.ProjectManager,
        "get_project_by_graph_id",
        lambda graph_id: project if graph_id == "graph_123" else None,
    )

    class FakeReader:
        def __init__(self, backend=None):
            captured["backend"] = backend

        def filter_defined_entities(self, graph_id, defined_entity_types=None, enrich_with_edges=True):
            captured["graph_id"] = graph_id
            captured["entity_types"] = defined_entity_types
            captured["enrich"] = enrich_with_edges
            return type(
                "Result",
                (),
                {
                    "to_dict": lambda self: {
                        "entities": [],
                        "entity_types": [],
                        "total_count": 0,
                        "filtered_count": 0,
                    }
                },
            )()

    monkeypatch.setattr(simulation_api, "ZepEntityReader", FakeReader)

    response = client.get("/api/simulation/entities/graph_123")

    assert response.status_code == 200
    assert captured["backend"] == "graphiti"
    assert captured["graph_id"] == "graph_123"


def test_profiles_realtime_separates_expected_total_from_verified_count(tmp_path, monkeypatch):
    app = create_app()
    client = app.test_client()
    simulation_id = "sim_expected_total"
    sim_dir = tmp_path / simulation_id
    sim_dir.mkdir()

    (sim_dir / "state.json").write_text(
        json.dumps(
            {
                "status": "preparing",
                "entities_count": 41,
                "verification_candidate_count": 41,
                "verification_verified_count": 2,
                "verification_skipped_count": 39,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (sim_dir / "reddit_profiles.json").write_text(
        json.dumps(
            [
                {"username": "福建电视台第一帮帮团_610", "name": "福建电视台第一帮帮团"},
                {"username": "漳州市食品安全委员会办公室_183", "name": "漳州市食品安全委员会办公室"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(simulation_api.Config, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))

    def fake_get_simulation(self, requested_simulation_id):
        assert requested_simulation_id == simulation_id
        return SimulationState(
            simulation_id=simulation_id,
            project_id="proj_1",
            graph_id="graph_1",
            entities_count=41,
            verification_candidate_count=41,
            verification_verified_count=2,
            verification_skipped_count=39,
        )

    monkeypatch.setattr(simulation_api.SimulationManager, "get_simulation", fake_get_simulation)

    response = client.get(f"/api/simulation/{simulation_id}/profiles/realtime?platform=reddit")

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["count"] == 2
    assert data["total_expected"] == 41
    assert data["expected_agents_count"] == 41
    assert data["verified_count"] == 2


def test_delete_simulation_removes_history_record_and_files(tmp_path, monkeypatch):
    app = create_app()
    client = app.test_client()
    simulation_id = "sim_delete_me"
    sim_dir = tmp_path / simulation_id
    sim_dir.mkdir()
    (sim_dir / "state.json").write_text(
        json.dumps(
            {
                "simulation_id": simulation_id,
                "project_id": "proj_delete",
                "graph_id": "graph_delete",
                "status": "failed",
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
                "error": "历史失败记录",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (sim_dir / "simulation.log").write_text("failed log", encoding="utf-8")

    monkeypatch.setattr(simulation_api.SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path))

    response = client.delete(f"/api/simulation/{simulation_id}")

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["data"]["simulation_id"] == simulation_id
    assert not sim_dir.exists()

    list_response = client.get("/api/simulation/list?project_id=proj_delete")
    assert list_response.status_code == 200
    assert list_response.get_json()["data"] == []


def test_delete_missing_simulation_returns_404(tmp_path, monkeypatch):
    app = create_app()
    client = app.test_client()
    monkeypatch.setattr(simulation_api.SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path))

    response = client.delete("/api/simulation/sim_missing")

    assert response.status_code == 404
    body = response.get_json()
    assert body["success"] is False
    assert "模拟不存在" in body["error"]
