import importlib.util
import json
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT_DIR / "backend" / "scripts" / "action_logger.py"
SPEC = importlib.util.spec_from_file_location("action_logger_module", MODULE_PATH)
action_logger = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(action_logger)


def test_platform_logger_round_end_contains_simulated_hours(tmp_path):
    logger = action_logger.PlatformActionLogger("twitter", str(tmp_path))

    logger.log_round_end(round_num=4, actions_count=3, simulated_hours=2)

    log_path = tmp_path / "twitter" / "actions.jsonl"
    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["event_type"] == "round_end"
    assert entry["round"] == 4
    assert entry["actions_count"] == 3
    assert entry["simulated_hours"] == 2


def test_platform_logger_total_rounds_respects_minutes_per_round(tmp_path):
    logger = action_logger.PlatformActionLogger("reddit", str(tmp_path))

    logger.log_simulation_start(
        {
            "time_config": {
                "total_simulation_hours": 24,
                "minutes_per_round": 60,
            },
            "agent_configs": [{}, {}],
        }
    )

    log_path = tmp_path / "reddit" / "actions.jsonl"
    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["event_type"] == "simulation_start"
    assert entry["total_rounds"] == 24
    assert entry["agents_count"] == 2


def test_calculate_total_rounds_defaults_to_one_hour_per_round():
    assert action_logger.calculate_total_rounds({}) == 72
