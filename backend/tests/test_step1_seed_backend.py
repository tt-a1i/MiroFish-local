import io
import json
from datetime import datetime

from app import create_app
from app.models.project import Project, ProjectManager, ProjectStatus
from app.services.bailian_web_search_service import BailianWebSearchService
from app.services.bocha_search_service import BochaSearchService, SearchSource
from app.services.seed_analysis_service import SeedAnalysisService
from app.services.web_search_provider import WebSearchProviderFactory


def test_project_seed_fields_roundtrip():
    project = Project(
        project_id="proj_seed",
        name="seed",
        status=ProjectStatus.CREATED,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        seed_input_mode="web_search",
        search_query="测试检索",
        seed_summary_md="## 摘要",
        seed_full_content_md="## 完整内容",
        seed_sources=[{"title": "来源", "url": "https://example.com"}],
        simulation_suggestions=["模拟建议"],
        entity_hints=["测试公司"],
        seed_metadata={"analysis_mode": "fallback"},
    )

    restored = Project.from_dict(project.to_dict())

    assert restored.seed_input_mode == "web_search"
    assert restored.search_query == "测试检索"
    assert restored.seed_summary_md == "## 摘要"
    assert restored.seed_full_content_md == "## 完整内容"
    assert restored.seed_sources == [{"title": "来源", "url": "https://example.com"}]
    assert restored.simulation_suggestions == ["模拟建议"]
    assert restored.entity_hints == ["测试公司"]
    assert restored.seed_metadata == {"analysis_mode": "fallback"}


def test_project_manager_seed_summary_and_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    project = ProjectManager.create_project(name="seed files")

    ProjectManager.save_seed_summary(project.project_id, "## Seed")
    ProjectManager.save_seed_sources(
        project.project_id,
        [{"title": "A", "url": "https://example.com/a"}],
    )

    assert ProjectManager.get_seed_summary(project.project_id) == "## Seed"
    assert ProjectManager.get_seed_sources(project.project_id) == [
        {"title": "A", "url": "https://example.com/a"}
    ]


def test_web_search_seed_rejects_multipart_input():
    app = create_app()
    client = app.test_client()

    response = client.post(
        "/api/graph/seed/web-search",
        data={"query": "测试", "files": (io.BytesIO(b"hello"), "test.txt")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "仅接受 JSON 请求" in data["error"]


def test_json_ontology_generation_rejects_seed_inputs():
    app = create_app()
    client = app.test_client()

    response = client.post(
        "/api/graph/ontology/generate",
        json={
            "project_id": "proj_test",
            "simulation_requirement": "模拟信息扩散",
            "search_query": "不应同时提交",
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "文件和搜索输入请先完成 seed 阶段" in data["error"]


def test_web_search_provider_defaults_to_bailian(monkeypatch):
    monkeypatch.setattr("app.services.web_search_provider.Config.USE_BOCHA_WEB_SEARCH", False)
    monkeypatch.setattr("app.services.web_search_provider.Config.WEB_SEARCH_PROVIDER", "bailian")

    assert WebSearchProviderFactory.get_provider_name() == "bailian"
    assert isinstance(WebSearchProviderFactory.create(), BailianWebSearchService)


def test_web_search_provider_uses_bocha_when_switch_enabled(monkeypatch):
    monkeypatch.setattr("app.services.web_search_provider.Config.USE_BOCHA_WEB_SEARCH", True)
    monkeypatch.setattr("app.services.web_search_provider.Config.WEB_SEARCH_PROVIDER", "bailian")

    assert WebSearchProviderFactory.get_provider_name() == "bocha"
    assert isinstance(WebSearchProviderFactory.create(), BochaSearchService)


def test_web_search_seed_uses_configured_provider(monkeypatch, tmp_path):
    app = create_app()
    client = app.test_client()
    captured = {}

    class FakeSearchService:
        def search(self, query, count=None, freshness=None, summary=True):
            captured.update({
                "query": query,
                "count": count,
                "freshness": freshness,
                "summary": summary,
            })
            return [SearchSource(title="来源", url="https://example.com/news", snippet="片段")]

    class FakeSeedAnalysisService:
        _build_search_material = staticmethod(
            lambda sources, query: f"检索词：{query}\n来源数：{len(sources)}"
        )

        def analyze_from_sources(self, sources, query, additional_context=None):
            from app.services.seed_analysis_service import SeedAnalysisResult

            return SeedAnalysisResult(
                seed_summary_md="## Seed",
                simulation_suggestions=["建议"],
                entity_hints=["来源"],
                seed_metadata={},
            )

    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr("app.api.graph.WebSearchProviderFactory.get_provider_name", lambda provider=None: "bailian")
    monkeypatch.setattr("app.api.graph.WebSearchProviderFactory.create", lambda provider=None: FakeSearchService())
    monkeypatch.setattr("app.api.graph.SeedAnalysisService", FakeSeedAnalysisService)

    response = client.post(
        "/api/graph/seed/web-search",
        json={"search_query": "测试关键词", "count": 2, "summary": False},
    )

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert captured == {
        "query": "测试关键词",
        "count": 2,
        "freshness": None,
        "summary": False,
    }
    assert data["seed_metadata"]["web_search_provider"] == "bailian"
    assert data["seed_full_content_md"] == "## Seed"
    assert ProjectManager.get_extracted_text(data["project_id"]) == "## Seed"


def test_web_search_seed_stream_emits_progress_and_result(monkeypatch, tmp_path):
    app = create_app()
    client = app.test_client()

    class FakeSearchService:
        def search(self, query, count=None, freshness=None, summary=True):
            return [
                SearchSource(
                    title="来源",
                    url="https://example.com/news",
                    snippet="片段",
                    site_name="Example",
                )
            ]

    class FakeSeedAnalysisService:
        _build_search_material = staticmethod(
            lambda sources, query: f"检索词：{query}\n来源数：{len(sources)}"
        )

        def analyze_from_sources(self, sources, query, additional_context=None):
            from app.services.seed_analysis_service import SeedAnalysisResult

            return SeedAnalysisResult(
                seed_summary_md="## Seed",
                simulation_suggestions=["建议"],
                entity_hints=["来源"],
                seed_metadata={},
            )

    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr("app.api.graph.WebSearchProviderFactory.get_provider_name", lambda provider=None: "bailian")
    monkeypatch.setattr("app.api.graph.WebSearchProviderFactory.create", lambda provider=None: FakeSearchService())
    monkeypatch.setattr("app.api.graph.SeedAnalysisService", FakeSeedAnalysisService)

    response = client.post(
        "/api/graph/seed/web-search/stream",
        json={"search_query": "测试关键词"},
    )

    assert response.status_code == 200
    events = [
        json.loads(line)
        for line in response.get_data(as_text=True).splitlines()
        if line.strip()
    ]
    assert [event["event"] for event in events] == [
        "progress",
        "progress",
        "sources",
        "progress",
        "progress",
        "progress",
        "complete",
    ]
    provider_event = events[1]
    assert provider_event["message"] == "准备进行 Web Search，抓取可引用网页来源"
    assert "provider" not in provider_event
    assert "博查" not in provider_event["message"]
    assert "百炼" not in provider_event["message"]
    assert events[-1]["data"]["seed_summary_md"] == "## Seed"
    assert events[-1]["data"]["seed_metadata"]["web_search_provider"] == "bailian"


def test_web_search_seed_stream_masks_provider_error(monkeypatch):
    app = create_app()
    client = app.test_client()

    class FailingSearchService:
        def search(self, query, count=None, freshness=None, summary=True):
            raise RuntimeError("博查搜索请求失败")

    monkeypatch.setattr("app.api.graph.WebSearchProviderFactory.get_provider_name", lambda provider=None: "bocha")
    monkeypatch.setattr("app.api.graph.WebSearchProviderFactory.create", lambda provider=None: FailingSearchService())

    response = client.post(
        "/api/graph/seed/web-search/stream",
        json={"search_query": "测试关键词"},
    )

    assert response.status_code == 200
    events = [
        json.loads(line)
        for line in response.get_data(as_text=True).splitlines()
        if line.strip()
    ]

    assert events[-1]["event"] == "error"
    assert events[-1]["message"] == "Web Search 处理失败，请稍后重试或调整检索关键词"
    assert "博查" not in events[-1]["message"]
    assert "百炼" not in events[-1]["message"]
    assert "traceback" not in events[-1]


def test_uploaded_seed_uses_extracted_file_text_as_full_content(monkeypatch, tmp_path):
    app = create_app()
    client = app.test_client()

    class FakeSeedAnalysisService:
        def analyze_from_text(self, text, topic="上传文档", additional_context=None):
            from app.services.seed_analysis_service import SeedAnalysisResult

            assert "上传正文内容" in text
            return SeedAnalysisResult(
                seed_summary_md="## 上传摘要",
                simulation_suggestions=["建议"],
                entity_hints=["测试公司"],
                seed_metadata={},
            )

    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr("app.api.graph.SeedAnalysisService", FakeSeedAnalysisService)

    response = client.post(
        "/api/graph/ontology/generate",
        data={"files": (io.BytesIO("上传正文内容".encode("utf-8")), "seed.txt")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["seed_summary_md"] == "## 上传摘要"
    assert "上传正文内容" in data["seed_full_content_md"]
    assert ProjectManager.get_extracted_text(data["project_id"]) == data["seed_full_content_md"]


def test_uploaded_seed_stream_emits_file_steps(monkeypatch, tmp_path):
    app = create_app()
    client = app.test_client()

    class FakeSeedAnalysisService:
        def analyze_from_text(self, text, topic="上传文档", additional_context=None):
            from app.services.seed_analysis_service import SeedAnalysisResult

            assert "上传正文内容" in text
            return SeedAnalysisResult(
                seed_summary_md="## 上传摘要",
                simulation_suggestions=["建议"],
                entity_hints=["测试公司"],
                seed_metadata={},
            )

    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr("app.api.graph.SeedAnalysisService", FakeSeedAnalysisService)

    response = client.post(
        "/api/graph/seed/upload/stream",
        data={"files": (io.BytesIO("上传正文内容".encode("utf-8")), "seed.txt")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    events = [
        json.loads(line)
        for line in response.get_data(as_text=True).splitlines()
        if line.strip()
    ]
    assert events[0]["event"] == "progress"
    assert any(event.get("step") == "parse" for event in events)
    assert events[-1]["event"] == "complete"
    assert events[-1]["data"]["seed_summary_md"] == "## 上传摘要"
    assert "上传正文内容" in events[-1]["data"]["seed_full_content_md"]


def test_web_search_analysis_generates_markdown_before_auxiliary_json():
    calls = []

    class FakeLLMClient:
        def chat(self, messages, temperature=0.7, max_tokens=4096, response_format=None):
            calls.append(("chat", response_format, max_tokens))
            assert response_format is None
            return "# 张雪峰去世事件全记录\n\n## 事件概述\n材料显示该事件存在来源可疑问题。"

        def chat_json(self, messages, temperature=0.3, max_tokens=4096):
            calls.append(("chat_json", None, max_tokens))
            return {
                "simulation_suggestions": ["模拟谣言传播与辟谣路径"],
                "entity_hints": ["张雪峰", "网易"],
            }

    service = SeedAnalysisService(llm_client=FakeLLMClient())

    result = service.analyze_from_sources(
        sources=[
            SearchSource(
                title="张雪峰去世传闻",
                url="https://example.com/news",
                snippet="张雪峰 去世 网易 辟谣",
                site_name="网易",
            )
        ],
        query="张雪峰去世事件",
    )

    assert result.seed_summary_md.startswith("# 张雪峰去世事件全记录")
    assert result.simulation_suggestions == ["推演谣言传播与辟谣路径"]
    assert result.entity_hints == ["张雪峰", "网易"]
    assert result.seed_metadata["analysis_mode"] == "llm"
    assert calls[0] == ("chat", None, 5000)
    assert calls[1] == ("chat_json", None, 1200)


def test_seed_suggestions_normalize_simulation_wording():
    assert SeedAnalysisService._clean_suggestion_list(
        ["可模拟事件发酵路径", "模拟多方主体回应"],
        limit=3,
    ) == ["可推演事件发酵路径", "推演多方主体回应"]

    assert SeedAnalysisService._fallback_suggestions("测试事件", ["主体A"])[0].startswith(
        "追踪测试事件"
    )


def test_seed_suggestions_keep_event_focus_short_and_plain():
    long_text = "追踪小女孩吐槽雷军武汉过早视频的二次传播路径，研判其如何从地方性偶发片段演变为全国性舆论符号，并分析情绪化标签对公众认知的塑造作用。"

    assert SeedAnalysisService._normalize_suggestion_text(long_text) == (
        "追踪小女孩吐槽雷军武汉过早视频的二次传播路径"
    )

    material = """
    雷军在武汉过早时被多人围观拍摄。
    一名小女孩吐槽吃早餐还要这么多人拍照，引发网友热议。
    之后相关视频在微博和短视频平台继续传播。
    """
    suggestions = SeedAnalysisService._fallback_suggestions("雷军武汉过早事件", ["雷军", "小女孩"], material)

    assert suggestions[0] == "追踪小女孩吐槽吃早餐还要这么多人拍照的二次传播路径"
    assert "小米汽车" not in "".join(suggestions)
    assert all(len(suggestion) <= SeedAnalysisService.MAX_SUGGESTION_LENGTH for suggestion in suggestions)


def test_seed_suggestions_repair_markdown_structure_fragments():
    material = """
    # 雷军武汉过早事件全记录
    ## 事件概述
    - 时间：2026年6月15日清晨；6月16日至6月21日（舆情发酵与回应）
    - 地点：湖北省武汉市武昌区大成路早食街
    - 核心主体：小米集团创始人雷军、围观群众及一名小女孩
    - 关键结果：一段小女孩吐槽“吃个早饭还要这么多人拍照，我靠”的视频进一步引爆舆论。

    ### 现场细节与争议萌芽
    多名市民围观、拍照、请求合影，现场气氛热烈。
    小女孩吐槽视频在微博和短视频平台传播，引发网友讨论。
    雷军随后回应称这是流量时代需要承受的代价。
    """
    suggestions = SeedAnalysisService._repair_suggestions(
        [
            "追踪至6月21日（舆情发酵与回应）-**地点**：湖的二次传播路径",
            "推演公众对至6月21日（舆情发酵与回应）-**地点**：湖的态度变化",
        ],
        topic="雷军武汉过早事件",
        entity_hints=["雷军", "小女孩"],
        material=material,
    )

    assert suggestions[0] == "追踪小女孩吐槽视频的二次传播路径"
    assert all("地点" not in suggestion for suggestion in suggestions)
    assert all("**" not in suggestion for suggestion in suggestions)


def test_seed_entity_hints_extract_core_people_and_media_platforms():
    material = """
    材料显示，被告人许国利与受害人来惠利是案件核心当事人。
    许国利涉嫌杀害妻子来惠利，案发地涉及三堡北苑小区。
    小红书、微博、抖音出现相关公共讨论，杭州中院作出判决。
    """

    hints = SeedAnalysisService._extract_entity_hints(material)

    assert "许国利" in hints
    assert "来惠利" in hints
    assert "小红书" in hints
    assert "微博" in hints
    assert "抖音" in hints
    assert "三堡北苑" not in hints


def test_seed_auxiliary_prompt_requires_core_people_first():
    captured = {}

    class FakeLLMClient:
        def chat_json(self, messages, temperature=0.2, max_tokens=1200):
            captured["prompt"] = messages[-1]["content"]
            return {
                "simulation_suggestions": ["推演公众反应路径"],
                "entity_hints": ["许国利", "来惠利", "小红书"],
            }

    service = SeedAnalysisService(llm_client=FakeLLMClient())
    suggestions, hints = service._generate_web_search_auxiliary(
        client=FakeLLMClient(),
        summary="被告人许国利与受害人来惠利是案件核心当事人，小红书出现相关讨论。",
        material="被告人许国利与受害人来惠利是案件核心当事人，小红书出现相关讨论。",
        topic="杭州杀妻案",
    )

    assert suggestions == ["推演公众反应路径"]
    assert hints == ["许国利", "来惠利", "小红书"]
    assert "先判断材料里的“事件焦点”" in captured["prompt"]
    assert "每条建议必须包含材料中出现过的具体锚点" in captured["prompt"]
    assert "禁止输出脱离事件触发点的方向" in captured["prompt"]
    assert "必须优先覆盖核心人物" in captured["prompt"]
    assert "这些人物即使不是可发声账号也要保留为图谱实体提示" in captured["prompt"]
    assert "媒体/社交平台可以作为平台实体提示" in captured["prompt"]


def test_bailian_web_search_parses_sources():
    raw_text = """
    {
      "sources": [
        {
          "title": "新闻标题",
          "url": "https://example.com/news",
          "snippet": "新闻片段",
          "summary": "搜索摘要",
          "site_name": "Example",
          "date_published": "2026-05-01"
        },
        {
          "title": "非法链接",
          "url": "javascript:void(0)"
        },
        {
          "title": "重复链接",
          "url": "https://example.com/news"
        }
      ]
    }
    """

    sources = BailianWebSearchService.parse_sources(raw_text)

    assert len(sources) == 1
    assert sources[0].title == "新闻标题"
    assert sources[0].url == "https://example.com/news"
    assert sources[0].snippet == "新闻片段"
    assert sources[0].summary == "搜索摘要"
    assert sources[0].site_name == "Example"
    assert sources[0].date_published == "2026-05-01"


def test_bailian_freshness_maps_bocha_style_values_to_days():
    assert BailianWebSearchService._freshness_to_days("twoMonths") == 60
    assert BailianWebSearchService._freshness_to_days("14") == 14
    assert BailianWebSearchService._freshness_to_days("") is None


def test_bailian_web_search_filters_unusable_sources(monkeypatch):
    service = BailianWebSearchService(api_key="test-key")
    sources = [
        SearchSource(title="可用来源", url="https://example.com/live#fragment", snippet="有效"),
        SearchSource(title="重复来源", url="https://example.com/live", snippet="重复"),
        SearchSource(title="搜索页", url="https://example.com/search?q=test", snippet="低价值"),
        SearchSource(title="已删除", url="https://example.com/deleted", snippet="该内容已被删除"),
        SearchSource(title="不可访问", url="https://example.com/missing", snippet="有效"),
    ]

    monkeypatch.setattr(
        BailianWebSearchService,
        "_check_live_source",
        lambda self, source: (
            source.url == "https://example.com/live",
            "" if source.url == "https://example.com/live" else "链接不可访问",
            "<html><body>完整正文片段</body></html>" if source.url == "https://example.com/live" else "",
        ),
    )

    filtered = service._filter_live_sources(sources)

    assert len(filtered) == 1
    assert filtered[0].url == "https://example.com/live"
    assert "完整正文片段" in filtered[0].summary


def test_bocha_search_service_parses_web_pages(monkeypatch):
    response_payload = {
        "code": 200,
        "data": {
            "webPages": {
                "value": [
                    {
                        "name": "新闻标题",
                        "url": "https://example.com/news",
                        "snippet": "新闻片段",
                        "summary": "搜索摘要",
                        "siteName": "Example",
                        "datePublished": "2026-05-01",
                    }
                ]
            }
        },
    }

    monkeypatch.setattr(
        BochaSearchService,
        "_post_json",
        lambda self, payload: response_payload,
    )

    sources = BochaSearchService(api_key="test-key", validate_links=False).search("测试", count=1)

    assert len(sources) == 1
    assert sources[0].title == "新闻标题"
    assert sources[0].url == "https://example.com/news"
    assert sources[0].snippet == "新闻片段"
    assert sources[0].summary == "搜索摘要"
    assert sources[0].site_name == "Example"
    assert sources[0].date_published == "2026-05-01"


def test_bocha_search_uses_two_months_freshness_by_default(monkeypatch):
    captured_payload = {}
    response_payload = {
        "data": {
            "webPages": {
                "value": [
                    {
                        "name": "新闻标题",
                        "url": "https://example.com/news",
                    }
                ]
            }
        }
    }

    def fake_post_json(self, payload):
        captured_payload.update(payload)
        return response_payload

    monkeypatch.setattr(BochaSearchService, "_post_json", fake_post_json)

    BochaSearchService(api_key="test-key", validate_links=False).search("测试", count=1)

    assert captured_payload["freshness"] == "twoMonths"
    assert captured_payload["count"] == 3


def test_bocha_search_accepts_explicit_freshness(monkeypatch):
    captured_payload = {}
    response_payload = {
        "data": {
            "webPages": {
                "value": [
                    {
                        "name": "新闻标题",
                        "url": "https://example.com/news",
                    }
                ]
            }
        }
    }

    def fake_post_json(self, payload):
        captured_payload.update(payload)
        return response_payload

    monkeypatch.setattr(BochaSearchService, "_post_json", fake_post_json)

    BochaSearchService(api_key="test-key", validate_links=False).search(
        "测试",
        count=1,
        freshness="oneMonth",
    )

    assert captured_payload["freshness"] == "oneMonth"
    assert captured_payload["count"] == 1


def test_bocha_search_filters_old_sources_by_default(monkeypatch):
    response_payload = {
        "data": {
            "webPages": {
                "value": [
                    {
                        "name": "近期新闻",
                        "url": "https://example.com/recent",
                        "datePublished": "2026-05-01T00:00:00+08:00",
                    },
                    {
                        "name": "过期新闻",
                        "url": "https://example.com/old",
                        "datePublished": "2026-01-01T00:00:00+08:00",
                    },
                    {
                        "name": "无日期新闻",
                        "url": "https://example.com/no-date",
                    },
                ]
            }
        }
    }

    monkeypatch.setattr(BochaSearchService, "_post_json", lambda self, payload: response_payload)
    class FixedDatetime:
        now = staticmethod(lambda tz=None: datetime(2026, 5, 26, tzinfo=tz))
        fromisoformat = staticmethod(datetime.fromisoformat)
        strptime = staticmethod(datetime.strptime)

    monkeypatch.setattr("app.services.bocha_search_service.datetime", FixedDatetime)

    sources = BochaSearchService(api_key="test-key", validate_links=False).search("测试", count=5)

    assert [source.url for source in sources] == [
        "https://example.com/recent",
        "https://example.com/no-date",
    ]


def test_bocha_search_filters_empty_invalid_and_deleted_sources(monkeypatch):
    response_payload = {
        "data": {
            "webPages": {
                "value": [
                    {"name": "空链接", "url": "", "snippet": "无效"},
                    {"name": "非法链接", "url": "javascript:void(0)", "snippet": "无效"},
                    {"name": "已删除文章", "url": "https://example.com/deleted", "snippet": "该内容已被删除"},
                    {"name": "可用来源", "url": "https://example.com/live#fragment", "snippet": "有效新闻"},
                    {"name": "重复来源", "url": "https://example.com/live", "snippet": "重复"},
                    {"name": "不可访问来源", "url": "https://example.com/missing", "snippet": "有效标题"},
                ]
            }
        }
    }

    monkeypatch.setattr(BochaSearchService, "_post_json", lambda self, payload: response_payload)
    monkeypatch.setattr(
        BochaSearchService,
        "_check_live_source",
        lambda self, source: (source.url == "https://example.com/live", "链接不可访问"),
    )

    sources = BochaSearchService(api_key="test-key", validate_links=True).search("测试", count=6)

    assert len(sources) == 1
    assert sources[0].title == "可用来源"
    assert sources[0].url == "https://example.com/live"


def test_bocha_link_check_falls_back_to_get_when_head_is_blocked(monkeypatch):
    calls = []

    def fake_request_source_preview(self, url, method="GET"):
        calls.append(method)
        if method == "HEAD":
            return 403, ""
        return 200, "<html><title>可用新闻</title></html>"

    monkeypatch.setattr(
        BochaSearchService,
        "_request_source_preview",
        fake_request_source_preview,
    )

    service = BochaSearchService(api_key="test-key", validate_links=True)
    is_live, reason = service._check_live_source(
        SearchSource(title="可用新闻", url="https://example.com/news")
    )

    assert is_live is True
    assert reason == ""
    assert calls == ["HEAD", "GET"]


def test_bocha_link_check_falls_back_to_get_when_head_raises(monkeypatch):
    calls = []

    def fake_request_source_preview(self, url, method="GET"):
        calls.append(method)
        if method == "HEAD":
            raise TimeoutError("head timeout")
        return 200, "<html><title>可用新闻</title></html>"

    monkeypatch.setattr(
        BochaSearchService,
        "_request_source_preview",
        fake_request_source_preview,
    )

    service = BochaSearchService(api_key="test-key", validate_links=True)
    is_live, reason = service._check_live_source(
        SearchSource(title="可用新闻", url="https://example.com/news")
    )

    assert is_live is True
    assert reason == ""
    assert calls == ["HEAD", "GET"]


def test_bocha_deleted_marker_does_not_match_common_substrings():
    assert BochaSearchService._looks_deleted_from_body('<a class="gonew">返回新闻频道</a>') is False
    assert BochaSearchService._looks_deleted_from_body("function onthrow(error) {}") is False
    assert BochaSearchService._looks_deleted_from_body("404 Not Found") is True
