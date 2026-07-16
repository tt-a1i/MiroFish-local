import json

from app.services.real_entity_resolver import RealEntityResolver, RealEntitySource, UNSUPPORTED, UNVERIFIED, VERIFIED
from app.services.zep_entity_reader import EntityNode


def make_entity(uuid, name, label="Person"):
    return EntityNode(
        uuid=uuid,
        name=name,
        labels=["Entity", label],
        summary=f"{name} is a public entity with enough graph context for verification.",
        attributes={},
    )


class FailingSearchService:
    def search(self, *args, **kwargs):
        raise AssertionError("unsupported 节点不应该调用外部搜索")


def test_default_entity_without_context_is_unsupported():
    entity = EntityNode(
        uuid="entity-1",
        name="Entity",
        labels=["Entity"],
        summary="",
        attributes={},
    )

    resolver = RealEntityResolver(search_service=FailingSearchService())
    result = resolver.resolve_entity(entity)

    assert result.verification_status == UNSUPPORTED
    assert "默认 Entity" in result.skip_reason
    assert result.info_sources == []


def test_group_entities_are_disabled_by_default():
    entity = EntityNode(
        uuid="group-1",
        name="Example Community",
        labels=["Entity", "Group"],
        summary="A group with enough graph context but group agents are disabled by default.",
        attributes={"kind": "group", "region": "test"},
    )

    resolver = RealEntityResolver(search_service=FailingSearchService(), allow_group_agents=False)
    result = resolver.resolve_entity(entity)

    assert result.verification_status == UNSUPPORTED
    assert "allow_group_agents" in result.skip_reason


def test_organization_entities_are_not_blocked_by_group_default(monkeypatch):
    monkeypatch.setattr("app.services.real_entity_resolver.Config.LLM_WEB_SEARCH_API_KEY", "")

    entity = EntityNode(
        uuid="org-1",
        name="Example Organization",
        labels=["Entity", "Organization"],
        summary="An organization with enough graph context should be verified by sources.",
        attributes={"kind": "organization", "region": "test"},
    )

    class EmptySearchService:
        def search(self, *args, **kwargs):
            return []

    resolver = RealEntityResolver(search_service=EmptySearchService())
    result = resolver.resolve_entity(entity)

    assert result.verification_status == UNSUPPORTED
    assert result.skip_reason == "LLM联网查询未返回可引用来源"


def test_group_entities_are_allowed_by_default_with_one_source(monkeypatch):
    monkeypatch.setattr("app.services.real_entity_resolver.Config.LLM_WEB_SEARCH_API_KEY", "")

    entity = EntityNode(
        uuid="group-1",
        name="Example Community",
        labels=["Entity", "Group"],
        summary="A publicly documented community involved in the event.",
        attributes={},
    )

    class OneSourceSearchService:
        def search(self, *args, **kwargs):
            return [
                {
                    "title": "Example Community official profile",
                    "url": "https://example.com/community",
                    "snippet": "Example Community is a publicly documented community involved in the event.",
                }
            ]

    resolver = RealEntityResolver(search_service=OneSourceSearchService())
    result = resolver.resolve_entity(entity)

    assert result.verification_status == VERIFIED
    assert len(result.source_citations) == 1


def test_short_names_are_not_rejected_before_search(monkeypatch):
    monkeypatch.setattr("app.services.real_entity_resolver.Config.LLM_WEB_SEARCH_API_KEY", "")

    entity = EntityNode(
        uuid="short-1",
        name="张",
        labels=["Entity", "Person"],
        summary="A person with a short public name but enough context.",
        attributes={},
    )

    class ShortNameSearchService:
        def search(self, *args, **kwargs):
            return [
                {
                    "title": "张 公开资料",
                    "url": "https://example.com/zhang",
                    "snippet": "张 是公开报道中的真实人物。",
                }
            ]

    resolver = RealEntityResolver(search_service=ShortNameSearchService())
    result = resolver.resolve_entity(entity)

    assert result.verification_status == VERIFIED


def test_default_real_entity_resolution_uses_llm_web_search(monkeypatch):
    entity = make_entity("person-1", "Alice Example")

    class UnexpectedSearchService:
        def search(self, *args, **kwargs):
            raise AssertionError("LLM联网结果充足时不应该调用显式搜索服务")

    class FakeCompletions:
        def create(self, **kwargs):
            assert kwargs["extra_body"]["enable_search"] is True
            assert kwargs["extra_body"]["search_options"]["forced_search"] is True

            class Response:
                class Choice:
                    class Message:
                        content = json.dumps({
                            "sources": [
                                {
                                    "title": "Alice Example public profile",
                                    "url": "https://example.com/alice",
                                    "snippet": "Alice Example is a public person with enough context.",
                                    "site_name": "Example",
                                    "published_at": "",
                                }
                            ]
                        })

                    message = Message()

                choices = [Choice()]

            return Response()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr("app.services.real_entity_resolver.Config.LLM_WEB_SEARCH_API_KEY", "test-key")
    monkeypatch.setattr("app.services.real_entity_resolver.Config.LLM_WEB_SEARCH_VALIDATE_LINKS", False)
    resolver = RealEntityResolver(
        search_service=UnexpectedSearchService(),
        llm_web_search_client=FakeClient(),
    )
    result = resolver.resolve_entity(entity)

    assert result.verification_status == VERIFIED
    assert result.source_citations[0]["url"] == "https://example.com/alice"


def test_batch_real_entity_resolution_uses_one_llm_web_request(monkeypatch):
    entities = [
        make_entity("person-1", "Alice Example"),
        make_entity("org-1", "Beta News", "Organization"),
    ]

    class UnexpectedSearchService:
        def search(self, *args, **kwargs):
            raise AssertionError("批量 LLM 联网结果充足时不应该调用显式搜索服务")

    class FakeCompletions:
        calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            user_prompt = kwargs["messages"][1]["content"]
            assert "entity_uuid: person-1" in user_prompt
            assert "entity_uuid: org-1" in user_prompt

            class Response:
                class Choice:
                    class Message:
                        content = json.dumps({
                            "entities": [
                                {
                                    "entity_uuid": "person-1",
                                    "entity_name": "Alice Example",
                                    "sources": [
                                        {
                                            "title": "Alice Example public profile",
                                            "url": "https://example.com/alice",
                                            "snippet": "Alice Example is a public entity with enough graph context.",
                                        }
                                    ],
                                },
                                {
                                    "entity_uuid": "org-1",
                                    "entity_name": "Beta News",
                                    "sources": [
                                        {
                                            "title": "Beta News official profile",
                                            "url": "https://example.com/beta-news",
                                            "snippet": "Beta News is a public entity with enough graph context.",
                                        }
                                    ],
                                },
                            ]
                        })

                    message = Message()

                choices = [Choice()]

            return Response()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr("app.services.real_entity_resolver.Config.LLM_WEB_SEARCH_API_KEY", "test-key")
    monkeypatch.setattr("app.services.real_entity_resolver.Config.LLM_WEB_SEARCH_VALIDATE_LINKS", False)
    resolver = RealEntityResolver(
        search_service=UnexpectedSearchService(),
        llm_web_search_client=FakeClient(),
        batch_size=30,
    )

    results = resolver.resolve_entities(entities)

    assert [result.entity_uuid for result in results] == ["person-1", "org-1"]
    assert [result.verification_status for result in results] == [VERIFIED, VERIFIED]
    assert len(FakeChat.completions.calls) == 1


def test_batch_real_entity_resolution_skips_unsupported_nodes(monkeypatch):
    unsupported_entity = EntityNode(
        uuid="entity-1",
        name="Entity",
        labels=["Entity"],
        summary="",
        attributes={},
    )
    searchable_entity = make_entity("person-1", "Alice Example")
    second_searchable_entity = make_entity("org-1", "Beta News", "Organization")

    class FakeCompletions:
        def create(self, **kwargs):
            user_prompt = kwargs["messages"][1]["content"]
            assert "entity_uuid: entity-1" not in user_prompt
            assert "entity_uuid: person-1" in user_prompt
            assert "entity_uuid: org-1" in user_prompt

            class Response:
                class Choice:
                    class Message:
                        content = json.dumps({
                            "entities": [
                                {
                                    "entity_uuid": "person-1",
                                    "entity_name": "Alice Example",
                                    "sources": [
                                        {
                                            "title": "Alice Example public profile",
                                            "url": "https://example.com/alice",
                                            "snippet": "Alice Example is a public entity with enough graph context.",
                                        }
                                    ],
                                },
                                {
                                    "entity_uuid": "org-1",
                                    "entity_name": "Beta News",
                                    "sources": [
                                        {
                                            "title": "Beta News official profile",
                                            "url": "https://example.com/beta-news",
                                            "snippet": "Beta News is a public entity with enough graph context.",
                                        }
                                    ],
                                },
                            ]
                        })

                    message = Message()

                choices = [Choice()]

            return Response()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr("app.services.real_entity_resolver.Config.LLM_WEB_SEARCH_API_KEY", "test-key")
    monkeypatch.setattr("app.services.real_entity_resolver.Config.LLM_WEB_SEARCH_VALIDATE_LINKS", False)
    resolver = RealEntityResolver(llm_web_search_client=FakeClient(), batch_size=30)

    results = resolver.resolve_entities([unsupported_entity, searchable_entity, second_searchable_entity])

    assert [result.entity_uuid for result in results] == ["entity-1", "person-1", "org-1"]
    assert results[0].verification_status == UNSUPPORTED
    assert results[1].verification_status == VERIFIED
    assert results[2].verification_status == VERIFIED


def test_batch_real_entity_resolution_falls_back_when_entity_missing(monkeypatch):
    entities = [
        make_entity("person-1", "Alice Example"),
        make_entity("person-2", "Bob Example"),
    ]

    class FakeCompletions:
        calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            user_prompt = kwargs["messages"][1]["content"]
            if "待验证实体:" in user_prompt:
                content = {
                    "entities": [
                        {
                            "entity_uuid": "person-1",
                            "entity_name": "Alice Example",
                            "sources": [
                                {
                                    "title": "Alice Example public profile",
                                    "url": "https://example.com/alice",
                                    "snippet": "Alice Example is a public entity with enough graph context.",
                                }
                            ],
                        }
                    ]
                }
            else:
                assert "实体名称: Bob Example" in user_prompt
                content = {
                    "sources": [
                        {
                            "title": "Bob Example public profile",
                            "url": "https://example.com/bob",
                            "snippet": "Bob Example is a public entity with enough graph context.",
                        }
                    ]
                }

            message = type("Message", (), {"content": json.dumps(content)})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice]})()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr("app.services.real_entity_resolver.Config.LLM_WEB_SEARCH_API_KEY", "test-key")
    monkeypatch.setattr("app.services.real_entity_resolver.Config.LLM_WEB_SEARCH_VALIDATE_LINKS", False)
    resolver = RealEntityResolver(llm_web_search_client=FakeClient(), batch_size=30)

    results = resolver.resolve_entities(entities)

    assert [result.verification_status for result in results] == [VERIFIED, VERIFIED]
    assert [result.source_citations[0]["url"] for result in results] == [
        "https://example.com/alice",
        "https://example.com/bob",
    ]
    assert len(FakeChat.completions.calls) == 2


def test_batch_real_entity_resolution_can_process_batches_concurrently(monkeypatch):
    entities = [
        make_entity("person-1", "Alice Example"),
        make_entity("person-2", "Bob Example"),
        make_entity("person-3", "Carol Example"),
    ]

    captured_workers = []

    class InlineFuture:
        def __init__(self, result):
            self._result = result

        def result(self):
            return self._result

    class FakeExecutor:
        def __init__(self, max_workers):
            captured_workers.append(max_workers)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, *args):
            return InlineFuture(fn(*args))

    def fake_batch(self, batch):
        return {
            entity.uuid: [
                RealEntitySource(
                    title=f"{entity.name} public profile",
                    url=f"https://example.com/{entity.uuid}",
                    snippet=f"{entity.name} is a public entity with enough graph context.",
                )
            ]
            for _, entity, _, _ in batch
        }

    monkeypatch.setattr("app.services.real_entity_resolver.Config.LLM_WEB_SEARCH_API_KEY", "test-key")
    monkeypatch.setattr("app.services.real_entity_resolver.Config.LLM_WEB_SEARCH_VALIDATE_LINKS", False)
    monkeypatch.setattr("app.services.real_entity_resolver.ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(
        "app.services.real_entity_resolver.as_completed",
        lambda futures: reversed(list(futures)),
    )
    monkeypatch.setattr(RealEntityResolver, "_search_with_llm_web_batch", fake_batch)

    resolver = RealEntityResolver(batch_size=1, concurrency=2)
    results = resolver.resolve_entities(entities)

    assert captured_workers == [2]
    assert [result.entity_uuid for result in results] == ["person-1", "person-2", "person-3"]
    assert [result.verification_status for result in results] == [VERIFIED, VERIFIED, VERIFIED]


def test_llm_source_validation_only_filters_invalid_urls(monkeypatch):
    monkeypatch.setattr("app.services.real_entity_resolver.Config.LLM_WEB_SEARCH_VALIDATE_LINKS", True)

    resolver = RealEntityResolver(search_service=None)
    validated = resolver._validate_llm_sources(
        [
            RealEntitySource(
                title="Alice Example public profile",
                url="https://example.com/alice",
                snippet="Alice Example is a public person with enough context.",
                site_name="Example",
                published_at="",
            ),
            RealEntitySource(
                title="Alice Example invalid profile",
                url="not-a-url",
                snippet="Alice Example invalid source.",
                site_name="Example",
                published_at="",
            ),
        ]
    )

    assert [source.url for source in validated] == ["https://example.com/alice"]


def test_person_entity_rejects_related_organization_source():
    entity = EntityNode(
        uuid="person-xgl",
        name="许国利",
        labels=["Entity", "Person"],
        summary="许国利是杭州杀妻案中的被告人，与案件行为轨迹直接相关。",
        attributes={},
    )
    resolver = RealEntityResolver(min_source_count=1)

    result = resolver._resolve_from_sources(
        entity=entity,
        entity_type="Person",
        raw_query="许国利 杭州杀妻案 被告人",
        sources=[
            RealEntitySource(
                title="杭州公安通报许国利案侦办情况",
                url="https://example.com/police-xgl-case",
                snippet="杭州市公安局江干区分局是公安机关，负责刑事侦查、案件通报和公共安全维护。",
            )
        ],
    )

    assert result.verification_status == UNVERIFIED
    assert result.source_citations == []
    assert "可引用来源不足" in result.skip_reason


def test_person_entity_accepts_source_with_person_anchor():
    entity = EntityNode(
        uuid="person-xgl",
        name="许国利",
        labels=["Entity", "Person"],
        summary="许国利是杭州杀妻案中的被告人，与案件行为轨迹直接相关。",
        attributes={},
    )
    resolver = RealEntityResolver(min_source_count=1)

    result = resolver._resolve_from_sources(
        entity=entity,
        entity_type="Person",
        raw_query="许国利 杭州杀妻案 被告人",
        sources=[
            RealEntitySource(
                title="许国利一审被判死刑",
                url="https://example.com/xgl-person",
                snippet="被告人许国利因故意杀人罪被判处死刑，案件与杭州杀妻案相关。",
            )
        ],
    )

    assert result.verification_status == VERIFIED
    assert result.source_citations[0]["url"] == "https://example.com/xgl-person"
