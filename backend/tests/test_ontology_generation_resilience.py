from app import create_app
from app.config import Config
from app.models.project import ProjectManager
from app.services.ontology_generator import OntologyGenerator
from app.utils.llm_client import LLMClient, LLMRequestError


def test_ontology_generation_falls_back_from_boost_to_base(monkeypatch):
    monkeypatch.setattr(Config, "LLM_API_KEY", "base-key")
    monkeypatch.setattr(Config, "LLM_BASE_URL", "https://base.example/v1")
    monkeypatch.setattr(Config, "LLM_MODEL_NAME", "base-model")
    monkeypatch.setattr(Config, "LLM_BOOST_API_KEY", "boost-key")
    monkeypatch.setattr(Config, "LLM_BOOST_BASE_URL", "https://boost.example/v1")
    monkeypatch.setattr(Config, "LLM_BOOST_MODEL_NAME", "boost-model")

    calls = []

    def fake_chat_json(self, messages, temperature=0.3, max_tokens=4096):
        calls.append(self.route_name)
        if self.route_name == "boost":
            raise LLMRequestError(
                "LLM 认证失败，请检查 LLM_API_KEY 或 LLM_BOOST_API_KEY 配置",
                status_code=502,
                route_name=self.route_name,
            )
        return {
            "entity_types": [
                {"name": "MediaOutlet", "description": "Media org.", "attributes": [], "examples": []},
            ],
            "edge_types": [],
            "analysis_summary": "测试摘要",
        }

    monkeypatch.setattr(LLMClient, "chat_json", fake_chat_json)

    result = OntologyGenerator().generate(
        document_texts=["某媒体报道事件，当事人回应。"],
        simulation_requirement="模拟舆论扩散",
    )

    assert calls == ["boost", "base"]
    assert result["analysis_summary"] == "测试摘要"
    assert [entity["name"] for entity in result["entity_types"]][-2:] == ["Person", "Organization"]


def test_ontology_generate_returns_clean_llm_error(monkeypatch, tmp_path):
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    project = ProjectManager.create_project(name="测试项目")
    ProjectManager.save_extracted_text(project.project_id, "测试事件材料")

    def fake_generate(self, document_texts, simulation_requirement, additional_context=None):
        raise LLMRequestError(
            "LLM 认证失败，请检查 LLM_API_KEY 或 LLM_BOOST_API_KEY 配置",
            status_code=502,
            retryable=False,
            route_name="boost",
        )

    monkeypatch.setattr(OntologyGenerator, "generate", fake_generate)
    app = create_app()
    client = app.test_client()

    response = client.post(
        "/api/graph/ontology/generate",
        json={
            "project_id": project.project_id,
            "simulation_requirement": "模拟舆论扩散",
        },
    )

    data = response.get_json()
    assert response.status_code == 502
    assert data == {
        "success": False,
        "error": "LLM 认证失败，请检查 LLM_API_KEY 或 LLM_BOOST_API_KEY 配置",
        "error_code": "llm_request_failed",
        "retryable": False,
    }
