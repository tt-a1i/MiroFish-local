import json

from app import create_app
from app.api import simulation as simulation_api


class FakeAgentDialogueService:
    def stream_chat(self, **kwargs):
        yield {
            "event": "meta",
            "agent": {
                "user_id": kwargs["user_id"],
                "name": "张雪",
                "stable_agent_key": kwargs["agent_key"],
            },
        }
        yield {"event": "delta", "content": "你好"}
        yield {"event": "done"}

    def interview_agents_from_profiles(self, **kwargs):
        return {
            "success": True,
            "interviews_count": len(kwargs["interviews"]),
            "result": {
                "interviews_count": len(kwargs["interviews"]),
                "source": "profile_llm",
                "fallback_reason": kwargs.get("fallback_reason", ""),
                "results": {
                    "reddit_0": {
                        "agent_id": 0,
                        "response": "我是张雪",
                        "answer": "我是张雪",
                        "platform": "reddit",
                        "source": "profile_llm",
                    }
                },
            },
            "timestamp": "2026-01-01T00:00:00",
        }

    def chat(self, **kwargs):
        return {
            "response": "你好",
            "agent": {
                "user_id": kwargs["user_id"],
                "name": "张雪",
                "stable_agent_key": kwargs["agent_key"],
            },
        }


def test_agent_chat_stream_returns_ndjson(monkeypatch):
    monkeypatch.setattr(simulation_api, "AgentDialogueService", lambda: FakeAgentDialogueService())
    app = create_app()
    client = app.test_client()

    response = client.post(
        "/api/simulation/sim_1/agent-chat/stream",
        json={
            "agent_key": "entity_uuid:entity-zhangxue",
            "user_id": 0,
            "platform": "reddit",
            "message": "你好",
        },
    )

    assert response.status_code == 200
    assert response.content_type == "application/x-ndjson; charset=utf-8"
    assert response.headers["Cache-Control"] == "no-cache"
    assert response.headers["X-Accel-Buffering"] == "no"
    lines = [json.loads(line) for line in response.data.decode("utf-8").strip().splitlines()]
    assert [line["event"] for line in lines] == ["meta", "delta", "done"]
    assert lines[0]["agent"]["name"] == "张雪"
    assert lines[1]["content"] == "你好"


def test_agent_chat_non_stream_returns_json(monkeypatch):
    monkeypatch.setattr(simulation_api, "AgentDialogueService", lambda: FakeAgentDialogueService())
    app = create_app()
    client = app.test_client()

    response = client.post(
        "/api/simulation/sim_1/agent-chat",
        json={
            "agent_key": "entity_uuid:entity-zhangxue",
            "user_id": 0,
            "platform": "reddit",
            "message": "你好",
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["data"]["agent"]["name"] == "张雪"
    assert data["data"]["response"] == "你好"


def test_batch_interview_falls_back_to_profile_survey_when_env_stopped(monkeypatch):
    monkeypatch.setattr(simulation_api.SimulationRunner, "check_env_alive", lambda _: False)
    monkeypatch.setattr(simulation_api, "AgentDialogueService", lambda: FakeAgentDialogueService())
    app = create_app()
    client = app.test_client()

    response = client.post(
        "/api/simulation/interview/batch",
        json={
            "simulation_id": "sim_1",
            "interviews": [{"agent_id": 0, "prompt": "你怎么看？"}],
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["data"]["result"]["source"] == "profile_llm"
    assert data["data"]["result"]["fallback_reason"] == "模拟环境未运行或已关闭"
    assert data["data"]["result"]["results"]["reddit_0"]["response"] == "我是张雪"


def test_batch_interview_falls_back_when_oasis_ipc_fails(monkeypatch):
    monkeypatch.setattr(simulation_api.SimulationRunner, "check_env_alive", lambda _: True)

    def fail_batch(**kwargs):
        raise TimeoutError("等待命令响应超时")

    monkeypatch.setattr(simulation_api.SimulationRunner, "interview_agents_batch", fail_batch)
    monkeypatch.setattr(simulation_api, "AgentDialogueService", lambda: FakeAgentDialogueService())
    app = create_app()
    client = app.test_client()

    response = client.post(
        "/api/simulation/interview/batch",
        json={
            "simulation_id": "sim_1",
            "interviews": [{"agent_id": 0, "prompt": "你怎么看？"}],
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["data"]["result"]["source"] == "profile_llm"
    assert data["data"]["result"]["fallback_reason"] == "等待命令响应超时"
