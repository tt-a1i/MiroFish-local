"""
图谱相关API路由
采用项目上下文机制，服务端持久化状态
"""

import json
import os
import traceback
import threading
import time
from flask import Response, request, jsonify, stream_with_context

from . import graph_bp
from ..config import Config
from ..services.ontology_generator import OntologyGenerator
from ..services.graph_builder import GraphBuilderService
from ..services.type_translation_service import TypeTranslationService
from ..services.seed_analysis_service import SeedAnalysisService
from ..services.web_search_provider import WebSearchProviderFactory
from ..services.text_processor import TextProcessor
from ..utils.file_parser import FileParser
from ..utils.llm_client import LLMRequestError
from ..utils.logger import get_logger
from ..utils.llm_routing import get_graph_build_llm_endpoint_pool
from ..utils.neo4j_errors import format_neo4j_auth_error, is_neo4j_auth_error
from ..models.task import TaskManager, TaskStatus
from ..models.project import ProjectManager, ProjectStatus

# 获取日志器
logger = get_logger('mirofish.api')


def _stream_event(event: str, message: str = "", **payload) -> str:
    """生成前端可逐行解析的 NDJSON 事件。"""
    data = {"event": event, "message": message}
    data.update(payload)
    return json.dumps(data, ensure_ascii=False) + "\n"


def _source_preview(sources, limit: int = 5):
    """把搜索来源压缩成前端日志可读的摘要。"""
    previews = []
    for source in (sources or [])[:limit]:
        source_dict = source.to_dict() if hasattr(source, "to_dict") else source
        previews.append({
            "title": source_dict.get("title") or source_dict.get("name") or "未命名来源",
            "url": source_dict.get("url") or "",
            "site_name": source_dict.get("site_name") or source_dict.get("siteName") or "",
            "date_published": source_dict.get("date_published") or source_dict.get("datePublished") or "",
            "summary": source_dict.get("summary") or source_dict.get("snippet") or "",
        })
    return previews


def allowed_file(filename: str) -> bool:
    """检查文件扩展名是否允许"""
    if not filename or '.' not in filename:
        return False
    ext = os.path.splitext(filename)[1].lower().lstrip('.')
    return ext in Config.ALLOWED_EXTENSIONS


def _get_project_backend_or_404(graph_id: str):
    """按 graph_id 解析项目与 backend，不允许静默回退到全局 backend。"""
    project = ProjectManager.get_project_by_graph_id(graph_id)
    if not project:
        return None, None, (
            jsonify({
                "success": False,
                "error": f"图谱未绑定到任何项目元数据: {graph_id}"
            }),
            404,
        )
    backend = project.graph_backend or Config.ZEP_BACKEND
    return project, backend, None


def _persist_seed_analysis(project, seed_result, sources=None) -> None:
    """把 seed 分析结果同步保存到元数据和独立文件。"""
    source_dicts = []
    for source in sources or []:
        source_dicts.append(source.to_dict() if hasattr(source, "to_dict") else source)

    project.seed_summary_md = seed_result.seed_summary_md
    if project.seed_input_mode == 'web_search':
        project.seed_full_content_md = seed_result.seed_summary_md
    else:
        project.seed_full_content_md = project.seed_full_content_md or seed_result.seed_summary_md
    project.seed_sources = source_dicts
    project.simulation_suggestions = seed_result.simulation_suggestions
    project.entity_hints = seed_result.entity_hints
    project.seed_metadata = seed_result.seed_metadata

    ProjectManager.save_seed_summary(project.project_id, project.seed_summary_md)
    ProjectManager.save_seed_sources(project.project_id, project.seed_sources)


def _extract_and_store_uploaded_files(project, uploaded_files):
    """保存上传文件并提取预处理文本。"""
    document_texts = []
    all_text = ""

    for file in uploaded_files:
        if file and file.filename and allowed_file(file.filename):
            file_info = ProjectManager.save_file_to_project(
                project.project_id,
                file,
                file.filename
            )
            project.files.append({
                "filename": file_info["original_filename"],
                "size": file_info["size"]
            })

            text = FileParser.extract_text(file_info["path"])
            text = TextProcessor.preprocess_text(text)
            document_texts.append(text)
            all_text += f"\n\n=== {file_info['original_filename']} ===\n{text}"

    return document_texts, all_text


def _save_generated_ontology(project, ontology):
    """保存本体生成结果到项目。"""
    ontology = TypeTranslationService.ensure_ontology_translations(ontology)
    project.ontology = {
        "entity_types": ontology.get("entity_types", []),
        "edge_types": ontology.get("edge_types", [])
    }
    project.analysis_summary = ontology.get("analysis_summary", "")
    project.status = ProjectStatus.ONTOLOGY_GENERATED
    ProjectManager.save_project(project)


def _merge_task_progress_detail(task_manager: TaskManager, task_id: str, **updates) -> dict:
    """合并更新任务进度详情，避免覆盖 graph_id 等前端轮询依赖字段。"""
    task = task_manager.get_task(task_id)
    detail = dict((task.progress_detail if task else {}) or {})
    detail.update({key: value for key, value in updates.items() if value is not None})
    return detail


def _dedupe_text_items(items, limit: int = 120) -> list:
    """按顺序去重文本列表。"""
    result = []
    seen = set()
    for item in items or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _build_entity_enrichment_queries(project, graph_name: str) -> list:
    """根据事件主题、推演方向和已有实体提示生成补充检索词。"""
    topic = project.search_query or project.name or graph_name or "舆情事件"
    requirement = project.simulation_requirement or ""
    hints = " ".join((project.entity_hints or [])[:12])
    base_query = " ".join(part for part in [topic, requirement, hints] if part).strip()
    queries = [
        base_query,
        f"{topic} 相关人物 机构 媒体 平台 舆论 争议 官方回应",
        f"{topic} 时间线 当事人 组织 传播路径 评论",
    ]
    return _dedupe_text_items(queries, limit=max(1, int(Config.GRAPH_ENTITY_ENRICHMENT_QUERY_LIMIT or 1)))


def _search_entity_enrichment_sources(project, graph_name: str) -> tuple[list, list, str]:
    """联网检索事件相关补充来源，失败时返回空来源和错误摘要。"""
    if not Config.GRAPH_ENTITY_ENRICHMENT_ENABLED:
        return [], [], "补充检索已关闭"

    queries = _build_entity_enrichment_queries(project, graph_name)
    if not queries:
        return [], [], "未生成有效补充检索词"

    try:
        provider_name = WebSearchProviderFactory.get_provider_name()
        search_service = WebSearchProviderFactory.create()
    except Exception as exc:
        return queries, [], f"补充检索服务初始化失败: {exc}"

    sources = []
    seen_urls = set()
    max_sources = max(1, int(Config.GRAPH_ENTITY_ENRICHMENT_MAX_SOURCES or 1))
    per_query_count = max(1, int(Config.GRAPH_ENTITY_ENRICHMENT_SEARCH_COUNT or 1))
    errors = []
    for query in queries:
        try:
            results = search_service.search(query=query, count=per_query_count, summary=True)
        except Exception as exc:
            errors.append(f"{query}: {exc}")
            continue
        for source in results or []:
            source_dict = source.to_dict() if hasattr(source, "to_dict") else dict(source or {})
            url = str(source_dict.get("url") or "").strip()
            dedupe_key = url or str(source_dict.get("title") or "").strip()
            if not dedupe_key or dedupe_key in seen_urls:
                continue
            seen_urls.add(dedupe_key)
            source_dict["enrichment_query"] = query
            source_dict["web_search_provider"] = provider_name
            sources.append(source_dict)
            if len(sources) >= max_sources:
                break
        if len(sources) >= max_sources:
            break

    error_summary = "；".join(errors[:3])
    return queries, sources, error_summary


def _build_entity_enrichment_material(project, sources: list, graph_name: str) -> str:
    """把联网来源压缩为可写入 Graphiti 的补充事件材料。"""
    if not sources:
        return ""

    lines = [
        "# 事件相关联网补充材料",
        "",
        "以下材料用于补足原始文档未覆盖的事件相关实体。只抽取与事件主题、推演方向或已知关键实体存在明确关联的主体。",
        "",
        f"- 事件主题：{project.search_query or project.name or graph_name}",
        f"- 推演方向：{project.simulation_requirement or '未提供'}",
    ]
    if project.entity_hints:
        lines.append(f"- 已知关键实体：{'、'.join(project.entity_hints[:80])}")
    lines.append("")

    for idx, source in enumerate(sources, 1):
        title = source.get("title") or "未命名来源"
        url = source.get("url") or ""
        site_name = source.get("site_name") or source.get("siteName") or ""
        date_published = source.get("date_published") or source.get("datePublished") or ""
        query = source.get("enrichment_query") or ""
        snippet = source.get("snippet") or ""
        summary = source.get("summary") or ""
        lines.extend([
            f"## 补充来源 {idx}: {title}",
            f"- 检索词：{query}" if query else "",
            f"- 站点：{site_name}" if site_name else "",
            f"- 发布时间：{date_published}" if date_published else "",
            f"- URL：{url}" if url else "",
            f"- 摘要片段：{snippet}" if snippet else "",
            f"- 来源总结：{summary}" if summary else "",
            "",
        ])

    material = "\n".join(line for line in lines if line is not None).strip()
    max_chars = max(1000, int(Config.GRAPH_ENTITY_ENRICHMENT_MAX_MATERIAL_CHARS or 1000))
    return material[:max_chars]


def _extend_entity_hints_from_sources(existing_hints: list, sources: list) -> list:
    """从补充来源摘要中提取更多实体提示。"""
    material_parts = []
    for source in sources or []:
        material_parts.extend([
            str(source.get("title") or ""),
            str(source.get("snippet") or ""),
            str(source.get("summary") or ""),
            str(source.get("site_name") or source.get("siteName") or ""),
        ])
    extracted = SeedAnalysisService._extract_entity_hints("\n".join(material_parts))
    return _dedupe_text_items([*(existing_hints or []), *extracted], limit=160)


def _get_graph_build_llm_observability(builder=None) -> dict:
    """读取图谱构建 LLM 路由观测信息，兼容单模型和未来 Builder 实现。"""
    fallback = {
        "dual_llm_enabled": False,
        "llm_routes": [],
        "llm_route_weights": {},
        "llm_route_counts": {},
    }

    if builder is None:
        if Config.ZEP_BACKEND != "graphiti":
            return fallback
        try:
            pool = get_graph_build_llm_endpoint_pool(build_mode=True)
            routes = list(getattr(pool, "route_names", None) or [])
            weights = dict(getattr(pool, "weights", None) or {})
            if routes:
                return {
                    **fallback,
                    "dual_llm_enabled": bool(getattr(pool, "dual_enabled", False)),
                    "llm_routes": routes,
                    "llm_route_weights": weights,
                }
        except Exception as exc:
            logger.debug("读取图谱构建 LLM endpoint pool 初始观测失败: %s", exc)
        if Config.LLM_BOOST_API_KEY and Config.LLM_BOOST_BASE_URL and Config.LLM_BOOST_MODEL_NAME:
            return {
                **fallback,
                "llm_routes": ["boost"],
                "llm_route_weights": {"boost": 1},
            }
        if Config.LLM_API_KEY and Config.LLM_BASE_URL and Config.LLM_MODEL_NAME:
            return {
                **fallback,
                "llm_routes": ["base"],
                "llm_route_weights": {"base": 1},
            }
        return fallback

    for method_name in (
        "get_llm_observability",
        "get_llm_route_observability",
        "describe_llm_routes",
    ):
        method = getattr(builder, method_name, None)
        if callable(method):
            try:
                payload = method()
                if isinstance(payload, dict):
                    return {**fallback, **payload}
            except Exception as exc:
                logger.debug("读取图谱构建 LLM 路由观测方法失败: %s", exc)

    pool = getattr(builder, "_llm_endpoint_pool", None)
    describe_pool = getattr(builder, "_describe_llm_endpoint_pool", None)
    if callable(describe_pool):
        try:
            payload = describe_pool(pool)
            if isinstance(payload, dict):
                fallback.update(payload)
        except Exception as exc:
            logger.debug("读取图谱构建 LLM endpoint pool 失败: %s", exc)

    for attr_name, target_key in (
        ("dual_llm_enabled", "dual_llm_enabled"),
        ("llm_routes", "llm_routes"),
        ("llm_route_weights", "llm_route_weights"),
        ("llm_route_counts", "llm_route_counts"),
        ("_llm_route_counts", "llm_route_counts"),
    ):
        value = getattr(builder, attr_name, None)
        if value is not None:
            fallback[target_key] = value

    fallback["dual_llm_enabled"] = bool(fallback.get("dual_llm_enabled"))
    fallback["llm_routes"] = list(fallback.get("llm_routes") or [])
    fallback["llm_route_weights"] = dict(fallback.get("llm_route_weights") or {})
    fallback["llm_route_counts"] = dict(fallback.get("llm_route_counts") or {})
    return fallback


@graph_bp.route('/type-translations', methods=['GET'])
def get_type_translations():
    """获取实体类型和关系类型翻译表。"""
    return jsonify({
        "success": True,
        "data": TypeTranslationService.get_public_payload()
    })


# ============== 项目管理接口 ==============

@graph_bp.route('/project/<project_id>', methods=['GET'])
def get_project(project_id: str):
    """
    获取项目详情
    """
    project = ProjectManager.get_project(project_id)
    
    if not project:
        return jsonify({
            "success": False,
            "error": f"项目不存在: {project_id}"
        }), 404
    
    return jsonify({
        "success": True,
        "data": project.to_dict()
    })


@graph_bp.route('/project/list', methods=['GET'])
def list_projects():
    """
    列出所有项目
    """
    limit = request.args.get('limit', 50, type=int)
    projects = ProjectManager.list_projects(limit=limit)
    
    return jsonify({
        "success": True,
        "data": [p.to_dict() for p in projects],
        "count": len(projects)
    })


@graph_bp.route('/project/<project_id>', methods=['DELETE'])
def delete_project(project_id: str):
    """
    删除项目
    """
    project = ProjectManager.get_project(project_id)
    if project and project.graph_id:
        backend = project.graph_backend or Config.ZEP_BACKEND
        try:
            GraphBuilderService(backend=backend).delete_graph(project.graph_id)
        except Exception as exc:
            logger.warning(f"删除项目时清理图谱失败: project_id={project_id}, graph_id={project.graph_id}, error={exc}")

    success = ProjectManager.delete_project(project_id)
    
    if not success:
        return jsonify({
            "success": False,
            "error": f"项目不存在或删除失败: {project_id}"
        }), 404
    
    return jsonify({
        "success": True,
        "message": f"项目已删除: {project_id}"
    })


@graph_bp.route('/project/<project_id>/reset', methods=['POST'])
def reset_project(project_id: str):
    """
    重置项目状态（用于重新构建图谱）
    """
    project = ProjectManager.get_project(project_id)
    
    if not project:
        return jsonify({
            "success": False,
            "error": f"项目不存在: {project_id}"
        }), 404
    
    old_graph_id = project.graph_id
    old_backend = project.graph_backend or Config.ZEP_BACKEND

    # 重置到本体已生成状态
    if project.ontology:
        project.status = ProjectStatus.ONTOLOGY_GENERATED
    else:
        project.status = ProjectStatus.CREATED
    
    project.graph_id = None
    project.graph_build_task_id = None
    project.graph_backend = None
    project.graph_provider = None
    project.graph_schema_version = None
    project.error = None
    ProjectManager.save_project(project)

    if old_graph_id:
        try:
            GraphBuilderService(backend=old_backend).delete_graph(old_graph_id)
        except Exception as exc:
            logger.warning(f"重置项目时清理旧图谱失败: project_id={project_id}, graph_id={old_graph_id}, error={exc}")
    
    return jsonify({
        "success": True,
        "message": f"项目已重置: {project_id}",
        "data": project.to_dict()
    })


# ============== Step1：Web 搜索 seed ==============

@graph_bp.route('/seed/web-search', methods=['POST'])
def create_seed_from_web_search():
    """
    通过配置的联网搜索 provider 创建 seed 项目。
    """
    try:
        if request.content_type and 'multipart/form-data' in request.content_type:
            return jsonify({
                "success": False,
                "error": "Web 搜索 seed 仅接受 JSON 请求，文件输入请使用 /ontology/generate 的 multipart 阶段"
            }), 400

        data = request.get_json(silent=True) or {}
        query = (data.get('query') or data.get('search_query') or '').strip()
        project_name = data.get('project_name') or query or 'Web Search Seed'
        additional_context = data.get('additional_context')

        if not query:
            return jsonify({
                "success": False,
                "error": "请提供搜索关键词 query"
            }), 400
        if data.get('files'):
            return jsonify({
                "success": False,
                "error": "搜索输入和文件输入互斥，请不要在 Web 搜索 seed 中提交 files"
            }), 400

        provider_name = WebSearchProviderFactory.get_provider_name(data.get('provider'))
        search_service = WebSearchProviderFactory.create(provider_name)
        sources = search_service.search(
            query=query,
            count=data.get('count'),
            freshness=data.get('freshness'),
            summary=data.get('summary', True),
        )
        if not sources:
            return jsonify({
                "success": False,
                "error": "未获得可用搜索结果"
            }), 400

        project = ProjectManager.create_project(name=project_name)
        project.seed_input_mode = 'web_search'
        project.search_query = query

        source_text = SeedAnalysisService._build_search_material(
            [source.to_dict() for source in sources],
            query,
        )
        ProjectManager.save_extracted_text(project.project_id, source_text)
        project.total_text_length = len(source_text)

        seed_result = SeedAnalysisService().analyze_from_sources(
            sources=sources,
            query=query,
            additional_context=additional_context,
        )
        seed_result.seed_metadata["web_search_provider"] = provider_name
        _persist_seed_analysis(project, seed_result, sources=sources)
        ProjectManager.save_extracted_text(project.project_id, project.seed_full_content_md)
        project.total_text_length = len(project.seed_full_content_md or '')
        ProjectManager.save_project(project)

        return jsonify({
            "success": True,
            "data": {
                "project_id": project.project_id,
                "project_name": project.name,
                "seed_input_mode": project.seed_input_mode,
                "search_query": project.search_query,
                "seed_summary_md": project.seed_summary_md,
                "seed_full_content_md": project.seed_full_content_md,
                "seed_sources": project.seed_sources,
                "simulation_suggestions": project.simulation_suggestions,
                "entity_hints": project.entity_hints,
                "seed_metadata": project.seed_metadata,
                "total_text_length": project.total_text_length,
            }
        })

    except ValueError as exc:
        return jsonify({
            "success": False,
            "error": str(exc)
        }), 400
    except LLMRequestError as e:
        logger.error(
            "本体/seed 阶段 LLM 调用失败: route=%s, retryable=%s, error=%s",
            e.route_name,
            e.retryable,
            e,
        )
        return jsonify({
            "success": False,
            "error": str(e),
            "error_code": "llm_request_failed",
            "retryable": e.retryable
        }), e.status_code
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@graph_bp.route('/seed/web-search/stream', methods=['POST'])
def create_seed_from_web_search_stream():
    """通过 NDJSON 流返回联网搜索与 seed 分析进度。"""

    def generate():
        try:
            if request.content_type and 'multipart/form-data' in request.content_type:
                yield _stream_event("error", "Web 搜索 seed 仅接受 JSON 请求，文件输入请使用文件流式分析接口")
                return

            data = request.get_json(silent=True) or {}
            query = (data.get('query') or data.get('search_query') or '').strip()
            project_name = data.get('project_name') or query or 'Web Search Seed'
            additional_context = data.get('additional_context')

            if not query:
                yield _stream_event("error", "请提供搜索关键词 query")
                return
            if data.get('files'):
                yield _stream_event("error", "搜索输入和文件输入互斥，请不要在 Web 搜索 seed 中提交 files")
                return

            yield _stream_event("progress", f"已接收检索主题：{query}", step="receive", progress=5)
            provider_name = WebSearchProviderFactory.get_provider_name(data.get('provider'))
            yield _stream_event(
                "progress",
                "准备进行 Web Search，抓取可引用网页来源",
                step="provider",
                progress=12,
            )
            search_service = WebSearchProviderFactory.create(provider_name)
            sources = search_service.search(
                query=query,
                count=data.get('count'),
                freshness=data.get('freshness'),
                summary=data.get('summary', True),
            )
            if not sources:
                yield _stream_event("error", "未获得可用搜索结果")
                return

            yield _stream_event(
                "sources",
                f"联网查询完成，获得 {len(sources)} 条可用来源",
                step="sources",
                progress=38,
                sources=_source_preview(sources),
            )

            project = ProjectManager.create_project(name=project_name)
            project.seed_input_mode = 'web_search'
            project.search_query = query
            yield _stream_event(
                "progress",
                f"已创建项目上下文：{project.project_id}，正在整理搜索材料",
                step="project",
                progress=45,
                project_id=project.project_id,
            )

            source_text = SeedAnalysisService._build_search_material(
                [source.to_dict() for source in sources],
                query,
            )
            ProjectManager.save_extracted_text(project.project_id, source_text)
            project.total_text_length = len(source_text)
            yield _stream_event(
                "progress",
                f"已汇总搜索材料约 {project.total_text_length} 字，开始生成完整事件 Markdown",
                step="summary",
                progress=58,
                text_length=project.total_text_length,
            )

            seed_result = SeedAnalysisService().analyze_from_sources(
                sources=sources,
                query=query,
                additional_context=additional_context,
            )
            seed_result.seed_metadata["web_search_provider"] = provider_name
            yield _stream_event(
                "progress",
                f"事件总结已生成，正在提炼 {len(seed_result.simulation_suggestions or [])} 条推演方向建议",
                step="suggestions",
                progress=78,
                suggestions=seed_result.simulation_suggestions,
            )

            _persist_seed_analysis(project, seed_result, sources=sources)
            ProjectManager.save_extracted_text(project.project_id, project.seed_full_content_md)
            project.total_text_length = len(project.seed_full_content_md or '')
            ProjectManager.save_project(project)
            result = {
                "project_id": project.project_id,
                "project_name": project.name,
                "seed_input_mode": project.seed_input_mode,
                "search_query": project.search_query,
                "seed_summary_md": project.seed_summary_md,
                "seed_full_content_md": project.seed_full_content_md,
                "seed_sources": project.seed_sources,
                "simulation_suggestions": project.simulation_suggestions,
                "entity_hints": project.entity_hints,
                "seed_metadata": project.seed_metadata,
                "total_text_length": project.total_text_length,
            }
            yield _stream_event(
                "complete",
                "所有联网资料已处理完成，即将进入推演方向确认页",
                step="complete",
                progress=100,
                data=result,
            )

        except ValueError as exc:
            logger.error("流式联网 seed 分析失败: %s", exc)
            logger.debug(traceback.format_exc())
            yield _stream_event("error", "Web Search 处理失败，请稍后重试或调整检索关键词")
        except Exception as exc:
            logger.error("流式联网 seed 分析失败: %s", exc)
            logger.debug(traceback.format_exc())
            yield _stream_event("error", "Web Search 处理失败，请稍后重试或调整检索关键词")

    return Response(
        stream_with_context(generate()),
        mimetype="application/x-ndjson; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ============== 接口1：seed 分析 / 生成本体 ==============

@graph_bp.route('/ontology/generate', methods=['POST'])
def generate_ontology():
    """
    multipart/form-data：上传文件并生成 seed 分析，不生成本体。
    application/json：基于已有 project_id 和 simulation_requirement 生成本体。
    """
    try:
        if request.is_json:
            logger.info("=== 开始基于项目生成本体定义 ===")
            data = request.get_json(silent=True) or {}
            project_id = data.get('project_id')
            simulation_requirement = (data.get('simulation_requirement') or '').strip()
            additional_context = data.get('additional_context')

            if data.get('files') or data.get('search_query') or data.get('query'):
                return jsonify({
                    "success": False,
                    "error": "本体生成阶段仅接受 project_id 和 simulation_requirement；文件和搜索输入请先完成 seed 阶段"
                }), 400
            if not project_id:
                return jsonify({
                    "success": False,
                    "error": "请提供 project_id"
                }), 400
            if not simulation_requirement:
                return jsonify({
                    "success": False,
                    "error": "请提供模拟需求描述 (simulation_requirement)"
                }), 400

            project = ProjectManager.get_project(project_id)
            if not project:
                return jsonify({
                    "success": False,
                    "error": f"项目不存在: {project_id}"
                }), 404

            extracted_text = ProjectManager.get_extracted_text(project_id)
            if not extracted_text:
                return jsonify({
                    "success": False,
                    "error": "未找到提取的文本内容，请先完成 seed 分析阶段"
                }), 400

            project.simulation_requirement = simulation_requirement
            logger.info("调用 LLM 生成本体定义...")
            ontology = OntologyGenerator().generate(
                document_texts=[extracted_text],
                simulation_requirement=simulation_requirement,
                additional_context=additional_context if additional_context else None
            )
            _save_generated_ontology(project, ontology)
            logger.info(f"=== 本体生成完成 === 项目ID: {project.project_id}")

            return jsonify({
                "success": True,
                "data": {
                    "project_id": project.project_id,
                    "project_name": project.name,
                    "ontology": project.ontology,
                    "analysis_summary": project.analysis_summary,
                    "files": project.files,
                    "seed_input_mode": project.seed_input_mode,
                    "total_text_length": project.total_text_length
                }
            })

        logger.info("=== 开始 multipart 文件 seed 分析 ===")
        if request.form.get('search_query') or request.form.get('query'):
            return jsonify({
                "success": False,
                "error": "文件输入和搜索输入互斥；搜索 seed 请使用 /seed/web-search"
            }), 400

        project_name = request.form.get('project_name', 'Unnamed Project')
        additional_context = request.form.get('additional_context', '')
        uploaded_files = request.files.getlist('files')
        if not uploaded_files or all(not f.filename for f in uploaded_files):
            return jsonify({
                "success": False,
                "error": "请至少上传一个文档文件"
            }), 400

        project = ProjectManager.create_project(name=project_name)
        project.seed_input_mode = 'file_upload'
        simulation_requirement = request.form.get('simulation_requirement', '').strip()
        if simulation_requirement:
            project.simulation_requirement = simulation_requirement
        logger.info(f"创建项目: {project.project_id}")

        document_texts, all_text = _extract_and_store_uploaded_files(project, uploaded_files)
        if not document_texts:
            ProjectManager.delete_project(project.project_id)
            return jsonify({
                "success": False,
                "error": "没有成功处理任何文档，请检查文件格式"
            }), 400

        project.total_text_length = len(all_text)
        project.seed_full_content_md = all_text
        ProjectManager.save_extracted_text(project.project_id, all_text)
        seed_result = SeedAnalysisService().analyze_from_text(
            text=all_text,
            topic=project_name,
            additional_context=additional_context if additional_context else None,
        )
        _persist_seed_analysis(project, seed_result, sources=[])
        ProjectManager.save_project(project)
        logger.info(f"=== 文件 seed 分析完成 === 项目ID: {project.project_id}")

        return jsonify({
            "success": True,
            "data": {
                "project_id": project.project_id,
                "project_name": project.name,
                "seed_input_mode": project.seed_input_mode,
                "seed_summary_md": project.seed_summary_md,
                "seed_full_content_md": project.seed_full_content_md,
                "seed_sources": project.seed_sources,
                "simulation_suggestions": project.simulation_suggestions,
                "entity_hints": project.entity_hints,
                "seed_metadata": project.seed_metadata,
                "files": project.files,
                "total_text_length": project.total_text_length
            }
        })
        
    except LLMRequestError as e:
        logger.error(
            "本体生成 LLM 调用失败: route=%s, retryable=%s, error=%s",
            e.route_name,
            e.retryable,
            e,
        )
        return jsonify({
            "success": False,
            "error": str(e),
            "error_code": "llm_request_failed",
            "retryable": e.retryable
        }), e.status_code
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@graph_bp.route('/seed/upload/stream', methods=['POST'])
def analyze_uploaded_seed_stream():
    """通过 NDJSON 流返回文件解析与 seed 分析进度。"""

    def generate():
        try:
            if request.is_json:
                yield _stream_event("error", "文件流式分析仅接受 multipart/form-data 请求")
                return
            if request.form.get('search_query') or request.form.get('query'):
                yield _stream_event("error", "文件输入和搜索输入互斥；搜索 seed 请使用联网搜索接口")
                return

            project_name = request.form.get('project_name', 'Unnamed Project')
            additional_context = request.form.get('additional_context', '')
            uploaded_files = request.files.getlist('files')
            if not uploaded_files or all(not f.filename for f in uploaded_files):
                yield _stream_event("error", "请至少上传一个文档文件")
                return

            yield _stream_event(
                "progress",
                f"已接收 {len(uploaded_files)} 个文件，开始检查格式",
                step="receive",
                progress=5,
            )
            project = ProjectManager.create_project(name=project_name)
            project.seed_input_mode = 'file_upload'
            simulation_requirement = request.form.get('simulation_requirement', '').strip()
            if simulation_requirement:
                project.simulation_requirement = simulation_requirement

            document_texts = []
            all_text = ""
            valid_files = [file for file in uploaded_files if file and file.filename and allowed_file(file.filename)]
            if not valid_files:
                ProjectManager.delete_project(project.project_id)
                yield _stream_event("error", "没有成功处理任何文档，请检查文件格式")
                return

            total_files = len(valid_files)
            for index, file in enumerate(valid_files, 1):
                yield _stream_event(
                    "progress",
                    f"正在解析文件 {index}/{total_files}：{file.filename}",
                    step="parse",
                    progress=10 + int((index - 1) / total_files * 35),
                    filename=file.filename,
                )
                file_info = ProjectManager.save_file_to_project(
                    project.project_id,
                    file,
                    file.filename
                )
                project.files.append({
                    "filename": file_info["original_filename"],
                    "size": file_info["size"]
                })

                text = FileParser.extract_text(file_info["path"])
                text = TextProcessor.preprocess_text(text)
                document_texts.append(text)
                all_text += f"\n\n=== {file_info['original_filename']} ===\n{text}"
                yield _stream_event(
                    "progress",
                    f"文件解析完成：{file_info['original_filename']}，提取约 {len(text)} 字",
                    step="parsed",
                    progress=10 + int(index / total_files * 35),
                    filename=file_info["original_filename"],
                    text_length=len(text),
                )

            project.total_text_length = len(all_text)
            project.seed_full_content_md = all_text
            ProjectManager.save_extracted_text(project.project_id, all_text)
            yield _stream_event(
                "progress",
                f"文档预处理完成，共提取约 {project.total_text_length} 字，开始总结提炼",
                step="summary",
                progress=55,
                text_length=project.total_text_length,
            )

            seed_result = SeedAnalysisService().analyze_from_text(
                text=all_text,
                topic=project_name,
                additional_context=additional_context if additional_context else None,
            )
            yield _stream_event(
                "progress",
                f"文件摘要已生成，正在提炼 {len(seed_result.simulation_suggestions or [])} 条推演方向建议",
                step="suggestions",
                progress=82,
                suggestions=seed_result.simulation_suggestions,
            )
            _persist_seed_analysis(project, seed_result, sources=[])
            ProjectManager.save_project(project)
            result = {
                "project_id": project.project_id,
                "project_name": project.name,
                "seed_input_mode": project.seed_input_mode,
                "seed_summary_md": project.seed_summary_md,
                "seed_full_content_md": project.seed_full_content_md,
                "seed_sources": project.seed_sources,
                "simulation_suggestions": project.simulation_suggestions,
                "entity_hints": project.entity_hints,
                "seed_metadata": project.seed_metadata,
                "files": project.files,
                "total_text_length": project.total_text_length
            }
            yield _stream_event(
                "complete",
                "上传文件已解析并总结完成，即将进入推演方向确认页",
                step="complete",
                progress=100,
                data=result,
            )

        except Exception as exc:
            logger.error("流式文件 seed 分析失败: %s", exc)
            logger.debug(traceback.format_exc())
            yield _stream_event("error", str(exc), traceback=traceback.format_exc())

    return Response(
        stream_with_context(generate()),
        mimetype="application/x-ndjson; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ============== 接口2：构建图谱 ==============

@graph_bp.route('/build', methods=['POST'])
def build_graph():
    """
    接口2：根据project_id构建图谱
    
    请求（JSON）：
        {
            "project_id": "proj_xxxx",  // 必填，来自接口1
            "graph_name": "图谱名称",    // 可选
            "chunk_size": 1200,         // 可选，默认1200
            "chunk_overlap": 100,       // 可选，默认100
            "batch_size": 5             // 可选，默认5
        }
        
    返回：
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "task_id": "task_xxxx",
                "message": "图谱构建任务已启动"
            }
        }
    """
    try:
        logger.info("=== 开始构建图谱 ===")
        
        # 检查配置（仅 cloud 模式需要 ZEP_API_KEY）
        if Config.ZEP_BACKEND == 'cloud' and not Config.ZEP_API_KEY:
            logger.error("配置错误: ZEP_API_KEY未配置")
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY未配置（cloud模式需要）"
            }), 500
        
        # 解析请求
        data = request.get_json() or {}
        project_id = data.get('project_id')
        logger.debug(f"请求参数: project_id={project_id}")
        
        if not project_id:
            return jsonify({
                "success": False,
                "error": "请提供 project_id"
            }), 400
        
        # 获取项目
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"项目不存在: {project_id}"
            }), 404
        
        # 检查项目状态
        force = data.get('force', False)  # 强制重新构建
        
        if project.status == ProjectStatus.CREATED:
            return jsonify({
                "success": False,
                "error": "项目尚未生成本体，请先调用 /ontology/generate"
            }), 400
        
        if project.status == ProjectStatus.GRAPH_BUILDING and not force:
            return jsonify({
                "success": False,
                "error": "图谱正在构建中，请勿重复提交。如需强制重建，请添加 force: true",
                "task_id": project.graph_build_task_id
            }), 400
        
        # 如果强制重建，重置状态
        if force and project.status in [ProjectStatus.GRAPH_BUILDING, ProjectStatus.FAILED, ProjectStatus.GRAPH_COMPLETED]:
            old_graph_id = project.graph_id
            old_backend = project.graph_backend or Config.ZEP_BACKEND
            project.status = ProjectStatus.ONTOLOGY_GENERATED
            project.graph_id = None
            project.graph_build_task_id = None
            project.graph_backend = None
            project.graph_provider = None
            project.graph_schema_version = None
            project.error = None
            if old_graph_id:
                try:
                    GraphBuilderService(backend=old_backend).delete_graph(old_graph_id)
                except Exception as exc:
                    logger.warning(f"强制重建前清理旧图谱失败: project_id={project_id}, graph_id={old_graph_id}, error={exc}")
        
        # 获取配置
        graph_name = data.get('graph_name', project.name or 'MiroFish Graph')
        chunk_size = int(data.get('chunk_size') or Config.DEFAULT_CHUNK_SIZE)
        chunk_overlap = int(data.get('chunk_overlap') or Config.DEFAULT_CHUNK_OVERLAP)
        batch_size = max(1, int(data.get('batch_size') or Config.GRAPH_BUILD_BATCH_SIZE))
        graph_build_concurrency = max(1, int(data.get('concurrency') or Config.GRAPH_BUILD_CONCURRENCY))
        batch_plan = GraphBuilderService.resolve_batch_plan(
            batch_size=batch_size,
            concurrency=graph_build_concurrency,
            backend=Config.ZEP_BACKEND,
        )
        effective_batch_size = batch_plan.batch_size
        effective_concurrency = batch_plan.concurrency
        bulk_ingest_enabled = batch_plan.use_bulk_ingest
        
        # 更新项目配置
        project.chunk_size = chunk_size
        project.chunk_overlap = chunk_overlap
        
        # 获取提取的文本
        text = ProjectManager.get_extracted_text(project_id)
        if not text:
            return jsonify({
                "success": False,
                "error": "未找到提取的文本内容"
            }), 400
        
        # 获取本体
        ontology = project.ontology
        if not ontology:
            return jsonify({
                "success": False,
                "error": "未找到本体定义"
            }), 400

        extraction_context = {
            "event_topic": project.search_query or project.name or graph_name,
            "simulation_requirement": project.simulation_requirement or "",
            "seed_summary": project.seed_summary_md or project.analysis_summary or "",
            "entity_hints": project.entity_hints or [],
        }
        
        # 创建异步任务
        task_manager = TaskManager()
        task_id = task_manager.create_task(f"构建图谱: {graph_name}")
        logger.info(f"创建图谱构建任务: task_id={task_id}, project_id={project_id}")
        task_manager.update_task(
            task_id,
            progress_detail={
                "batch_size": effective_batch_size,
                "concurrency": effective_concurrency,
                "bulk_ingest_enabled": bulk_ingest_enabled,
                "requested_batch_size": batch_size,
                "requested_concurrency": graph_build_concurrency,
                "backend": Config.ZEP_BACKEND,
                "llm_boost_enabled": bool(Config.LLM_BOOST_API_KEY and Config.LLM_BOOST_BASE_URL and Config.LLM_BOOST_MODEL_NAME),
                **_get_graph_build_llm_observability(),
                "current_stage": "queued",
            }
        )
        
        # 更新项目状态
        project.status = ProjectStatus.GRAPH_BUILDING
        project.graph_build_task_id = task_id
        ProjectManager.save_project(project)
        
        # 启动后台任务
        def build_task():
            build_logger = get_logger('mirofish.build')
            build_started_at = time.monotonic()
            try:
                build_logger.info(f"[{task_id}] 开始构建图谱...")
                task_manager.update_task(
                    task_id, 
                    status=TaskStatus.PROCESSING,
                    message="初始化图谱构建服务..."
                )
                
                # 创建图谱构建服务
                builder = GraphBuilderService(
                    api_key=Config.ZEP_API_KEY,
                    backend=Config.ZEP_BACKEND,
                    build_mode=True,
                )
                llm_observability = _get_graph_build_llm_observability(builder)
                task_manager.update_task(
                    task_id,
                    progress_detail=_merge_task_progress_detail(
                        task_manager,
                        task_id,
                        **llm_observability,
                    )
                )
                
                # 分块
                split_started_at = time.monotonic()
                task_manager.update_task(
                    task_id,
                    message="文本分块中...",
                    progress=5,
                    progress_detail=_merge_task_progress_detail(
                        task_manager,
                        task_id,
                        current_stage="split_text",
                    )
                )
                chunks = TextProcessor.split_text(
                    text, 
                    chunk_size=chunk_size, 
                    overlap=chunk_overlap
                )
                total_chunks = len(chunks)
                total_batches = (total_chunks + effective_batch_size - 1) // effective_batch_size if effective_batch_size else 0
                split_elapsed = time.monotonic() - split_started_at
                build_logger.info(
                    "[%s] 文本分块完成: text_chars=%s, chunks=%s, chunk_size=%s, overlap=%s, elapsed=%.1fs",
                    task_id,
                    len(text or ""),
                    total_chunks,
                    chunk_size,
                    chunk_overlap,
                    split_elapsed,
                )
                
                # 创建图谱
                task_manager.update_task(
                    task_id,
                    message="创建图谱...",
                    progress=10,
                    progress_detail=_merge_task_progress_detail(
                        task_manager,
                        task_id,
                        current_stage="create_graph",
                        text_length=len(text or ""),
                        total_chunks=total_chunks,
                        total_batches=total_batches,
                    )
                )
                graph_id = builder.create_graph(name=graph_name)

                task_manager.update_task(
                    task_id,
                    progress_detail=_merge_task_progress_detail(
                        task_manager,
                        task_id,
                        current_stage="create_graph",
                        pending_graph_id=graph_id,
                        graph_id=graph_id,
                    )
                )

                # 创建图谱后立即绑定 graph_id，前端轮询才能读取构建中的半成品图谱。
                project.graph_id = graph_id
                project.graph_backend = Config.ZEP_BACKEND
                project.graph_provider = "graphiti" if Config.ZEP_BACKEND == "graphiti" else "zep"
                project.graph_schema_version = "v1"
                ProjectManager.save_project(project)
                
                # 设置本体
                task_manager.update_task(
                    task_id,
                    message="设置本体定义...",
                    progress=15,
                    progress_detail=_merge_task_progress_detail(
                        task_manager,
                        task_id,
                        current_stage="set_ontology",
                        entity_type_count=len(ontology.get("entity_types", []) if isinstance(ontology, dict) else []),
                        edge_type_count=len(ontology.get("edge_types", []) if isinstance(ontology, dict) else []),
                    )
                )
                builder.set_ontology(graph_id, ontology)
                
                graph_progress_floor = 15

                def update_graph_task_progress(message, progress):
                    nonlocal graph_progress_floor
                    progress = max(graph_progress_floor, int(progress))
                    graph_progress_floor = progress
                    task_manager.update_task(
                        task_id,
                        message=message,
                        progress=progress,
                        progress_detail=_merge_task_progress_detail(
                            task_manager,
                            task_id,
                            current_stage="ingest_episodes",
                        )
                    )

                # 添加文本（progress_callback 签名是 (msg, progress_ratio)）
                def add_progress_callback(msg, progress_ratio):
                    progress = 15 + int(progress_ratio * 40)  # 15% - 55%
                    update_graph_task_progress(msg, progress)
                
                task_manager.update_task(
                    task_id,
                    message=f"开始添加 {total_chunks} 个文本块...",
                    progress=15,
                    progress_detail=_merge_task_progress_detail(
                        task_manager,
                        task_id,
                        current_stage="ingest_episodes",
                        total_chunks=total_chunks,
                        total_batches=total_batches,
                        **llm_observability,
                    )
                )
                
                ingest_started_at = time.monotonic()
                episode_uuids = builder.add_text_batches(
                    graph_id, 
                    chunks,
                    batch_size=effective_batch_size,
                    progress_callback=add_progress_callback,
                    extraction_context=extraction_context,
                    concurrency=effective_concurrency,
                )
                ingest_elapsed = time.monotonic() - ingest_started_at
                llm_observability = _get_graph_build_llm_observability(builder)
                build_logger.info(
                    "[%s] 图谱 episode 写入完成: graph_id=%s, chunks=%s, batches=%s, episodes=%s, elapsed=%.1fs, bulk_ingest=%s",
                    task_id,
                    graph_id,
                    total_chunks,
                    total_batches,
                    len(episode_uuids),
                    ingest_elapsed,
                    bulk_ingest_enabled,
                )
                
                # 等待Zep处理完成（查询每个episode的processed状态）
                task_manager.update_task(
                    task_id,
                    message="等待图谱服务处理数据...",
                    progress=55,
                    progress_detail=_merge_task_progress_detail(
                        task_manager,
                        task_id,
                        current_stage="wait_episodes",
                        episode_count=len(episode_uuids),
                        ingest_elapsed_seconds=round(ingest_elapsed, 1),
                        **llm_observability,
                    )
                )
                
                def wait_progress_callback(msg, progress_ratio):
                    progress = 55 + int(progress_ratio * 35)  # 55% - 90%
                    update_graph_task_progress(msg, progress)
                
                builder._wait_for_episodes(episode_uuids, wait_progress_callback)
                
                # 获取图谱数据
                task_manager.update_task(
                    task_id,
                    message="获取图谱数据...",
                    progress=95,
                    progress_detail=_merge_task_progress_detail(
                        task_manager,
                        task_id,
                        current_stage="load_graph_data",
                    )
                )
                graph_data_started_at = time.monotonic()
                graph_data = builder.get_graph_data(graph_id)
                graph_data_elapsed = time.monotonic() - graph_data_started_at
                initial_node_count = graph_data.get("node_count", 0)
                entity_target = max(0, int(Config.GRAPH_MIN_ENTITY_TARGET or 0))
                enrichment_info = {
                    "enabled": bool(Config.GRAPH_ENTITY_ENRICHMENT_ENABLED),
                    "target_node_count": entity_target,
                    "initial_node_count": initial_node_count,
                    "performed": False,
                    "queries": [],
                    "source_count": 0,
                    "final_node_count": initial_node_count,
                    "error": "",
                }

                if Config.GRAPH_ENTITY_ENRICHMENT_ENABLED and entity_target and initial_node_count < entity_target:
                    task_manager.update_task(
                        task_id,
                        message=f"当前实体数 {initial_node_count}，低于目标 {entity_target}，正在联网补充事件相关材料...",
                        progress=96,
                        progress_detail=_merge_task_progress_detail(
                            task_manager,
                            task_id,
                            current_stage="entity_enrichment_search",
                            entity_enrichment=enrichment_info,
                        )
                    )
                    enrichment_queries, enrichment_sources, enrichment_error = _search_entity_enrichment_sources(project, graph_name)
                    enrichment_info.update({
                        "performed": bool(enrichment_sources),
                        "queries": enrichment_queries,
                        "source_count": len(enrichment_sources),
                        "error": enrichment_error,
                    })
                    enrichment_material = _build_entity_enrichment_material(project, enrichment_sources, graph_name)

                    if enrichment_material:
                        enrichment_chunks = TextProcessor.split_text(
                            enrichment_material,
                            chunk_size=chunk_size,
                            overlap=chunk_overlap,
                        )
                        enrichment_context = dict(extraction_context)
                        enrichment_context["entity_hints"] = _extend_entity_hints_from_sources(
                            extraction_context.get("entity_hints") or [],
                            enrichment_sources,
                        )
                        enrichment_context["seed_summary"] = "\n".join(
                            part for part in [
                                extraction_context.get("seed_summary") or "",
                                "联网补充材料用于补足原始文档未覆盖的事件相关人物、机构、媒体、平台和公众群体。",
                            ]
                            if part
                        )

                        task_manager.update_task(
                            task_id,
                            message=f"正在写入 {len(enrichment_sources)} 条联网补充来源，继续扩展图谱实体...",
                            progress=97,
                            progress_detail=_merge_task_progress_detail(
                                task_manager,
                                task_id,
                                current_stage="entity_enrichment_ingest",
                                entity_enrichment=enrichment_info,
                            )
                        )
                        enrichment_started_at = time.monotonic()
                        enrichment_episode_uuids = builder.add_text_batches(
                            graph_id,
                            enrichment_chunks,
                            batch_size=effective_batch_size,
                            progress_callback=None,
                            extraction_context=enrichment_context,
                            concurrency=effective_concurrency,
                        )
                        builder._wait_for_episodes(enrichment_episode_uuids)
                        enrichment_info["ingest_elapsed_seconds"] = round(time.monotonic() - enrichment_started_at, 1)
                        enrichment_info["chunk_count"] = len(enrichment_chunks)
                        enrichment_info["episode_count"] = len(enrichment_episode_uuids)

                        graph_data_started_at = time.monotonic()
                        graph_data = builder.get_graph_data(graph_id)
                        graph_data_elapsed = time.monotonic() - graph_data_started_at
                        enrichment_info["final_node_count"] = graph_data.get("node_count", 0)
                    else:
                        build_logger.warning(
                            "[%s] 实体补充检索未获得可写入材料: graph_id=%s, queries=%s, error=%s",
                            task_id,
                            graph_id,
                            enrichment_queries,
                            enrichment_error,
                        )
                        enrichment_info["performed"] = False

                    final_node_count = int(enrichment_info.get("final_node_count") or graph_data.get("node_count", 0) or 0)
                    if final_node_count < entity_target:
                        raise RuntimeError(
                            "图谱实体数量未达到最低要求: "
                            f"当前 {final_node_count}，最低 {entity_target}。"
                            f"已尝试联网补充，查询数 {len(enrichment_info.get('queries') or [])}，"
                            f"来源数 {enrichment_info.get('source_count') or 0}。"
                            f"{'补充错误: ' + enrichment_info.get('error') if enrichment_info.get('error') else ''}"
                        )
                
                # 更新项目状态
                project.graph_id = graph_id
                project.status = ProjectStatus.GRAPH_COMPLETED
                ProjectManager.save_project(project)
                
                node_count = graph_data.get("node_count", 0)
                edge_count = graph_data.get("edge_count", 0)
                total_elapsed = time.monotonic() - build_started_at
                build_logger.info(
                    "[%s] 图谱构建完成: graph_id=%s, 节点=%s, 边=%s, load_graph_elapsed=%.1fs, total_elapsed=%.1fs",
                    task_id,
                    graph_id,
                    node_count,
                    edge_count,
                    graph_data_elapsed,
                    total_elapsed,
                )
                
                # 完成
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.COMPLETED,
                    message="图谱构建完成",
                    progress=100,
                    progress_detail=_merge_task_progress_detail(
                        task_manager,
                        task_id,
                        current_stage="completed",
                        entity_enrichment=enrichment_info,
                    ),
                    result={
                        "project_id": project_id,
                        "graph_id": graph_id,
                        "node_count": node_count,
                        "edge_count": edge_count,
                        "chunk_count": total_chunks,
                        "batch_size": effective_batch_size,
                        "concurrency": effective_concurrency,
                        "bulk_ingest_enabled": bulk_ingest_enabled,
                        "requested_batch_size": batch_size,
                        "requested_concurrency": graph_build_concurrency,
                        "entity_enrichment": enrichment_info,
                        "ingest_elapsed_seconds": round(ingest_elapsed, 1),
                        "load_graph_elapsed_seconds": round(graph_data_elapsed, 1),
                        "total_elapsed_seconds": round(total_elapsed, 1),
                        **llm_observability,
                    }
                )
                
            except Exception as e:
                # 更新项目状态为失败
                error_message = str(e) or e.__class__.__name__
                build_logger.error(f"[{task_id}] 图谱构建失败: {error_message}")
                build_logger.debug(traceback.format_exc())
                
                project.status = ProjectStatus.FAILED
                project.error = error_message
                ProjectManager.save_project(project)

                task = task_manager.get_task(task_id)
                failure_detail = {
                    **((task.progress_detail if task else {}) or {}),
                    "graph_id": project.graph_id,
                    "backend": project.graph_backend or Config.ZEP_BACKEND,
                }
                
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    message=f"构建失败: {error_message}",
                    error=traceback.format_exc(),
                    progress_detail=failure_detail
                )
        
        # 启动后台线程
        thread = threading.Thread(target=build_task, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "task_id": task_id,
                "message": "图谱构建任务已启动，请通过 /task/{task_id} 查询进度"
            }
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 任务查询接口 ==============

@graph_bp.route('/task/<task_id>', methods=['GET'])
def get_task(task_id: str):
    """
    查询任务状态
    """
    task = TaskManager().get_task(task_id)
    
    if not task:
        return jsonify({
            "success": False,
            "error": f"任务不存在: {task_id}"
        }), 404
    
    return jsonify({
        "success": True,
        "data": task.to_dict()
    })


@graph_bp.route('/tasks', methods=['GET'])
def list_tasks():
    """
    列出所有任务
    """
    tasks = TaskManager().list_tasks()
    
    return jsonify({
        "success": True,
        "data": [t.to_dict() for t in tasks],
        "count": len(tasks)
    })


# ============== 图谱数据接口 ==============

@graph_bp.route('/data/<graph_id>', methods=['GET'])
def get_graph_data(graph_id: str):
    """
    获取图谱数据（节点和边）
    """
    try:
        project, backend, error_response = _get_project_backend_or_404(graph_id)
        if error_response:
            return error_response

        if backend == 'cloud' and not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY未配置（cloud模式需要）"
            }), 500

        builder = GraphBuilderService(backend=backend)
        graph_data = builder.get_graph_data(graph_id)
        graph_data = TypeTranslationService.ensure_graph_data_translations(graph_data)
        
        return jsonify({
            "success": True,
            "data": graph_data
        })
        
    except Exception as e:
        if is_neo4j_auth_error(e):
            logger.error(f"获取图谱数据失败：{format_neo4j_auth_error(e)}")
            return jsonify({
                "success": False,
                "error": format_neo4j_auth_error(e)
            }), 503
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@graph_bp.route('/delete/<graph_id>', methods=['DELETE'])
def delete_graph(graph_id: str):
    """
    删除Zep图谱
    """
    try:
        project, backend, error_response = _get_project_backend_or_404(graph_id)
        if error_response:
            return error_response

        if backend == 'cloud' and not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY未配置（cloud模式需要）"
            }), 500

        builder = GraphBuilderService(backend=backend)
        builder.delete_graph(graph_id)
        
        return jsonify({
            "success": True,
            "message": f"图谱已删除: {graph_id}"
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
