import json

import pytest

from app.models.project import Project, ProjectStatus
from app.services.agent_dialogue_service import (
    AgentDialogueError,
    AgentDialogueService,
    build_stable_agent_key,
    enrich_profile_for_dialogue,
)
from app.services.simulation_manager import SimulationManager, SimulationState, SimulationStatus


class FakeLLMClient:
    def __init__(self):
        self.messages = None

    def chat_stream(self, messages, temperature=0.7, max_tokens=2048):
        self.messages = messages
        yield "我是"
        yield "张雪"

    def chat(self, messages, temperature=0.7, max_tokens=2048):
        self.messages = messages
        return "我是张雪"


def make_project(project_id="proj_dialogue"):
    return Project(
        project_id=project_id,
        name="张雪机车事件",
        status=ProjectStatus.GRAPH_COMPLETED,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        graph_id="graph_1",
        graph_backend="graphiti",
        simulation_requirement="模拟张雪及相关机构在社交媒体上的舆论博弈演化路径",
        seed_summary_md="张雪机车事件的事件背景摘要。",
    )


def write_simulation(tmp_path, monkeypatch, profiles):
    monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path))
    manager = SimulationManager()
    state = SimulationState(
        simulation_id="sim_dialogue",
        project_id="proj_dialogue",
        graph_id="graph_1",
        graph_backend="graphiti",
        status=SimulationStatus.READY,
    )
    manager._save_simulation_state(state)

    sim_dir = tmp_path / "sim_dialogue"
    with (sim_dir / "reddit_profiles.json").open("w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False)

    return manager


def test_build_stable_agent_key_prefers_source_entity_uuid():
    profile = {
        "user_id": 3,
        "provenance": {"source_entity_uuid": "entity-zhangxue"},
    }

    assert build_stable_agent_key(profile, "sim_1", "reddit") == "entity_uuid:entity-zhangxue"


def test_enrich_profile_for_dialogue_marks_ready():
    profile = {
        "user_id": 0,
        "name": "张雪",
        "username": "zhangxue",
        "bio": "事件相关人物。",
        "persona": "以张雪身份讨论事件。",
        "provenance": {"source_entity_uuid": "entity-zhangxue"},
    }

    enriched = enrich_profile_for_dialogue(profile, "sim_1", "reddit", True)

    assert enriched["stable_agent_key"] == "entity_uuid:entity-zhangxue"
    assert enriched["source_entity_uuid"] == "entity-zhangxue"
    assert enriched["dialogue_ready"] is True


def test_resolve_agent_profile_rejects_mismatched_user_id(tmp_path, monkeypatch):
    write_simulation(
        tmp_path,
        monkeypatch,
        [
            {
                "user_id": 0,
                "name": "张雪",
                "username": "zhangxue",
                "bio": "事件相关人物。",
                "persona": "以张雪身份讨论事件。",
                "provenance": {"source_entity_uuid": "entity-zhangxue"},
            }
        ],
    )

    service = AgentDialogueService(llm_client=FakeLLMClient())

    with pytest.raises(AgentDialogueError, match="不一致"):
        service.resolve_agent_profile(
            simulation_id="sim_dialogue",
            agent_key="entity_uuid:entity-zhangxue",
            user_id=1,
            platform="reddit",
        )


def test_build_messages_contains_profile_and_step1_background(tmp_path, monkeypatch):
    project = make_project()
    monkeypatch.setattr("app.services.agent_dialogue_service.ProjectManager.get_project", lambda _: project)
    write_simulation(
        tmp_path,
        monkeypatch,
        [
            {
                "user_id": 0,
                "name": "张雪",
                "username": "zhangxue",
                "bio": "张雪是本事件相关人物。",
                "persona": "张雪会结合个人处境回应舆论。",
                "verification_status": "verified",
                "source_citations": [{"title": "公开来源", "url": "https://example.com"}],
                "provenance": {"source_entity_uuid": "entity-zhangxue"},
            }
        ],
    )
    fake_llm = FakeLLMClient()
    service = AgentDialogueService(llm_client=fake_llm)

    context = service.resolve_context(
        simulation_id="sim_dialogue",
        agent_key="entity_uuid:entity-zhangxue",
        user_id=0,
        platform="reddit",
    )
    messages = service.build_messages(context, "你怎么看后续舆论？", [])
    system_prompt = messages[0]["content"]

    assert "张雪" in system_prompt
    assert "张雪是本事件相关人物" in system_prompt
    assert project.simulation_requirement in system_prompt
    assert project.seed_summary_md in system_prompt
    assert "不得把自己说成其他同名人物" in system_prompt


def test_stream_chat_emits_meta_delta_done(tmp_path, monkeypatch):
    project = make_project()
    monkeypatch.setattr("app.services.agent_dialogue_service.ProjectManager.get_project", lambda _: project)
    write_simulation(
        tmp_path,
        monkeypatch,
        [
            {
                "user_id": 0,
                "name": "张雪",
                "username": "zhangxue",
                "bio": "张雪是本事件相关人物。",
                "persona": "张雪会结合个人处境回应舆论。",
                "provenance": {"source_entity_uuid": "entity-zhangxue"},
            }
        ],
    )
    service = AgentDialogueService(llm_client=FakeLLMClient())

    events = list(
        service.stream_chat(
            simulation_id="sim_dialogue",
            agent_key="entity_uuid:entity-zhangxue",
            user_id=0,
            platform="reddit",
            message="请回应一下",
        )
    )

    assert events[0]["event"] == "meta"
    assert events[0]["agent"]["name"] == "张雪"
    assert [event["event"] for event in events[1:]] == ["delta", "delta", "done"]
    assert "".join(event.get("content", "") for event in events) == "我是张雪"


def test_interview_agents_from_profiles_returns_batch_shape(tmp_path, monkeypatch):
    project = make_project()
    monkeypatch.setattr("app.services.agent_dialogue_service.ProjectManager.get_project", lambda _: project)
    write_simulation(
        tmp_path,
        monkeypatch,
        [
            {
                "user_id": 0,
                "name": "张雪",
                "username": "zhangxue",
                "bio": "张雪是本事件相关人物。",
                "persona": "张雪会结合个人处境回应舆论。",
                "provenance": {"source_entity_uuid": "entity-zhangxue"},
            }
        ],
    )
    service = AgentDialogueService(llm_client=FakeLLMClient())

    result = service.interview_agents_from_profiles(
        simulation_id="sim_dialogue",
        interviews=[{"agent_id": 0, "prompt": "你怎么看？"}],
        platform="reddit",
        fallback_reason="模拟环境未运行或已关闭",
    )

    assert result["success"] is True
    assert result["interviews_count"] == 1
    assert result["result"]["source"] == "profile_llm"
    assert result["result"]["fallback_reason"] == "模拟环境未运行或已关闭"
    assert result["result"]["results"]["reddit_0"]["response"] == "我是张雪"
    assert result["result"]["results"]["reddit_0"]["agent"]["name"] == "张雪"
