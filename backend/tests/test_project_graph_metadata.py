from app.config import Config
from app.models.project import Project, ProjectStatus


def test_project_graph_metadata_roundtrip():
    project = Project(
        project_id="proj_test",
        name="test",
        status=ProjectStatus.CREATED,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        graph_id="mirofish_test",
        graph_backend="graphiti",
        graph_provider="graphiti",
        graph_schema_version="v1",
    )

    data = project.to_dict()
    restored = Project.from_dict(data)

    assert restored.graph_id == "mirofish_test"
    assert restored.graph_backend == "graphiti"
    assert restored.graph_provider == "graphiti"
    assert restored.graph_schema_version == "v1"


def test_project_from_dict_backward_compatible():
    restored = Project.from_dict(
        {
            "project_id": "proj_old",
            "name": "old",
            "status": "created",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "graph_id": "mirofish_old",
        }
    )

    assert restored.graph_id == "mirofish_old"
    assert restored.graph_backend is None
    assert restored.graph_provider is None
    assert restored.graph_schema_version is None
    assert restored.chunk_size == Config.DEFAULT_CHUNK_SIZE
    assert restored.chunk_overlap == Config.DEFAULT_CHUNK_OVERLAP
