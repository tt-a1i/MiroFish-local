import pytest

from app.api.simulation import validate_max_rounds
from app.services.simulation_config_generator import SimulationConfigGenerator


@pytest.mark.parametrize("value", [8, "24", 72])
def test_validate_max_rounds_accepts_8_step_values(value):
    assert validate_max_rounds(value) == int(value)


@pytest.mark.parametrize("value", [0, 7, 10, 73, "abc"])
def test_validate_max_rounds_rejects_values_outside_8_to_72_or_not_8_step(value):
    with pytest.raises(ValueError):
        validate_max_rounds(value)


def test_parse_time_config_clamps_llm_time_to_72_hours_and_one_hour_rounds():
    generator = SimulationConfigGenerator(api_key="test-key")

    time_config = generator._parse_time_config({
        "total_simulation_hours": 168,
        "minutes_per_round": 30,
        "agents_per_hour_min": 3,
        "agents_per_hour_max": 8,
    }, num_entities=20)

    assert time_config.total_simulation_hours == 72
    assert time_config.minutes_per_round == 60
