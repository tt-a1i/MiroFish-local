import json

from app import create_app
from app.api import simulation as simulation_api
from app.services.simulation_manager import SimulationState, SimulationStatus


class FakeSimulationManager:
    def __init__(self):
        self.state = SimulationState(
            simulation_id="sim_replay_api",
            project_id="proj_1",
            graph_id="graph_1",
            graph_backend="graphiti",
            status=SimulationStatus.COMPLETED,
        )

    def get_simulation(self, simulation_id):
        return self.state if simulation_id == self.state.simulation_id else None

    def _save_simulation_state(self, state):
        self.state = state


class FakeUpdater:
    def __init__(self, graph_id, backend=None, simulation_id=None):
        self.graph_id = graph_id
        self.backend = backend
        self.simulation_id = simulation_id

    def replay_failed_outbox(self, statuses=None, limit=None):
        return {
            "simulation_id": self.simulation_id,
            "graph_id": self.graph_id,
            "backend": self.backend,
            "statuses": statuses,
            "hydrated_count": 1,
            "attempted": limit,
            "sent": limit,
            "failed": 0,
            "skipped": 0,
            "missing_payload": 0,
        }


def test_replay_graph_memory_outbox_api(monkeypatch):
    app = create_app()
    client = app.test_client()

    monkeypatch.setattr(simulation_api, "SimulationManager", FakeSimulationManager)
    monkeypatch.setattr(simulation_api, "ZepGraphMemoryUpdater", FakeUpdater)

    response = client.post(
        "/api/simulation/sim_replay_api/graph-memory/replay",
        json={"statuses": ["failed"], "limit": 2},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"] == {
        "simulation_id": "sim_replay_api",
        "graph_id": "graph_1",
        "backend": "graphiti",
        "statuses": ["failed"],
        "hydrated_count": 1,
        "attempted": 2,
        "sent": 2,
        "failed": 0,
        "skipped": 0,
        "missing_payload": 0,
    }


def test_replay_graph_memory_outbox_api_defaults_to_failed_and_blocked(monkeypatch):
    app = create_app()
    client = app.test_client()

    monkeypatch.setattr(simulation_api, "SimulationManager", FakeSimulationManager)
    monkeypatch.setattr(simulation_api, "ZepGraphMemoryUpdater", FakeUpdater)

    response = client.post(
        "/api/simulation/sim_replay_api/graph-memory/replay",
        json={},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["statuses"] == ["failed", "blocked"]


def test_replay_graph_memory_outbox_api_validates_statuses():
    app = create_app()
    client = app.test_client()

    response = client.post(
        "/api/simulation/sim_replay_api/graph-memory/replay",
        data=json.dumps({"statuses": "failed"}),
        content_type="application/json",
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "statuses" in payload["error"]
