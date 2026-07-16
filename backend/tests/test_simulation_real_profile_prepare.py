from app.services import simulation_manager as manager_module
from app.services.real_entity_resolver import ResolvedRealEntity, UNSUPPORTED, VERIFIED
from app.services.simulation_manager import SimulationManager, SimulationState, SimulationStatus
from app.services.simulation_config_generator import SimulationParameters
from app.services.zep_entity_reader import EntityNode, FilteredEntities


def test_prepare_fails_in_strict_mode_when_real_verification_has_zero_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path))

    entity = EntityNode(
        uuid="person-1",
        name="Alice Example",
        labels=["Entity", "Person"],
        summary="A candidate person with enough graph context for verification.",
        attributes={},
    )

    class FakeReader:
        def __init__(self, backend=None):
            pass

        def filter_defined_entities(self, graph_id, defined_entity_types=None, enrich_with_edges=True):
            return FilteredEntities(
                entities=[entity],
                entity_types={"Person"},
                total_count=1,
                filtered_count=1,
            )

    resolver_init_args = {}

    class FakeResolver:
        def __init__(self, min_source_count=1, allow_group_agents=True, concurrency=1):
            resolver_init_args["min_source_count"] = min_source_count
            resolver_init_args["allow_group_agents"] = allow_group_agents
            resolver_init_args["concurrency"] = concurrency

        def resolve_entities(self, entities):
            return [
                ResolvedRealEntity(
                    entity_uuid=entities[0].uuid,
                    entity_name=entities[0].name,
                    entity_type="Person",
                    verification_status=UNSUPPORTED,
                    skip_reason="测试：无可验证来源",
                )
            ]

    monkeypatch.setattr(manager_module, "ZepEntityReader", FakeReader)
    monkeypatch.setattr(manager_module, "RealEntityResolver", FakeResolver)

    class FakeProfileGenerator:
        def __init__(self, *args, **kwargs):
            raise AssertionError("严格真实模式没有 verified 实体时不应生成人设")

    monkeypatch.setattr(manager_module, "OasisProfileGenerator", FakeProfileGenerator)

    manager = SimulationManager()
    state = SimulationState(
        simulation_id="sim_zero_verified",
        project_id="proj_1",
        graph_id="graph_1",
        graph_backend="graphiti",
        status=SimulationStatus.CREATED,
    )
    manager._save_simulation_state(state)

    result = manager.prepare_simulation(
        simulation_id="sim_zero_verified",
        simulation_requirement="测试真实画像",
        document_text="测试文档",
        use_real_profiles=True,
        strict_real_mode=True,
    )

    assert result.status == SimulationStatus.FAILED
    assert resolver_init_args == {
        "min_source_count": 1,
        "allow_group_agents": True,
        "concurrency": manager_module.Config.REAL_ENTITY_RESOLVE_CONCURRENCY,
    }
    assert result.verification_candidate_count == 1
    assert result.verification_verified_count == 0
    assert result.verification_skipped_count == 1
    assert "严格真实模式" in result.error
    assert result.profiles_count == 0


def test_prepare_non_strict_mode_generates_profiles_for_unverified_entities(tmp_path, monkeypatch):
    monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path))

    first_entity = EntityNode(
        uuid="person-1",
        name="张雪",
        labels=["Entity", "Founder"],
        summary="张雪是张雪机车创始人，与820RR召回事件高度相关。",
        attributes={},
    )
    second_entity = EntityNode(
        uuid="company-1",
        name="张雪机车",
        labels=["Entity", "MotorcycleCompany"],
        summary="张雪机车是820RR曲轴箱破裂事件中的品牌主体。",
        attributes={},
    )

    class FakeReader:
        def __init__(self, backend=None):
            pass

        def filter_defined_entities(self, graph_id, defined_entity_types=None, enrich_with_edges=True):
            return FilteredEntities(
                entities=[first_entity, second_entity],
                entity_types={"Founder", "MotorcycleCompany"},
                total_count=2,
                filtered_count=2,
            )

    class FakeResolver:
        def __init__(self, min_source_count=1, allow_group_agents=True, concurrency=1):
            pass

        def resolve_entities(self, entities):
            return [
                ResolvedRealEntity(
                    entity_uuid=entity.uuid,
                    entity_name=entity.name,
                    entity_type=entity.get_entity_type(),
                    verification_status=UNSUPPORTED,
                    skip_reason="测试：未找到可引用来源",
                )
                for entity in entities
            ]

    captured = {}

    class FakeProfileGenerator:
        def __init__(self, *args, **kwargs):
            pass

        def generate_profiles_from_entities(self, **kwargs):
            captured["entities"] = kwargs["entities"]
            captured["strict_real_mode"] = kwargs["strict_real_mode"]
            captured["resolved_real_entities"] = kwargs["resolved_real_entities"]
            return [
                manager_module.OasisAgentProfile(
                    user_id=idx,
                    user_name=f"agent_{idx}",
                    name=entity.name,
                    bio=entity.summary,
                    persona=entity.summary,
                    source_entity_uuid=entity.uuid,
                    source_entity_type=entity.get_entity_type(),
                    verification_status=UNSUPPORTED,
                )
                for idx, entity in enumerate(kwargs["entities"])
            ]

        def save_profiles(self, profiles, file_path, platform="reddit"):
            pass

    class FakeConfigGenerator:
        def generate_config(self, **kwargs):
            captured["config_entities"] = kwargs["entities"]
            return SimulationParameters(
                simulation_id=kwargs["simulation_id"],
                project_id=kwargs["project_id"],
                graph_id=kwargs["graph_id"],
                simulation_requirement=kwargs["simulation_requirement"],
            )

    monkeypatch.setattr(manager_module, "ZepEntityReader", FakeReader)
    monkeypatch.setattr(manager_module, "RealEntityResolver", FakeResolver)
    monkeypatch.setattr(manager_module, "OasisProfileGenerator", FakeProfileGenerator)
    monkeypatch.setattr(manager_module, "SimulationConfigGenerator", FakeConfigGenerator)

    manager = SimulationManager()
    state = SimulationState(
        simulation_id="sim_non_strict_unverified",
        project_id="proj_1",
        graph_id="graph_1",
        graph_backend="graphiti",
        status=SimulationStatus.CREATED,
    )
    manager._save_simulation_state(state)

    result = manager.prepare_simulation(
        simulation_id="sim_non_strict_unverified",
        simulation_requirement="测试非严格真实画像",
        document_text="测试文档",
        use_real_profiles=True,
        strict_real_mode=False,
    )

    assert result.status == SimulationStatus.READY
    assert [entity.uuid for entity in captured["entities"]] == [first_entity.uuid, second_entity.uuid]
    assert [entity.uuid for entity in captured["config_entities"]] == [first_entity.uuid, second_entity.uuid]
    assert captured["strict_real_mode"] is False
    assert captured["resolved_real_entities"][first_entity.uuid].verification_status == UNSUPPORTED
    assert result.entities_count == 2
    assert result.profiles_count == 2
    assert result.verification_candidate_count == 2
    assert result.verification_verified_count == 0
    assert result.verification_skipped_count == 2


def test_prepare_strict_mode_only_generates_verified_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path))

    verified_entity = EntityNode(
        uuid="person-verified",
        name="许国利",
        labels=["Entity", "Person"],
        summary="许国利是杭州杀妻案中的被告人。",
        attributes={},
    )
    skipped_entity = EntityNode(
        uuid="org-related",
        name="杭州公安",
        labels=["Entity", "GovernmentAgency"],
        summary="相关机构节点，但本测试中未通过验证。",
        attributes={},
    )

    class FakeReader:
        def __init__(self, backend=None):
            pass

        def filter_defined_entities(self, graph_id, defined_entity_types=None, enrich_with_edges=True):
            return FilteredEntities(
                entities=[verified_entity, skipped_entity],
                entity_types={"Person", "GovernmentAgency"},
                total_count=2,
                filtered_count=2,
            )

    class FakeResolver:
        def __init__(self, min_source_count=1, allow_group_agents=True, concurrency=1):
            pass

        def resolve_entities(self, entities):
            return [
                ResolvedRealEntity(
                    entity_uuid=verified_entity.uuid,
                    entity_name=verified_entity.name,
                    entity_type="Person",
                    verification_status=VERIFIED,
                    real_identity_summary="被告人许国利因杭州杀妻案被判处死刑。",
                    verified_facts=["被告人许国利因杭州杀妻案被判处死刑。"],
                ),
                ResolvedRealEntity(
                    entity_uuid=skipped_entity.uuid,
                    entity_name=skipped_entity.name,
                    entity_type="GovernmentAgency",
                    verification_status=UNSUPPORTED,
                    skip_reason="测试跳过",
                ),
            ]

    captured = {}

    class FakeProfileGenerator:
        def __init__(self, *args, **kwargs):
            pass

        def generate_profiles_from_entities(self, **kwargs):
            captured["entities"] = kwargs["entities"]
            captured["strict_real_mode"] = kwargs["strict_real_mode"]
            captured["resolved_real_entities"] = kwargs["resolved_real_entities"]
            return [
                manager_module.OasisAgentProfile(
                    user_id=0,
                    user_name="xuguoli_179",
                    name="许国利",
                    bio="被告人许国利因杭州杀妻案被判处死刑。",
                    persona="个人主体：许国利是杭州杀妻案中的案发当事人。",
                    source_entity_uuid=verified_entity.uuid,
                    source_entity_type="Person",
                    verification_status=VERIFIED,
                )
            ]

        def save_profiles(self, profiles, file_path, platform="reddit"):
            pass

    class FakeConfigGenerator:
        def generate_config(self, **kwargs):
            captured["config_entities"] = kwargs["entities"]
            return SimulationParameters(
                simulation_id=kwargs["simulation_id"],
                project_id=kwargs["project_id"],
                graph_id=kwargs["graph_id"],
                simulation_requirement=kwargs["simulation_requirement"],
            )

    monkeypatch.setattr(manager_module, "ZepEntityReader", FakeReader)
    monkeypatch.setattr(manager_module, "RealEntityResolver", FakeResolver)
    monkeypatch.setattr(manager_module, "OasisProfileGenerator", FakeProfileGenerator)
    monkeypatch.setattr(manager_module, "SimulationConfigGenerator", FakeConfigGenerator)

    manager = SimulationManager()
    state = SimulationState(
        simulation_id="sim_verified_only",
        project_id="proj_1",
        graph_id="graph_1",
        graph_backend="graphiti",
        status=SimulationStatus.CREATED,
    )
    manager._save_simulation_state(state)

    result = manager.prepare_simulation(
        simulation_id="sim_verified_only",
        simulation_requirement="测试真实画像",
        document_text="测试文档",
        use_real_profiles=True,
        strict_real_mode=True,
    )

    assert result.status == SimulationStatus.READY
    assert [entity.uuid for entity in captured["entities"]] == [verified_entity.uuid]
    assert [entity.uuid for entity in captured["config_entities"]] == [verified_entity.uuid]
    assert captured["strict_real_mode"] is True
    assert captured["resolved_real_entities"][skipped_entity.uuid].verification_status == UNSUPPORTED
    assert result.entities_count == 1
    assert result.profiles_count == 1
    assert result.verification_candidate_count == 2
    assert result.verification_verified_count == 1
    assert result.verification_skipped_count == 1


def test_prepare_filters_location_entities_before_profiles_and_config(tmp_path, monkeypatch):
    monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path))

    place_entity = EntityNode(
        uuid="place-1",
        name="三堡北苑",
        labels=["Entity", "Person"],
        summary="三堡北苑是杭州市江干区的住宅小区，是案件相关地点。",
        attributes={},
    )
    mall_entity = EntityNode(
        uuid="mall-1",
        name="庆春银泰",
        labels=["Entity", "Organization"],
        summary="银泰百货庆春店位于杭州市庆春路与延安路交叉口，是杭州核心商圈重要商业体。",
        attributes={"org_type": "企业/品牌"},
    )
    platform_entity = EntityNode(
        uuid="platform-1",
        name="小红书",
        labels=["Entity", "Person"],
        summary="小红书平台出现相关公共讨论。",
        attributes={},
    )
    person_entity = EntityNode(
        uuid="person-1",
        name="来惠利",
        labels=["Entity", "Person"],
        summary="来惠利是案件当事人。",
        attributes={},
    )

    class FakeReader:
        def __init__(self, backend=None):
            pass

        def filter_defined_entities(self, graph_id, defined_entity_types=None, enrich_with_edges=True):
            return FilteredEntities(
                entities=[place_entity, mall_entity, platform_entity, person_entity],
                entity_types={"Person", "Organization"},
                total_count=4,
                filtered_count=4,
            )

    captured = {}

    class FakeProfileGenerator:
        def __init__(self, *args, **kwargs):
            pass

        def generate_profiles_from_entities(self, **kwargs):
            captured["entities"] = kwargs["entities"]
            return [
                manager_module.OasisAgentProfile(
                    user_id=idx,
                    user_name=f"agent_{idx}",
                    name=entity.name,
                    bio=entity.summary,
                    persona=entity.summary,
                    source_entity_uuid=entity.uuid,
                    source_entity_type=entity.get_entity_type(),
                    verification_status=UNSUPPORTED,
                )
                for idx, entity in enumerate(kwargs["entities"])
            ]

        def save_profiles(self, profiles, file_path, platform="reddit"):
            pass

    class FakeConfigGenerator:
        def generate_config(self, **kwargs):
            captured["config_entities"] = kwargs["entities"]
            return SimulationParameters(
                simulation_id=kwargs["simulation_id"],
                project_id=kwargs["project_id"],
                graph_id=kwargs["graph_id"],
                simulation_requirement=kwargs["simulation_requirement"],
            )

    monkeypatch.setattr(manager_module, "ZepEntityReader", FakeReader)
    monkeypatch.setattr(manager_module, "OasisProfileGenerator", FakeProfileGenerator)
    monkeypatch.setattr(manager_module, "SimulationConfigGenerator", FakeConfigGenerator)

    manager = SimulationManager()
    state = SimulationState(
        simulation_id="sim_filter_locations",
        project_id="proj_1",
        graph_id="graph_1",
        graph_backend="graphiti",
        status=SimulationStatus.CREATED,
    )
    manager._save_simulation_state(state)

    result = manager.prepare_simulation(
        simulation_id="sim_filter_locations",
        simulation_requirement="测试地点过滤",
        document_text="测试文档",
        use_real_profiles=False,
    )

    assert result.status == SimulationStatus.READY
    assert [entity.name for entity in captured["entities"]] == ["小红书", "来惠利"]
    assert [entity.name for entity in captured["config_entities"]] == ["小红书", "来惠利"]
    assert captured["entities"][0].get_entity_type() == "SocialMediaPlatform"
    assert result.entities_count == 2
    assert result.profiles_count == 2
    assert result.entity_types == ["Person", "SocialMediaPlatform"]
