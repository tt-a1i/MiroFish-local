"""
Zep图谱记忆更新服务
将模拟中的Agent活动动态更新到Zep图谱中

支持双后端：
- Zep Cloud (默认)
- Graphiti + Neo4j 本地部署
"""

import os
import json
import hashlib
import time
import threading
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime, timezone
from queue import Queue, Empty

from ..utils.logger import get_logger
from ..utils.neo4j_errors import format_neo4j_auth_error, is_neo4j_auth_error
from ..utils.time_utils import utc_now_iso, parse_iso_datetime
from .zep_factory import get_zep_client
from .zep_adapter import ZepClientAdapter
from ..config import Config

logger = get_logger('mirofish.zep_graph_memory_updater')

SIMULATION_DATA_DIR = os.path.join(
    os.path.dirname(__file__),
    '../../uploads/simulations'
)

SIMULATION_MEMORY_LABEL = "未来推演记忆"
SIMULATION_MEMORY_LABEL_KEY = "FutureSimulationMemory"

# 双平台推演 episode 字段汉化映射（避免 graphiti LLM 从 JSON payload 中提取英文实体名）
# 仅用于 graphiti backend 的 episode data 序列化，不影响内部追踪字段
EPISODE_FIELD_HANIZATION_MAP = {
    "platform": {
        "twitter": "推特平台",
        "reddit": "Reddit社区",
    },
    "source": {
        "dual_platform_simulation": "双平台推演",
    },
}


@dataclass
class AgentActivity:
    """Agent活动记录"""
    platform: str           # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str        # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any]
    round_num: int
    timestamp: str

    @classmethod
    def from_action_dict(cls, data: Dict[str, Any], platform: str) -> "AgentActivity":
        """从 actions.jsonl 的一条动作记录恢复 activity。"""
        return cls(
            platform=platform,
            agent_id=data.get("agent_id", 0),
            agent_name=data.get("agent_name", ""),
            action_type=data.get("action_type", ""),
            action_args=data.get("action_args", {}),
            round_num=data.get("round", data.get("round_num", 0)),
            timestamp=data.get("timestamp", utc_now_iso()),
        )

    def build_activity_id(self) -> str:
        """为 activity 生成稳定幂等 ID。"""
        canonical_args = json.dumps(self.action_args or {}, ensure_ascii=False, sort_keys=True)
        raw = "|".join([
            self.platform or "",
            str(self.round_num),
            str(self.agent_id),
            self.agent_name or "",
            self.action_type or "",
            self.timestamp or "",
            canonical_args,
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_episode_payload(self) -> Dict[str, Any]:
        """构造结构化 episode payload。"""
        return {
            "platform": self.platform,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "action_type": self.action_type,
            "action_args": self.action_args or {},
            "round_num": self.round_num,
            "timestamp": self.timestamp,
            "activity_id": self.build_activity_id(),
            "source": "dual_platform_simulation",
            "memory_label": SIMULATION_MEMORY_LABEL,
            "memory_label_key": SIMULATION_MEMORY_LABEL_KEY,
            "episode_text": self.to_episode_text(),
        }
    
    def to_episode_text(self) -> str:
        """
        将活动转换为可以发送给Zep的文本描述
        
        采用自然语言描述格式，让Zep能够从中提取实体和关系
        不添加模拟相关的前缀，避免误导图谱更新
        """
        # 根据不同的动作类型生成不同的描述
        action_descriptions = {
            "CREATE_POST": self._describe_create_post,
            "LIKE_POST": self._describe_like_post,
            "DISLIKE_POST": self._describe_dislike_post,
            "REPOST": self._describe_repost,
            "QUOTE_POST": self._describe_quote_post,
            "FOLLOW": self._describe_follow,
            "CREATE_COMMENT": self._describe_create_comment,
            "LIKE_COMMENT": self._describe_like_comment,
            "DISLIKE_COMMENT": self._describe_dislike_comment,
            "SEARCH_POSTS": self._describe_search,
            "SEARCH_USER": self._describe_search_user,
            "MUTE": self._describe_mute,
        }
        
        describe_func = action_descriptions.get(self.action_type, self._describe_generic)
        description = describe_func()
        
        # 直接返回 "agent名称: 活动描述" 格式，不添加模拟前缀
        return f"{self.agent_name}: {description}"
    
    def _describe_create_post(self) -> str:
        content = self.action_args.get("content", "")
        if content:
            return f"发布了一条帖子：「{content}」"
        return "发布了一条帖子"
    
    def _describe_like_post(self) -> str:
        """点赞帖子 - 包含帖子原文和作者信息"""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f"点赞了{post_author}的帖子：「{post_content}」"
        elif post_content:
            return f"点赞了一条帖子：「{post_content}」"
        elif post_author:
            return f"点赞了{post_author}的一条帖子"
        return "点赞了一条帖子"
    
    def _describe_dislike_post(self) -> str:
        """踩帖子 - 包含帖子原文和作者信息"""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f"踩了{post_author}的帖子：「{post_content}」"
        elif post_content:
            return f"踩了一条帖子：「{post_content}」"
        elif post_author:
            return f"踩了{post_author}的一条帖子"
        return "踩了一条帖子"
    
    def _describe_repost(self) -> str:
        """转发帖子 - 包含原帖内容和作者信息"""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        
        if original_content and original_author:
            return f"转发了{original_author}的帖子：「{original_content}」"
        elif original_content:
            return f"转发了一条帖子：「{original_content}」"
        elif original_author:
            return f"转发了{original_author}的一条帖子"
        return "转发了一条帖子"
    
    def _describe_quote_post(self) -> str:
        """引用帖子 - 包含原帖内容、作者信息和引用评论"""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        quote_content = self.action_args.get("quote_content", "") or self.action_args.get("content", "")
        
        base = ""
        if original_content and original_author:
            base = f"引用了{original_author}的帖子「{original_content}」"
        elif original_content:
            base = f"引用了一条帖子「{original_content}」"
        elif original_author:
            base = f"引用了{original_author}的一条帖子"
        else:
            base = "引用了一条帖子"
        
        if quote_content:
            base += f"，并评论道：「{quote_content}」"
        return base
    
    def _describe_follow(self) -> str:
        """关注用户 - 包含被关注用户的名称"""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f"关注了用户「{target_user_name}」"
        return "关注了一个用户"
    
    def _describe_create_comment(self) -> str:
        """发表评论 - 包含评论内容和所评论的帖子信息"""
        content = self.action_args.get("content", "")
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if content:
            if post_content and post_author:
                return f"在{post_author}的帖子「{post_content}」下评论道：「{content}」"
            elif post_content:
                return f"在帖子「{post_content}」下评论道：「{content}」"
            elif post_author:
                return f"在{post_author}的帖子下评论道：「{content}」"
            return f"评论道：「{content}」"
        return "发表了评论"
    
    def _describe_like_comment(self) -> str:
        """点赞评论 - 包含评论内容和作者信息"""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f"点赞了{comment_author}的评论：「{comment_content}」"
        elif comment_content:
            return f"点赞了一条评论：「{comment_content}」"
        elif comment_author:
            return f"点赞了{comment_author}的一条评论"
        return "点赞了一条评论"
    
    def _describe_dislike_comment(self) -> str:
        """踩评论 - 包含评论内容和作者信息"""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f"踩了{comment_author}的评论：「{comment_content}」"
        elif comment_content:
            return f"踩了一条评论：「{comment_content}」"
        elif comment_author:
            return f"踩了{comment_author}的一条评论"
        return "踩了一条评论"
    
    def _describe_search(self) -> str:
        """搜索帖子 - 包含搜索关键词"""
        query = self.action_args.get("query", "") or self.action_args.get("keyword", "")
        return f"搜索了「{query}」" if query else "进行了搜索"
    
    def _describe_search_user(self) -> str:
        """搜索用户 - 包含搜索关键词"""
        query = self.action_args.get("query", "") or self.action_args.get("username", "")
        return f"搜索了用户「{query}」" if query else "搜索了用户"
    
    def _describe_mute(self) -> str:
        """屏蔽用户 - 包含被屏蔽用户的名称"""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f"屏蔽了用户「{target_user_name}」"
        return "屏蔽了一个用户"
    
    def _describe_generic(self) -> str:
        # 对于未知的动作类型，生成通用描述
        return f"执行了{self.action_type}操作"


class ZepGraphMemoryUpdater:
    """
    Zep图谱记忆更新器
    
    监控模拟的actions日志文件，将新的agent活动实时更新到Zep图谱中。
    按平台分组，每累积BATCH_SIZE条活动后批量发送到Zep。
    
    所有有意义的行为都会被更新到Zep，action_args中会包含完整的上下文信息：
    - 点赞/踩的帖子原文
    - 转发/引用的帖子原文
    - 关注/屏蔽的用户名
    - 点赞/踩的评论原文
    """
    
    # 批量发送大小（每个平台累积多少条后发送）
    BATCH_SIZE = 5
    
    # 发送间隔（秒），避免请求过快
    SEND_INTERVAL = 0.5
    
    # 重试配置
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # 秒
    
    def __init__(
        self,
        graph_id: str,
        api_key: Optional[str] = None,
        backend: Optional[str] = None,
        simulation_id: Optional[str] = None,
    ):
        """
        初始化更新器

        Args:
            graph_id: Zep图谱ID
            api_key: Zep API Key（可选，已废弃，使用工厂模式）
        """
        self.graph_id = graph_id
        self.backend = backend or Config.ZEP_BACKEND
        self.simulation_id = simulation_id
        self._outbox_path = self._build_outbox_path(simulation_id) if simulation_id else None
        self._outbox_lock = threading.Lock()
        self._outbox = self._load_outbox()

        # 使用单例获取适配器（避免重复初始化）
        self.client: ZepClientAdapter = get_zep_client(backend=self.backend)
        
        # 活动队列
        self._activity_queue: Queue = Queue()
        
        # 按平台分组的活动缓冲区（每个平台各自累积到BATCH_SIZE后批量发送）
        self._platform_buffers: Dict[str, List[AgentActivity]] = {
            'twitter': [],
            'reddit': [],
        }
        self._buffer_lock = threading.Lock()
        
        # 控制标志
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        
        # 统计
        self._total_activities = 0  # 实际添加到队列的活动数
        self._total_sent = 0        # 成功发送到Zep的批次数
        self._total_items_sent = 0  # 成功发送到Zep的活动条数
        self._failed_count = 0      # 发送失败的批次数
        self._skipped_count = 0     # 被过滤跳过的活动数（DO_NOTHING）
        self._auth_error: Optional[str] = None  # Neo4j 认证错误会触发本轮写回熔断
        
        logger.info(f"ZepGraphMemoryUpdater 初始化完成: graph_id={graph_id}, batch_size={self.BATCH_SIZE}")

    def _build_outbox_path(self, simulation_id: str) -> str:
        sim_dir = os.path.join(SIMULATION_DATA_DIR, simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        return os.path.join(sim_dir, "graph_memory_outbox.json")

    def _load_outbox(self) -> Dict[str, Dict[str, Any]]:
        if not self._outbox_path or not os.path.exists(self._outbox_path):
            return {}
        try:
            with open(self._outbox_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"读取 graph memory outbox 失败，使用空状态继续: {e}")
            return {}

    def _save_outbox(self):
        if not self._outbox_path:
            return
        with open(self._outbox_path, 'w', encoding='utf-8') as f:
            json.dump(self._outbox, f, ensure_ascii=False, indent=2)

    def _mark_outbox_record(self, activity_id: str, **updates):
        if not self._outbox_path:
            return
        try:
            with self._outbox_lock:
                record = self._outbox.setdefault(activity_id, {})
                record.update(updates)
                self._save_outbox()
        except Exception as e:
            logger.error(
                "写入 graph memory outbox 失败: graph_id=%s, activity_id=%s, error=%s",
                self.graph_id,
                activity_id,
                e,
            )

    def _iter_activity_log_records(self):
        if not self.simulation_id:
            return

        sim_dir = os.path.join(SIMULATION_DATA_DIR, self.simulation_id)
        for platform in ("twitter", "reddit"):
            log_path = os.path.join(sim_dir, platform, "actions.jsonl")
            if not os.path.exists(log_path):
                continue
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if "event_type" in data:
                            continue
                        activity = AgentActivity.from_action_dict(data, platform)
                        if activity.action_type == "DO_NOTHING":
                            continue
                        yield activity
            except Exception as e:
                logger.warning(
                    "读取 activity 日志用于 outbox 恢复失败: simulation_id=%s, platform=%s, error=%s",
                    self.simulation_id,
                    platform,
                    e,
                )

    def _hydrate_outbox_from_action_logs(self) -> int:
        """用 actions.jsonl 回填旧 outbox 缺失的可重放字段。"""
        if not self._outbox_path:
            return 0

        hydrated = 0
        for activity in self._iter_activity_log_records() or []:
            activity_id = activity.build_activity_id()
            with self._outbox_lock:
                record = self._outbox.get(activity_id)
            if not record or record.get("status") == "sent":
                continue
            if record.get("activity"):
                continue

            try:
                request = self._build_episode_request(activity)
            except Exception as e:
                self._mark_outbox_record(activity_id, last_error=str(e))
                continue

            self._mark_outbox_record(
                activity_id,
                activity=activity.to_episode_payload(),
                graph_id=self.graph_id,
                simulation_id=self.simulation_id,
                platform=activity.platform,
                action_type=activity.action_type,
                agent_name=activity.agent_name,
                timestamp=activity.timestamp,
                round_num=activity.round_num,
                reference_time=request["reference_time_iso"],
                episode_type=request["episode_type"],
                payload_preview=request["payload_preview"],
            )
            hydrated += 1

        return hydrated

    def _activity_from_outbox_record(self, activity_id: str, record: Dict[str, Any]) -> Optional[AgentActivity]:
        payload = record.get("activity") or {}
        if not isinstance(payload, dict):
            payload = {}

        platform = payload.get("platform") or record.get("platform")
        action_type = payload.get("action_type") or record.get("action_type")
        timestamp = payload.get("timestamp") or record.get("timestamp")
        if not platform or not action_type or not timestamp:
            return None

        return AgentActivity(
            platform=platform,
            agent_id=payload.get("agent_id", record.get("agent_id", 0)),
            agent_name=payload.get("agent_name", record.get("agent_name", "")),
            action_type=action_type,
            action_args=payload.get("action_args", record.get("action_args", {})) or {},
            round_num=payload.get("round_num", record.get("round_num", 0)),
            timestamp=timestamp,
        )

    def _is_activity_sent(self, activity_id: str) -> bool:
        with self._outbox_lock:
            record = self._outbox.get(activity_id)
            return bool(record and record.get("status") == "sent")

    def _build_episode_request(self, activity: AgentActivity) -> Dict[str, Any]:
        payload = activity.to_episode_payload()
        activity_id = payload["activity_id"]
        reference_time = parse_iso_datetime(activity.timestamp)

        if self.backend == "graphiti":
            # 汉化 episode payload 中的英文字段值（如 platform、source），
            # 避免 graphiti LLM 从 JSON payload 中提取英文实体名
            graphiti_payload = dict(payload)
            for field, mapping in EPISODE_FIELD_HANIZATION_MAP.items():
                original = graphiti_payload.get(field)
                if original and original in mapping:
                    graphiti_payload[field] = mapping[original]
            return {
                "activity_id": activity_id,
                "data": json.dumps(graphiti_payload, ensure_ascii=False, sort_keys=True),
                "episode_type": "json",
                "reference_time": reference_time,
                "reference_time_iso": reference_time.isoformat(),
                "payload_preview": graphiti_payload["episode_text"],
            }

        return {
            "activity_id": activity_id,
            "data": payload["episode_text"],
            "episode_type": "text",
            "reference_time": reference_time,
            "reference_time_iso": reference_time.isoformat(),
            "payload_preview": payload["episode_text"],
        }
    
    def start(self):
        """启动后台工作线程"""
        if self._running:
            return
        
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"ZepMemoryUpdater-{self.graph_id[:8]}"
        )
        self._worker_thread.start()
        logger.info(f"ZepGraphMemoryUpdater 已启动: graph_id={self.graph_id}")
    
    def stop(self, flush: bool = True, timeout: Optional[float] = None):
        """停止后台工作线程"""
        self._running = False

        if flush:
            # 发送剩余的活动；停止服务或正常结束时尽力写回。
            self._flush_remaining()
        else:
            dropped_count = self._drop_pending_activities()
            if dropped_count:
                logger.info(
                    "重跑模拟时丢弃旧图谱记忆待写队列: graph_id=%s, dropped=%s",
                    self.graph_id,
                    dropped_count,
                )
        
        if self._worker_thread and self._worker_thread.is_alive():
            join_timeout = Config.GRAPH_MEMORY_STOP_TIMEOUT_SECONDS if timeout is None else timeout
            self._worker_thread.join(timeout=max(0, float(join_timeout or 0)))
            if self._worker_thread.is_alive():
                logger.warning(
                    "图谱记忆更新器停止等待超时，后台线程将自行退出: graph_id=%s",
                    self.graph_id,
                )
        
        logger.info(f"ZepGraphMemoryUpdater 已停止: graph_id={self.graph_id}, "
                   f"total_activities={self._total_activities}, "
                   f"batches_sent={self._total_sent}, "
                   f"items_sent={self._total_items_sent}, "
                   f"failed={self._failed_count}, "
                   f"skipped={self._skipped_count}")
    
    def add_activity(self, activity: AgentActivity):
        """
        添加一个agent活动到队列
        
        所有有意义的行为都会被添加到队列，包括：
        - CREATE_POST（发帖）
        - CREATE_COMMENT（评论）
        - QUOTE_POST（引用帖子）
        - SEARCH_POSTS（搜索帖子）
        - SEARCH_USER（搜索用户）
        - LIKE_POST/DISLIKE_POST（点赞/踩帖子）
        - REPOST（转发）
        - FOLLOW（关注）
        - MUTE（屏蔽）
        - LIKE_COMMENT/DISLIKE_COMMENT（点赞/踩评论）
        
        action_args中会包含完整的上下文信息（如帖子原文、用户名等）。
        
        Args:
            activity: Agent活动记录
        """
        # 跳过DO_NOTHING类型的活动
        if activity.action_type == "DO_NOTHING":
            self._skipped_count += 1
            return
        
        self._activity_queue.put(activity)
        self._total_activities += 1
        logger.debug(f"添加活动到Zep队列: {activity.agent_name} - {activity.action_type}")
    
    def add_activity_from_dict(self, data: Dict[str, Any], platform: str):
        """
        从字典数据添加活动
        
        Args:
            data: 从actions.jsonl解析的字典数据
            platform: 平台名称 (twitter/reddit)
        """
        # 跳过事件类型的条目
        if "event_type" in data:
            return
        
        activity = AgentActivity(
            platform=platform,
            agent_id=data.get("agent_id", 0),
            agent_name=data.get("agent_name", ""),
            action_type=data.get("action_type", ""),
            action_args=data.get("action_args", {}),
            round_num=data.get("round", 0),
            timestamp=data.get("timestamp", utc_now_iso()),
        )
        
        self.add_activity(activity)
    
    def _worker_loop(self):
        """后台工作循环 - 按平台批量发送活动到Zep"""
        while self._running or not self._activity_queue.empty():
            try:
                # 尝试从队列获取活动（超时1秒）
                try:
                    activity = self._activity_queue.get(timeout=1)
                    
                    # 将活动添加到对应平台的缓冲区
                    platform = activity.platform.lower()
                    with self._buffer_lock:
                        if platform not in self._platform_buffers:
                            self._platform_buffers[platform] = []
                        self._platform_buffers[platform].append(activity)
                        
                        # 检查该平台是否达到批量大小
                        if len(self._platform_buffers[platform]) >= self.BATCH_SIZE:
                            batch = self._platform_buffers[platform][:self.BATCH_SIZE]
                            self._platform_buffers[platform] = self._platform_buffers[platform][self.BATCH_SIZE:]
                            # 释放锁后再发送
                            self._send_batch_activities(batch, platform)
                            # 发送间隔，避免请求过快
                            time.sleep(self.SEND_INTERVAL)
                    
                except Empty:
                    pass
                    
            except Exception as e:
                logger.error(f"工作循环异常: {e}")
                time.sleep(1)
    
    def _send_batch_activities(self, activities: List[AgentActivity], platform: str):
        """
        批量发送活动到Zep图谱
        
        Args:
            activities: Agent活动列表
            platform: 平台名称
        """
        if not activities:
            return

        succeeded = 0
        failed = 0

        for activity in activities:
            if self._send_single_activity(activity):
                succeeded += 1
            else:
                failed += 1

        if succeeded > 0:
            self._total_sent += 1
            self._total_items_sent += succeeded
            logger.info(
                f"成功发送 {succeeded}/{len(activities)} 条{platform}活动到图谱 {self.graph_id}"
            )
            logger.debug(
                f"活动时间范围: first={activities[0].timestamp}, last={activities[-1].timestamp}"
            )

        if failed > 0:
            self._failed_count += failed
            logger.warning(
                f"{platform} 活动写回存在失败: failed={failed}, total={len(activities)}, graph_id={self.graph_id}"
            )

    def _send_single_activity(self, activity: AgentActivity) -> bool:
        """逐条发送 activity，避免整批重放导致重复写入。"""
        activity_id = activity.build_activity_id()

        try:
            request = self._build_episode_request(activity)
        except Exception as e:
            self._mark_outbox_record(
                activity_id,
                status="failed",
                graph_id=self.graph_id,
                simulation_id=self.simulation_id,
                platform=activity.platform,
                action_type=activity.action_type,
                agent_name=activity.agent_name,
                timestamp=activity.timestamp,
                last_error=str(e),
            )
            logger.error(
                "活动时间戳非法，拒绝写回: graph_id=%s, platform=%s, agent=%s, action=%s, "
                "timestamp=%s, activity_id=%s, error=%s",
                self.graph_id,
                activity.platform,
                activity.agent_name,
                activity.action_type,
                activity.timestamp,
                activity_id,
                e,
            )
            return False

        if self._is_activity_sent(activity_id):
            logger.debug(
                "跳过已成功写回的 activity: graph_id=%s, activity_id=%s",
                self.graph_id,
                activity_id,
            )
            return True

        if self._auth_error:
            self._mark_outbox_record(
                activity_id,
                status="blocked",
                last_error=self._auth_error,
            )
            logger.warning(
                "跳过活动写回：Neo4j 认证错误熔断中: graph_id=%s, activity_id=%s, error=%s",
                self.graph_id,
                activity_id,
                self._auth_error,
            )
            return False

        self._mark_outbox_record(
            activity_id,
            status="pending",
            graph_id=self.graph_id,
            simulation_id=self.simulation_id,
            platform=activity.platform,
            agent_id=activity.agent_id,
            action_type=activity.action_type,
            agent_name=activity.agent_name,
            timestamp=activity.timestamp,
            round_num=activity.round_num,
            activity=activity.to_episode_payload(),
            reference_time=request["reference_time_iso"],
            episode_type=request["episode_type"],
            payload_preview=request["payload_preview"],
        )

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                self._mark_outbox_record(
                    activity_id,
                    status="sending",
                    attempt_count=attempt,
                    last_error=None,
                )
                episode_uuid = self.client.add_episode(
                    graph_id=self.graph_id,
                    data=request["data"],
                    episode_type=request["episode_type"],
                    reference_time=request["reference_time"],
                )
                self._mark_outbox_record(
                    activity_id,
                    status="sent",
                    episode_uuid=episode_uuid,
                    sent_at=utc_now_iso(),
                )
                return True
            except Exception as e:
                if self.backend == "graphiti" and is_neo4j_auth_error(e):
                    self._auth_error = format_neo4j_auth_error(e)
                    self._mark_outbox_record(
                        activity_id,
                        status="blocked",
                        attempt_count=attempt,
                        last_error=self._auth_error,
                    )
                    logger.error(
                        "发送活动到Zep失败：检测到 Neo4j 认证错误，停止本轮重试: "
                        "graph_id=%s, platform=%s, agent=%s, action=%s, "
                        "timestamp=%s, attempt=%s/%s, activity_id=%s, error=%s",
                        self.graph_id,
                        activity.platform,
                        activity.agent_name,
                        activity.action_type,
                        activity.timestamp,
                        attempt,
                        self.MAX_RETRIES,
                        activity_id,
                        self._auth_error,
                    )
                    return False

                if attempt >= self.MAX_RETRIES:
                    self._mark_outbox_record(
                        activity_id,
                        status="failed",
                        attempt_count=attempt,
                        last_error=str(e),
                    )
                    logger.error(
                        "发送活动到Zep失败: graph_id=%s, platform=%s, agent=%s, action=%s, "
                        "timestamp=%s, attempts=%s, activity_id=%s, error=%s",
                        self.graph_id,
                        activity.platform,
                        activity.agent_name,
                        activity.action_type,
                        activity.timestamp,
                        self.MAX_RETRIES,
                        activity_id,
                        e,
                    )
                    return False

                self._mark_outbox_record(
                    activity_id,
                    status="retrying",
                    attempt_count=attempt,
                    last_error=str(e),
                )
                logger.warning(
                    "发送活动到Zep失败，准备重试: graph_id=%s, platform=%s, agent=%s, action=%s, "
                    "attempt=%s/%s, activity_id=%s, error=%s",
                    self.graph_id,
                    activity.platform,
                    activity.agent_name,
                    activity.action_type,
                    attempt,
                    self.MAX_RETRIES,
                    activity_id,
                    e,
                )
                time.sleep(self.RETRY_DELAY)

    def replay_failed_outbox(
        self,
        statuses: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        重新发送 outbox 中失败或未完成的活动。

        旧版本 outbox 可能没有保存 action_args，本方法会先尝试从 actions.jsonl
        回填可重放字段，再逐条走原有幂等发送逻辑。
        """
        statuses = statuses or ["failed", "blocked"]
        hydrated_count = self._hydrate_outbox_from_action_logs()

        with self._outbox_lock:
            candidates = [
                (activity_id, dict(record))
                for activity_id, record in self._outbox.items()
                if record.get("status") in statuses
            ]

        if limit is not None and limit > 0:
            candidates = candidates[:limit]

        stats = {
            "simulation_id": self.simulation_id,
            "graph_id": self.graph_id,
            "backend": self.backend,
            "statuses": statuses,
            "hydrated_count": hydrated_count,
            "attempted": 0,
            "sent": 0,
            "failed": 0,
            "skipped": 0,
            "missing_payload": 0,
        }

        for activity_id, record in candidates:
            activity = self._activity_from_outbox_record(activity_id, record)
            if not activity:
                stats["missing_payload"] += 1
                self._mark_outbox_record(
                    activity_id,
                    status="failed",
                    last_error="缺少可重放 activity payload，请确认 actions.jsonl 未被清理",
                )
                continue

            if activity.build_activity_id() != activity_id:
                stats["skipped"] += 1
                self._mark_outbox_record(
                    activity_id,
                    status="failed",
                    last_error="activity payload 与 outbox activity_id 不匹配",
                )
                continue

            stats["attempted"] += 1
            if self._send_single_activity(activity):
                stats["sent"] += 1
            else:
                stats["failed"] += 1

        return stats
    
    def _flush_remaining(self):
        """发送队列和缓冲区中剩余的活动"""
        # 首先处理队列中剩余的活动，添加到缓冲区
        while not self._activity_queue.empty():
            try:
                activity = self._activity_queue.get_nowait()
                platform = activity.platform.lower()
                with self._buffer_lock:
                    if platform not in self._platform_buffers:
                        self._platform_buffers[platform] = []
                    self._platform_buffers[platform].append(activity)
            except Empty:
                break
        
        # 然后发送各平台缓冲区中剩余的活动（即使不足BATCH_SIZE条）
        with self._buffer_lock:
            for platform, buffer in self._platform_buffers.items():
                if buffer:
                    logger.info(f"发送{platform}平台剩余的 {len(buffer)} 条活动")
                    self._send_batch_activities(buffer, platform)
            # 清空所有缓冲区
            for platform in self._platform_buffers:
                self._platform_buffers[platform] = []

    def _drop_pending_activities(self) -> int:
        """强制重跑时丢弃旧运行的待写队列，避免阻塞新模拟启动。"""
        dropped = 0
        while not self._activity_queue.empty():
            try:
                self._activity_queue.get_nowait()
                dropped += 1
            except Empty:
                break

        with self._buffer_lock:
            for platform, buffer in self._platform_buffers.items():
                dropped += len(buffer)
                self._platform_buffers[platform] = []

        return dropped
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._buffer_lock:
            buffer_sizes = {p: len(b) for p, b in self._platform_buffers.items()}
        
        return {
            "graph_id": self.graph_id,
            "batch_size": self.BATCH_SIZE,
            "total_activities": self._total_activities,  # 添加到队列的活动总数
            "batches_sent": self._total_sent,            # 成功发送的批次数
            "items_sent": self._total_items_sent,        # 成功发送的活动条数
            "failed_count": self._failed_count,          # 发送失败的批次数
            "skipped_count": self._skipped_count,        # 被过滤跳过的活动数（DO_NOTHING）
            "queue_size": self._activity_queue.qsize(),
            "buffer_sizes": buffer_sizes,                # 各平台缓冲区大小
            "running": self._running,
        }


class ZepGraphMemoryManager:
    """
    管理多个模拟的Zep图谱记忆更新器
    
    每个模拟可以有自己的更新器实例
    """
    
    _updaters: Dict[str, ZepGraphMemoryUpdater] = {}
    _lock = threading.Lock()
    
    @classmethod
    def create_updater(
        cls,
        simulation_id: str,
        graph_id: str,
        backend: Optional[str] = None,
    ) -> ZepGraphMemoryUpdater:
        """
        为模拟创建图谱记忆更新器
        
        Args:
            simulation_id: 模拟ID
            graph_id: Zep图谱ID
            
        Returns:
            ZepGraphMemoryUpdater实例
        """
        with cls._lock:
            # 如果已存在，先停止旧的
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop(
                    flush=False,
                    timeout=Config.GRAPH_MEMORY_STOP_TIMEOUT_SECONDS,
                )
                del cls._updaters[simulation_id]
            
            updater = ZepGraphMemoryUpdater(
                graph_id,
                backend=backend,
                simulation_id=simulation_id,
            )
            updater.start()
            cls._updaters[simulation_id] = updater
            
            logger.info(f"创建图谱记忆更新器: simulation_id={simulation_id}, graph_id={graph_id}")
            return updater
    
    @classmethod
    def get_updater(cls, simulation_id: str) -> Optional[ZepGraphMemoryUpdater]:
        """获取模拟的更新器"""
        return cls._updaters.get(simulation_id)
    
    @classmethod
    def stop_updater(
        cls,
        simulation_id: str,
        flush: bool = True,
        timeout: Optional[float] = None,
    ):
        """停止并移除模拟的更新器"""
        with cls._lock:
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop(flush=flush, timeout=timeout)
                del cls._updaters[simulation_id]
                logger.info(f"已停止图谱记忆更新器: simulation_id={simulation_id}")
    
    # 防止 stop_all 重复调用的标志
    _stop_all_done = False
    
    @classmethod
    def stop_all(cls):
        """停止所有更新器"""
        # 防止重复调用
        if cls._stop_all_done:
            return
        cls._stop_all_done = True
        
        with cls._lock:
            if cls._updaters:
                for simulation_id, updater in list(cls._updaters.items()):
                    try:
                        updater.stop(
                            flush=False,
                            timeout=Config.GRAPH_MEMORY_STOP_TIMEOUT_SECONDS,
                        )
                    except Exception as e:
                        logger.error(f"停止更新器失败: simulation_id={simulation_id}, error={e}")
                cls._updaters.clear()
            logger.info("已停止所有图谱记忆更新器")
    
    @classmethod
    def get_all_stats(cls) -> Dict[str, Dict[str, Any]]:
        """获取所有更新器的统计信息"""
        return {
            sim_id: updater.get_stats() 
            for sim_id, updater in cls._updaters.items()
        }
