import json
from datetime import datetime, timezone

from app.services.zep_graph_memory_updater import AgentActivity, ZepGraphMemoryUpdater


class FakeClient:
    def __init__(self, failures_before_success=0):
        self.failures_before_success = failures_before_success
        self.calls = []

    def add_episode(self, graph_id, data, episode_type="text", reference_time=None):
        self.calls.append(
            {
                "graph_id": graph_id,
                "data": data,
                "episode_type": episode_type,
                "reference_time": reference_time,
            }
        )
        if self.failures_before_success > 0:
            self.failures_before_success -= 1
            raise RuntimeError("temporary failure")
        return "episode_1"


class FakeNeo4jAuthError(Exception):
    code = "Neo.ClientError.Security.Unauthorized"


def test_send_single_activity_retries_and_preserves_reference_time(monkeypatch):
    fake_client = FakeClient(failures_before_success=1)
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.get_zep_client",
        lambda backend=None: fake_client,
    )
    monkeypatch.setattr("app.services.zep_graph_memory_updater.time.sleep", lambda _: None)

    updater = ZepGraphMemoryUpdater("graph_1", backend="graphiti")
    activity = AgentActivity(
        platform="twitter",
        agent_id=1,
        agent_name="Alice",
        action_type="CREATE_POST",
        action_args={"content": "hello"},
        round_num=1,
        timestamp="2026-01-02T03:04:05Z",
    )

    success = updater._send_single_activity(activity)

    assert success is True
    assert len(fake_client.calls) == 2
    first_time = fake_client.calls[0]["reference_time"]
    second_time = fake_client.calls[1]["reference_time"]
    assert first_time == second_time
    assert first_time == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_neo4j_auth_error_blocks_retries_and_following_activities(tmp_path, monkeypatch):
    class AuthFailingClient:
        def __init__(self):
            self.calls = []

        def add_episode(self, graph_id, data, episode_type="text", reference_time=None):
            self.calls.append(
                {
                    "graph_id": graph_id,
                    "data": data,
                    "episode_type": episode_type,
                    "reference_time": reference_time,
                }
            )
            raise FakeNeo4jAuthError("authentication failure")

    fake_client = AuthFailingClient()
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.get_zep_client",
        lambda backend=None: fake_client,
    )
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.SIMULATION_DATA_DIR",
        str(tmp_path),
    )
    monkeypatch.setattr("app.services.zep_graph_memory_updater.time.sleep", lambda _: None)

    updater = ZepGraphMemoryUpdater("graph_auth", backend="graphiti", simulation_id="sim_auth")
    first = AgentActivity(
        platform="twitter",
        agent_id=1,
        agent_name="Alice",
        action_type="CREATE_POST",
        action_args={"content": "hello"},
        round_num=1,
        timestamp="2026-01-02T03:04:05Z",
    )
    second = AgentActivity(
        platform="twitter",
        agent_id=2,
        agent_name="Bob",
        action_type="CREATE_POST",
        action_args={"content": "blocked"},
        round_num=1,
        timestamp="2026-01-02T03:04:06Z",
    )

    assert updater._send_single_activity(first) is False
    assert updater._send_single_activity(second) is False
    assert len(fake_client.calls) == 1

    outbox_path = tmp_path / "sim_auth" / "graph_memory_outbox.json"
    saved = json.loads(outbox_path.read_text(encoding="utf-8"))
    assert saved[first.build_activity_id()]["status"] == "blocked"
    assert saved[first.build_activity_id()]["attempt_count"] == 1
    assert "Neo4j 认证失败" in saved[first.build_activity_id()]["last_error"]
    assert saved[second.build_activity_id()]["status"] == "blocked"
    assert "Neo4j 认证失败" in saved[second.build_activity_id()]["last_error"]


def test_send_batch_activities_tracks_partial_success(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.get_zep_client",
        lambda backend=None: fake_client,
    )

    updater = ZepGraphMemoryUpdater("graph_2", backend="graphiti")

    results = iter([True, False, True])
    monkeypatch.setattr(updater, "_send_single_activity", lambda activity: next(results))

    activities = [
        AgentActivity("twitter", 1, "Alice", "CREATE_POST", {"content": "a"}, 1, "2026-01-01T00:00:00Z"),
        AgentActivity("twitter", 2, "Bob", "CREATE_POST", {"content": "b"}, 1, "2026-01-01T00:01:00Z"),
        AgentActivity("twitter", 3, "Carol", "CREATE_POST", {"content": "c"}, 1, "2026-01-01T00:02:00Z"),
    ]

    updater._send_batch_activities(activities, "twitter")

    assert updater._total_sent == 1
    assert updater._total_items_sent == 2
    assert updater._failed_count == 1


def test_graphiti_activity_uses_json_episode_and_persists_outbox(tmp_path, monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.get_zep_client",
        lambda backend=None: fake_client,
    )
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.SIMULATION_DATA_DIR",
        str(tmp_path),
    )

    updater = ZepGraphMemoryUpdater("graph_json", backend="graphiti", simulation_id="sim_json")
    activity = AgentActivity(
        platform="reddit",
        agent_id=7,
        agent_name="Bob",
        action_type="SEARCH_POSTS",
        action_args={"query": "graphiti"},
        round_num=2,
        timestamp="2026-02-03T04:05:06Z",
    )

    success = updater._send_single_activity(activity)

    assert success is True
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["episode_type"] == "json"
    payload = json.loads(call["data"])
    assert payload["activity_id"] == activity.build_activity_id()
    assert payload["action_type"] == "SEARCH_POSTS"

    outbox_path = tmp_path / "sim_json" / "graph_memory_outbox.json"
    saved = json.loads(outbox_path.read_text(encoding="utf-8"))
    record = saved[activity.build_activity_id()]
    assert record["status"] == "sent"
    assert record["episode_type"] == "json"
    assert record["activity"]["action_args"] == {"query": "graphiti"}
    assert record["agent_id"] == 7
    assert record["round_num"] == 2


def test_default_backend_uses_configured_graphiti_json_episode(tmp_path, monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.get_zep_client",
        lambda backend=None: fake_client,
    )
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.SIMULATION_DATA_DIR",
        str(tmp_path),
    )
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.Config",
        type("ConfigStub", (), {"ZEP_BACKEND": "graphiti"}),
    )

    updater = ZepGraphMemoryUpdater("graph_default", simulation_id="sim_default")
    activity = AgentActivity(
        platform="twitter",
        agent_id=11,
        agent_name="Frank",
        action_type="CREATE_POST",
        action_args={"content": "default backend"},
        round_num=3,
        timestamp="2026-05-06T07:08:09Z",
    )

    assert updater.backend == "graphiti"
    assert updater._send_single_activity(activity) is True
    assert len(fake_client.calls) == 1
    assert fake_client.calls[0]["episode_type"] == "json"
    payload = json.loads(fake_client.calls[0]["data"])
    assert payload["activity_id"] == activity.build_activity_id()


def test_duplicate_activity_is_skipped_by_outbox(tmp_path, monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.get_zep_client",
        lambda backend=None: fake_client,
    )
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.SIMULATION_DATA_DIR",
        str(tmp_path),
    )

    updater = ZepGraphMemoryUpdater("graph_dup", backend="graphiti", simulation_id="sim_dup")
    activity = AgentActivity(
        platform="twitter",
        agent_id=3,
        agent_name="Carol",
        action_type="CREATE_POST",
        action_args={"content": "same"},
        round_num=1,
        timestamp="2026-03-04T05:06:07Z",
    )

    assert updater._send_single_activity(activity) is True
    assert updater._send_single_activity(activity) is True
    assert len(fake_client.calls) == 1


def test_cloud_backend_keeps_text_episode(tmp_path, monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.get_zep_client",
        lambda backend=None: fake_client,
    )
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.SIMULATION_DATA_DIR",
        str(tmp_path),
    )

    updater = ZepGraphMemoryUpdater("graph_cloud", backend="cloud", simulation_id="sim_cloud")
    activity = AgentActivity(
        platform="twitter",
        agent_id=5,
        agent_name="Dora",
        action_type="FOLLOW",
        action_args={"target_user_name": "Neo4j"},
        round_num=4,
        timestamp="2026-04-05T06:07:08Z",
    )

    assert updater._send_single_activity(activity) is True
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["episode_type"] == "text"
    assert isinstance(call["data"], str)
    assert "关注了用户" in call["data"]


def test_invalid_timestamp_fails_instead_of_falling_back_to_now(tmp_path, monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.get_zep_client",
        lambda backend=None: fake_client,
    )
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.SIMULATION_DATA_DIR",
        str(tmp_path),
    )

    updater = ZepGraphMemoryUpdater("graph_invalid", backend="graphiti", simulation_id="sim_invalid")
    activity = AgentActivity(
        platform="twitter",
        agent_id=9,
        agent_name="Eve",
        action_type="CREATE_POST",
        action_args={"content": "bad time"},
        round_num=1,
        timestamp="not-a-time",
    )

    assert updater._send_single_activity(activity) is False
    assert fake_client.calls == []

    outbox_path = tmp_path / "sim_invalid" / "graph_memory_outbox.json"
    saved = json.loads(outbox_path.read_text(encoding="utf-8"))
    record = saved[activity.build_activity_id()]
    assert record["status"] == "failed"
    assert "isoformat" in record["last_error"] or "timestamp" in record["last_error"]


def test_replay_failed_outbox_rehydrates_legacy_record_from_actions(tmp_path, monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.get_zep_client",
        lambda backend=None: fake_client,
    )
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.SIMULATION_DATA_DIR",
        str(tmp_path),
    )

    sim_dir = tmp_path / "sim_replay"
    reddit_dir = sim_dir / "reddit"
    reddit_dir.mkdir(parents=True)

    activity = AgentActivity(
        platform="reddit",
        agent_id=8,
        agent_name="Bob",
        action_type="CREATE_COMMENT",
        action_args={"content": "reply"},
        round_num=4,
        timestamp="2026-06-01T02:03:04Z",
    )
    action_record = {
        "round": activity.round_num,
        "timestamp": activity.timestamp,
        "agent_id": activity.agent_id,
        "agent_name": activity.agent_name,
        "action_type": activity.action_type,
        "action_args": activity.action_args,
        "success": True,
    }
    (reddit_dir / "actions.jsonl").write_text(
        json.dumps(action_record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    outbox_path = sim_dir / "graph_memory_outbox.json"
    outbox_path.write_text(
        json.dumps(
            {
                activity.build_activity_id(): {
                    "status": "failed",
                    "platform": "reddit",
                    "action_type": "CREATE_COMMENT",
                    "agent_name": "Bob",
                    "timestamp": activity.timestamp,
                    "last_error": "Connection error.",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    updater = ZepGraphMemoryUpdater("graph_replay", backend="graphiti", simulation_id="sim_replay")
    stats = updater.replay_failed_outbox()

    assert stats["hydrated_count"] == 1
    assert stats["attempted"] == 1
    assert stats["sent"] == 1
    assert stats["failed"] == 0
    assert len(fake_client.calls) == 1
    payload = json.loads(fake_client.calls[0]["data"])
    assert payload["action_args"] == {"content": "reply"}

    saved = json.loads(outbox_path.read_text(encoding="utf-8"))
    record = saved[activity.build_activity_id()]
    assert record["status"] == "sent"
    assert record["activity"]["action_args"] == {"content": "reply"}


def test_replay_failed_outbox_reports_missing_payload(tmp_path, monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.get_zep_client",
        lambda backend=None: fake_client,
    )
    monkeypatch.setattr(
        "app.services.zep_graph_memory_updater.SIMULATION_DATA_DIR",
        str(tmp_path),
    )

    sim_dir = tmp_path / "sim_missing_payload"
    sim_dir.mkdir(parents=True)
    outbox_path = sim_dir / "graph_memory_outbox.json"
    outbox_path.write_text(
        json.dumps({"activity_1": {"status": "failed", "last_error": "Connection error."}}),
        encoding="utf-8",
    )

    updater = ZepGraphMemoryUpdater(
        "graph_missing_payload",
        backend="graphiti",
        simulation_id="sim_missing_payload",
    )
    stats = updater.replay_failed_outbox()

    assert stats["attempted"] == 0
    assert stats["missing_payload"] == 1
    assert fake_client.calls == []
