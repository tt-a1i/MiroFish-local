"""
阿里百炼 WebSearch 服务封装
"""

import json
import re
from typing import Any, Dict, List, Optional
from urllib import error, parse, request

from openai import OpenAI

from ..config import Config
from ..utils.logger import get_logger
from .bocha_search_service import SearchSource


logger = get_logger("mirofish.bailian_web_search")


class BailianWebSearchService:
    """通过百炼 OpenAI 兼容模式调用模型联网搜索并规范化来源。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        client: Optional[Any] = None,
    ):
        self.api_key = api_key or Config.LLM_WEB_SEARCH_API_KEY
        self.base_url = base_url or Config.LLM_WEB_SEARCH_BASE_URL
        self.model = model or Config.LLM_WEB_SEARCH_MODEL
        self.client = client
        self.validate_links = Config.LLM_WEB_SEARCH_VALIDATE_LINKS

    def search(
        self,
        query: str,
        count: Optional[int] = None,
        freshness: Optional[str] = None,
        summary: bool = True,
    ) -> List[SearchSource]:
        """执行百炼联网搜索，并返回统一来源结构。"""
        clean_query = (query or "").strip()
        if not clean_query:
            raise ValueError("搜索关键词不能为空")
        if not self.api_key:
            raise ValueError("LLM_WEB_SEARCH_API_KEY/LLM_API_KEY 未配置")

        requested_count = max(1, min(int(count or Config.BOCHA_WEB_SEARCH_MAX_RESULTS), 20))
        search_count = min(max(requested_count * 2, requested_count), 20)
        client = self.client or OpenAI(api_key=self.api_key, base_url=self.base_url)
        search_options = {
            "forced_search": True,
            "search_strategy": Config.LLM_WEB_SEARCH_STRATEGY,
        }
        freshness_days = self._freshness_to_days(freshness)
        if freshness_days:
            search_options["freshness"] = freshness_days

        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是严谨的联网资料检索助手。必须基于联网搜索结果返回可公开引用的网页来源；"
                        "禁止编造事实、标题或链接。"
                    ),
                },
                {
                    "role": "user",
                    "content": self._build_prompt(clean_query, search_count, freshness, summary),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            extra_body={
                "enable_search": True,
                "search_options": search_options,
            },
        )
        raw_text = self._extract_response_text(response)
        sources = self.parse_sources(raw_text)
        sources = self._filter_live_sources(sources) if self.validate_links else sources
        return sources[:requested_count]

    @staticmethod
    def _build_prompt(query: str, count: int, freshness: Optional[str], summary: bool) -> str:
        freshness_hint = f"时间范围偏好：{freshness}" if freshness else "时间范围偏好：优先近期、权威、可访问来源"
        summary_hint = "每条来源需要提供简短摘要。" if summary else "每条来源只需提供摘要片段。"
        return f"""请联网搜索并围绕关键词整理可引用来源。

关键词：{query}
来源数量：最多 {count} 条
{freshness_hint}
{summary_hint}

请只输出 JSON，格式：
{{
  "sources": [
    {{
      "title": "网页标题",
      "url": "https://...",
      "snippet": "原网页或搜索结果中的关键片段",
      "summary": "基于该来源的简短中文摘要",
      "site_name": "站点名称",
      "date_published": "YYYY-MM-DD 或空字符串"
    }}
  ]
}}

要求：
- sources 必须来自真实联网搜索结果。
- url 必须是 http 或 https 开头的完整链接。
- 优先选择新闻原文、政府/机构公告、企业公告、权威媒体报道，避免搜索结果页、聚合页、论坛搬运页。
- snippet 和 summary 必须尽量包含具体事实、主体、时间、地点、争议点、数据或进展，不能只写泛泛结论。
- 不确定发布时间时 date_published 返回空字符串。
"""

    @classmethod
    def parse_sources(cls, raw_text: str) -> List[SearchSource]:
        """解析模型返回的 JSON 来源列表。"""
        data = cls._loads_json(raw_text)
        raw_sources = data.get("sources") or data.get("results") or []
        if not isinstance(raw_sources, list):
            return []

        sources: List[SearchSource] = []
        seen_urls = set()
        for item in raw_sources:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or item.get("link") or "").strip()
            if not cls._is_valid_http_url(url) or url in seen_urls:
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            sources.append(
                SearchSource(
                    title=title or url,
                    url=url,
                    snippet=str(item.get("snippet") or item.get("content") or "").strip(),
                    summary=str(item.get("summary") or "").strip(),
                    site_name=str(item.get("site_name") or item.get("siteName") or "").strip(),
                    date_published=str(
                        item.get("date_published")
                        or item.get("datePublished")
                        or item.get("published_at")
                        or ""
                    ).strip(),
                    source_type="web",
                )
            )
            seen_urls.add(url)
        return sources

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        try:
            return response.choices[0].message.content or ""
        except Exception:
            logger.warning("百炼联网搜索响应结构异常: %s", response)
            return ""

    @staticmethod
    def _loads_json(raw_text: str) -> Dict[str, Any]:
        clean_text = (raw_text or "").strip()
        if not clean_text:
            return {}
        try:
            parsed = json.loads(clean_text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", clean_text)
            if not match:
                return {}
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _is_valid_http_url(url: str) -> bool:
        return url.startswith("http://") or url.startswith("https://")

    def _filter_live_sources(self, sources: List[SearchSource]) -> List[SearchSource]:
        """过滤重复、明显搜索页、不可访问或已删除的来源。"""
        filtered: List[SearchSource] = []
        seen_urls = set()
        for source in sources:
            normalized_url = self._normalize_url(source.url)
            if not normalized_url or normalized_url in seen_urls:
                continue
            if self._is_low_value_source(normalized_url):
                logger.info("过滤百炼来源：低价值搜索/聚合页 url=%s", normalized_url)
                continue
            if self._looks_deleted_from_metadata(source):
                logger.info("过滤百炼来源：摘要显示已删除或不存在 url=%s", normalized_url)
                continue

            source.url = normalized_url
            is_live, reason, body = self._check_live_source(source)
            if not is_live:
                logger.info("过滤百炼来源：%s url=%s", reason, normalized_url)
                continue
            if body and not source.summary:
                source.summary = self._extract_body_excerpt(body)

            seen_urls.add(normalized_url)
            filtered.append(source)
        return filtered

    def _check_live_source(self, source: SearchSource) -> tuple[bool, str, str]:
        body = ""
        try:
            try:
                status, _ = self._request_source_preview(source.url, method="HEAD")
            except Exception as exc:
                logger.info("百炼来源 HEAD 校验异常，改用 GET 复核 url=%s, error=%s", source.url, exc)
            else:
                if status < 200 or status >= 400:
                    logger.info("百炼来源 HEAD 校验未通过，改用 GET 复核 url=%s, status=%s", source.url, status)
            status, body = self._request_source_preview(source.url, method="GET")
        except Exception as exc:
            return False, f"链接检查异常: {exc}", ""

        if status < 200 or status >= 400:
            if self._looks_blocked_from_body(body):
                return False, f"目标站点拦截后端校验请求 status={status}", body
            return False, f"链接不可访问 status={status}", body
        if self._looks_blocked_from_body(body):
            return False, "目标站点返回拦截页", body
        if self._looks_deleted_from_body(body):
            return False, "页面内容提示已删除或不存在", body
        return True, "", body

    def _request_source_preview(self, url: str, method: str = "GET") -> tuple[int, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        req = request.Request(url=url, method=method, headers=headers)
        try:
            with request.urlopen(req, timeout=Config.BOCHA_LINK_CHECK_TIMEOUT) as resp:
                status = getattr(resp, "status", resp.getcode())
                body = ""
                if method == "GET":
                    raw_body = resp.read(Config.BOCHA_LINK_CHECK_MAX_BYTES)
                    body = raw_body.decode("utf-8", errors="ignore")
                return status, body
        except error.HTTPError as exc:
            body = ""
            if method == "GET":
                raw_body = exc.read(Config.BOCHA_LINK_CHECK_MAX_BYTES)
                body = raw_body.decode("utf-8", errors="ignore")
            return exc.code, body

    @classmethod
    def _normalize_url(cls, url: str) -> str:
        clean_url = (url or "").strip()
        if not cls._is_valid_http_url(clean_url):
            return ""
        parsed = parse.urlsplit(clean_url)
        return parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))

    @staticmethod
    def _is_low_value_source(url: str) -> bool:
        parsed = parse.urlsplit(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        query = parsed.query.lower()
        if "google." in host or "bing.com" in host or "baidu.com" in host:
            return True
        return any(marker in path or marker in query for marker in ("/search", "search?", "query=", "keyword="))

    @classmethod
    def _looks_deleted_from_metadata(cls, source: SearchSource) -> bool:
        text = " ".join([source.title, source.snippet, source.summary]).strip()
        return cls._contains_deleted_marker(text)

    @classmethod
    def _looks_deleted_from_body(cls, body: str) -> bool:
        if not body:
            return False
        text = re.sub(r"\s+", " ", body[: Config.BOCHA_LINK_CHECK_MAX_BYTES]).strip()
        return cls._contains_deleted_marker(text)

    @classmethod
    def _looks_blocked_from_body(cls, body: str) -> bool:
        if not body:
            return False
        text = re.sub(r"\s+", " ", body[: Config.BOCHA_LINK_CHECK_MAX_BYTES]).strip().lower()
        blocked_patterns = [
            "waf拦截页面",
            "web应用防护",
            "您的请求已中断",
            "访问拦截",
            "access denied",
            "request blocked",
        ]
        return any(pattern in text for pattern in blocked_patterns)

    @staticmethod
    def _contains_deleted_marker(text: str) -> bool:
        if not text:
            return False
        lowered = text.lower()
        deleted_patterns = [
            "404 not found",
            "page not found",
            "this page has been deleted",
            "this article has been deleted",
            "410 gone",
            "页面不存在",
            "网页不存在",
            "文件不存在",
            "资源不存在",
            "内容不存在",
            "该内容已被删除",
            "文章已删除",
            "页面已删除",
            "您访问的页面不存在",
            "抱歉，您访问的页面不存在",
            "很抱歉，您访问的页面不存在",
        ]
        return any(pattern in lowered for pattern in deleted_patterns)

    @staticmethod
    def _extract_body_excerpt(body: str) -> str:
        if not body:
            return ""
        text = re.sub(r"<script[\s\S]*?</script>", " ", body, flags=re.IGNORECASE)
        text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;|&#160;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:500]

    @staticmethod
    def _freshness_to_days(freshness: Optional[str]) -> Optional[int]:
        if freshness is None:
            return None
        if isinstance(freshness, int):
            return freshness if freshness > 0 else None
        clean_value = str(freshness).strip()
        if not clean_value:
            return None
        if clean_value.isdigit():
            days = int(clean_value)
            return days if days > 0 else None
        return {
            "oneDay": 1,
            "oneWeek": 7,
            "oneMonth": 30,
            "twoMonths": 60,
            "threeMonths": 90,
            "oneYear": 365,
        }.get(clean_value)
