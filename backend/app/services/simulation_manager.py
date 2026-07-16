"""
OASIS模拟管理器
管理Twitter和Reddit双平台并行模拟
使用预设脚本 + LLM智能生成配置参数
"""

import os
import json
import shutil
import csv
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.logger import get_logger
from .zep_entity_reader import ZepEntityReader, FilteredEntities
from .oasis_profile_generator import OasisProfileGenerator, OasisAgentProfile
from .simulation_config_generator import SimulationConfigGenerator, SimulationParameters
from .real_entity_resolver import RealEntityResolver, VERIFIED
from .location_entity_filter import is_location_entity_node

logger = get_logger('mirofish.simulation')


class SimulationStatus(str, Enum):
    """模拟状态"""
    CREATED = "created"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"      # 模拟被手动停止
    COMPLETED = "completed"  # 模拟自然完成
    FAILED = "failed"


class PlatformType(str, Enum):
    """平台类型"""
    TWITTER = "twitter"
    REDDIT = "reddit"


@dataclass
class SimulationState:
    """模拟状态"""
    simulation_id: str
    project_id: str
    graph_id: str
    graph_backend: Optional[str] = None
    
    # 平台启用状态
    enable_twitter: bool = True
    enable_reddit: bool = True
    
    # 状态
    status: SimulationStatus = SimulationStatus.CREATED
    
    # 准备阶段数据
    entities_count: int = 0
    profiles_count: int = 0
    entity_types: List[str] = field(default_factory=list)
    verification_candidate_count: int = 0
    verification_verified_count: int = 0
    verification_skipped_count: int = 0
    verification_skipped_entities: List[Dict[str, Any]] = field(default_factory=list)
    profile_provenance: List[Dict[str, Any]] = field(default_factory=list)
    
    # 配置生成信息
    config_generated: bool = False
    config_reasoning: str = ""
    
    # 运行时数据
    current_round: int = 0
    twitter_status: str = "not_started"
    reddit_status: str = "not_started"
    
    # 时间戳
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # 错误信息
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """完整状态字典（内部使用）"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "graph_backend": self.graph_backend,
            "enable_twitter": self.enable_twitter,
            "enable_reddit": self.enable_reddit,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "verification_candidate_count": self.verification_candidate_count,
            "verification_verified_count": self.verification_verified_count,
            "verification_skipped_count": self.verification_skipped_count,
            "verification_skipped_entities": self.verification_skipped_entities,
            "profile_provenance": self.profile_provenance,
            "config_generated": self.config_generated,
            "config_reasoning": self.config_reasoning,
            "current_round": self.current_round,
            "twitter_status": self.twitter_status,
            "reddit_status": self.reddit_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }
    
    def to_simple_dict(self) -> Dict[str, Any]:
        """简化状态字典（API返回使用）"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "graph_backend": self.graph_backend,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "verification_candidate_count": self.verification_candidate_count,
            "verification_verified_count": self.verification_verified_count,
            "verification_skipped_count": self.verification_skipped_count,
            "verification_skipped_entities": self.verification_skipped_entities,
            "profile_provenance": self.profile_provenance,
            "config_generated": self.config_generated,
            "error": self.error,
        }


class SimulationManager:
    """
    模拟管理器
    
    核心功能：
    1. 从Zep图谱读取实体并过滤
    2. 生成OASIS Agent Profile
    3. 使用LLM智能生成模拟配置参数
    4. 准备预设脚本所需的所有文件
    """
    
    # 模拟数据存储目录
    SIMULATION_DATA_DIR = os.path.join(
        os.path.dirname(__file__), 
        '../../uploads/simulations'
    )
    
    def __init__(self):
        # 确保目录存在
        os.makedirs(self.SIMULATION_DATA_DIR, exist_ok=True)
        
        # 内存中的模拟状态缓存
        self._simulations: Dict[str, SimulationState] = {}
    
    def _get_simulation_dir(self, simulation_id: str) -> str:
        """获取模拟数据目录"""
        sim_dir = os.path.join(self.SIMULATION_DATA_DIR, simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        return sim_dir

    def _resolve_simulation_dir(self, simulation_id: str) -> str:
        """解析模拟数据目录路径，不主动创建目录。"""
        base_dir = os.path.abspath(self.SIMULATION_DATA_DIR)
        sim_dir = os.path.abspath(os.path.join(base_dir, simulation_id))
        if os.path.commonpath([base_dir, sim_dir]) != base_dir:
            raise ValueError("非法的模拟 ID")
        return sim_dir
    
    def _save_simulation_state(self, state: SimulationState):
        """保存模拟状态到文件"""
        sim_dir = self._get_simulation_dir(state.simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        state.updated_at = datetime.now().isoformat()
        
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        
        self._simulations[state.simulation_id] = state
    
    def _load_simulation_state(self, simulation_id: str) -> Optional[SimulationState]:
        """从文件加载模拟状态"""
        if simulation_id in self._simulations:
            return self._simulations[simulation_id]
        
        sim_dir = self._resolve_simulation_dir(simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        if not os.path.exists(state_file):
            return None
        
        with open(state_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=data.get("project_id", ""),
            graph_id=data.get("graph_id", ""),
            graph_backend=data.get("graph_backend"),
            enable_twitter=data.get("enable_twitter", True),
            enable_reddit=data.get("enable_reddit", True),
            status=SimulationStatus(data.get("status", "created")),
            entities_count=data.get("entities_count", 0),
            profiles_count=data.get("profiles_count", 0),
            entity_types=data.get("entity_types", []),
            verification_candidate_count=data.get("verification_candidate_count", 0),
            verification_verified_count=data.get("verification_verified_count", 0),
            verification_skipped_count=data.get("verification_skipped_count", 0),
            verification_skipped_entities=data.get("verification_skipped_entities", []),
            profile_provenance=data.get("profile_provenance", []),
            config_generated=data.get("config_generated", False),
            config_reasoning=data.get("config_reasoning", ""),
            current_round=data.get("current_round", 0),
            twitter_status=data.get("twitter_status", "not_started"),
            reddit_status=data.get("reddit_status", "not_started"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            error=data.get("error"),
        )
        
        self._simulations[simulation_id] = state
        return state

    def delete_simulation(self, simulation_id: str) -> bool:
        """删除单个模拟历史记录及其本地文件。"""
        state = self._load_simulation_state(simulation_id)
        if not state:
            self._simulations.pop(simulation_id, None)
            return False

        sim_dir = self._resolve_simulation_dir(simulation_id)
        if os.path.isdir(sim_dir):
            shutil.rmtree(sim_dir)
        elif os.path.exists(sim_dir):
            os.remove(sim_dir)

        self._simulations.pop(simulation_id, None)
        logger.info("删除模拟历史记录: %s", simulation_id)
        return True
    
    def create_simulation(
        self,
        project_id: str,
        graph_id: str,
        graph_backend: Optional[str] = None,
        enable_twitter: bool = True,
        enable_reddit: bool = True,
    ) -> SimulationState:
        """
        创建新的模拟
        
        Args:
            project_id: 项目ID
            graph_id: Zep图谱ID
            enable_twitter: 是否启用Twitter模拟
            enable_reddit: 是否启用Reddit模拟
            
        Returns:
            SimulationState
        """
        import uuid
        simulation_id = f"sim_{uuid.uuid4().hex[:12]}"
        
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            graph_backend=graph_backend,
            enable_twitter=enable_twitter,
            enable_reddit=enable_reddit,
            status=SimulationStatus.CREATED,
        )
        
        self._save_simulation_state(state)
        logger.info(f"创建模拟: {simulation_id}, project={project_id}, graph={graph_id}")
        
        return state
    
    def prepare_simulation(
        self,
        simulation_id: str,
        simulation_requirement: str,
        document_text: str,
        defined_entity_types: Optional[List[str]] = None,
        use_llm_for_profiles: bool = True,
        progress_callback: Optional[callable] = None,
        parallel_profile_count: int = 3,
        use_real_profiles: bool = True,
        strict_real_mode: bool = False,
        allow_group_agents: bool = True,
        min_source_count: int = 1,
    ) -> SimulationState:
        """
        准备模拟环境（全程自动化）
        
        步骤：
        1. 从Zep图谱读取并过滤实体
        2. 为每个实体生成OASIS Agent Profile（可选LLM增强，支持并行）
        3. 使用LLM智能生成模拟配置参数（时间、活跃度、发言频率等）
        4. 保存配置文件和Profile文件
        5. 复制预设脚本到模拟目录
        
        Args:
            simulation_id: 模拟ID
            simulation_requirement: 模拟需求描述（用于LLM生成配置）
            document_text: 原始文档内容（用于LLM理解背景）
            defined_entity_types: 预定义的实体类型（可选）
            use_llm_for_profiles: 是否使用LLM生成详细人设
            progress_callback: 进度回调函数 (stage, progress, message)
            parallel_profile_count: 并行生成人设的数量，默认3
            use_real_profiles: 是否启用真实资料验证画像
            strict_real_mode: 严格真实模式。默认关闭；启用后只有 verified 实体会进入人设生成。
                关闭时会保留未核验实体，使用图谱上下文生成模拟画像，并保留 verification_status。
            allow_group_agents: 是否允许机构/群体实体进入Agent，默认 true
            min_source_count: verified 所需最少来源数
            
        Returns:
            SimulationState
        """
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"模拟不存在: {simulation_id}")
        
        try:
            state.status = SimulationStatus.PREPARING
            self._save_simulation_state(state)
            
            sim_dir = self._get_simulation_dir(simulation_id)
            
            # ========== 阶段1: 读取并过滤实体 ==========
            if progress_callback:
                progress_callback("reading", 0, "正在连接Zep图谱...")
            
            reader = ZepEntityReader(backend=state.graph_backend or Config.ZEP_BACKEND)
            
            if progress_callback:
                progress_callback("reading", 30, "正在读取节点数据...")
            
            filtered = reader.filter_defined_entities(
                graph_id=state.graph_id,
                defined_entity_types=defined_entity_types,
                enrich_with_edges=True
            )

            agent_candidate_entities = [
                entity for entity in filtered.entities
                if not is_location_entity_node(entity)
            ]
            skipped_location_count = filtered.filtered_count - len(agent_candidate_entities)
            if skipped_location_count > 0:
                logger.warning(
                    "准备模拟时跳过 %s 个地点/场所实体，不进入Agent生成: simulation_id=%s",
                    skipped_location_count,
                    simulation_id,
                )
            
            state.entities_count = len(agent_candidate_entities)
            state.entity_types = sorted({
                entity.get_entity_type() or "Entity"
                for entity in agent_candidate_entities
            })
            
            if progress_callback:
                progress_callback(
                    "reading", 100, 
                    f"完成，共 {len(agent_candidate_entities)} 个可生成Agent的实体",
                    current=len(agent_candidate_entities),
                    total=len(agent_candidate_entities)
                )
            
            if not agent_candidate_entities:
                state.status = SimulationStatus.FAILED
                state.error = "没有找到符合条件的实体，请检查图谱是否正确构建"
                self._save_simulation_state(state)
                return state

            entities_for_profiles = agent_candidate_entities
            resolved_results = []
            resolved_by_uuid = {}

            if use_real_profiles:
                if progress_callback:
                    progress_callback(
                        "verifying_entities", 0,
                        "开始验证真实实体...",
                        current=0,
                        total=len(entities_for_profiles)
                    )

                resolver = RealEntityResolver(
                    min_source_count=min_source_count,
                    allow_group_agents=allow_group_agents,
                    concurrency=Config.REAL_ENTITY_RESOLVE_CONCURRENCY,
                )
                resolved_results = resolver.resolve_entities(entities_for_profiles)
                verified_results = [item for item in resolved_results if item.verification_status == VERIFIED]
                skipped_results = [item for item in resolved_results if item.verification_status != VERIFIED]
                state.verification_candidate_count = len(resolved_results)
                state.verification_verified_count = len(verified_results)
                state.verification_skipped_count = len(skipped_results)
                state.verification_skipped_entities = [item.to_dict() for item in skipped_results]
                logger.info(
                    "真实实体验证完成: simulation_id=%s, candidates=%s, verified=%s, skipped=%s, allow_group_agents=%s",
                    simulation_id,
                    len(resolved_results),
                    len(verified_results),
                    len(skipped_results),
                    allow_group_agents,
                )

                resolution_path = os.path.join(sim_dir, "real_entity_resolution.json")
                with open(resolution_path, 'w', encoding='utf-8') as f:
                    json.dump([item.to_dict() for item in resolved_results], f, ensure_ascii=False, indent=2)

                if progress_callback:
                    progress_callback(
                        "verifying_entities", 100,
                        f"验证完成，verified={len(verified_results)}, skipped={len(skipped_results)}",
                        current=len(resolved_results),
                        total=len(resolved_results)
                    )

                resolved_by_uuid = {item.entity_uuid: item for item in resolved_results}
                if strict_real_mode:
                    verified_uuids = {item.entity_uuid for item in verified_results}
                    entities_for_profiles = [
                        entity for entity in agent_candidate_entities
                        if entity.uuid in verified_uuids
                    ]
                    state.entities_count = len(entities_for_profiles)
                    state.entity_types = sorted({
                        entity.get_entity_type() or "Entity"
                        for entity in entities_for_profiles
                    })

                    if not entities_for_profiles:
                        state.status = SimulationStatus.FAILED
                        state.error = (
                            "严格真实模式下没有通过 verified 的实体，已拒绝生成伪真实人设。"
                            "请检查图谱实体、事件上下文或真实资料来源。"
                        )
                        self._save_simulation_state(state)
                        return state
                else:
                    logger.info(
                        "严格真实模式关闭，保留全部图谱实体生成画像: simulation_id=%s, entities=%s, verified=%s, unverified=%s",
                        simulation_id,
                        len(entities_for_profiles),
                        len(verified_results),
                        len(skipped_results),
                    )
                self._save_simulation_state(state)
            
            # ========== 阶段2: 生成Agent Profile ==========
            total_entities = len(entities_for_profiles)
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 0, 
                    "开始生成...",
                    current=0,
                    total=total_entities
                )
            
            # 传入graph_id以启用Zep检索功能，获取更丰富的上下文
            generator = OasisProfileGenerator(
                graph_id=state.graph_id,
                graph_backend=state.graph_backend or Config.ZEP_BACKEND,
            )
            
            def profile_progress(current, total, msg):
                if progress_callback:
                    progress_callback(
                        "generating_profiles", 
                        int(current / total * 100), 
                        msg,
                        current=current,
                        total=total,
                        item_name=msg
                    )
            
            # 设置实时保存的文件路径（优先使用 Reddit JSON 格式）
            realtime_output_path = None
            realtime_platform = "reddit"
            if state.enable_reddit:
                realtime_output_path = os.path.join(sim_dir, "reddit_profiles.json")
                realtime_platform = "reddit"
            elif state.enable_twitter:
                realtime_output_path = os.path.join(sim_dir, "twitter_profiles.csv")
                realtime_platform = "twitter"
            
            profiles = generator.generate_profiles_from_entities(
                entities=entities_for_profiles,
                use_llm=use_llm_for_profiles,
                progress_callback=profile_progress,
                graph_id=state.graph_id,  # 传入graph_id用于Zep检索
                parallel_count=parallel_profile_count,  # 并行生成数量
                realtime_output_path=realtime_output_path,  # 实时保存路径
                output_platform=realtime_platform,  # 输出格式
                resolved_real_entities=resolved_by_uuid if use_real_profiles else None,
                strict_real_mode=strict_real_mode and use_real_profiles,
            )
            
            state.profiles_count = len(profiles)
            state.profile_provenance = [profile._provenance_dict() for profile in profiles]
            
            # 保存Profile文件（注意：Twitter使用CSV格式，Reddit使用JSON格式）
            # Reddit 已经在生成过程中实时保存了，这里再保存一次确保完整性
            if progress_callback:
                progress_callback(
                    "generating_profiles", 95, 
                    "保存Profile文件...",
                    current=total_entities,
                    total=total_entities
                )
            
            if state.enable_reddit:
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "reddit_profiles.json"),
                    platform="reddit"
                )
            
            if state.enable_twitter:
                # Twitter使用CSV格式！这是OASIS的要求
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "twitter_profiles.csv"),
                    platform="twitter"
                )
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 100, 
                    f"完成，共 {len(profiles)} 个Profile",
                    current=len(profiles),
                    total=len(profiles)
                )
            
            # ========== 阶段3: LLM智能生成模拟配置 ==========
            if progress_callback:
                progress_callback(
                    "generating_config", 0, 
                    "正在分析模拟需求...",
                    current=0,
                    total=3
                )
            
            config_generator = SimulationConfigGenerator()
            
            if progress_callback:
                progress_callback(
                    "generating_config", 30, 
                    "正在调用LLM生成配置...",
                    current=1,
                    total=3
                )
            
            sim_params = config_generator.generate_config(
                simulation_id=simulation_id,
                project_id=state.project_id,
                graph_id=state.graph_id,
                simulation_requirement=simulation_requirement,
                document_text=document_text,
                entities=entities_for_profiles,
                enable_twitter=state.enable_twitter,
                enable_reddit=state.enable_reddit
            )
            
            if progress_callback:
                progress_callback(
                    "generating_config", 70, 
                    "正在保存配置文件...",
                    current=2,
                    total=3
                )
            
            # 保存配置文件
            config_path = os.path.join(sim_dir, "simulation_config.json")
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(sim_params.to_json())
            
            state.config_generated = True
            state.config_reasoning = sim_params.generation_reasoning
            
            if progress_callback:
                progress_callback(
                    "generating_config", 100, 
                    "配置生成完成",
                    current=3,
                    total=3
                )
            
            # 注意：运行脚本保留在 backend/scripts/ 目录，不再复制到模拟目录
            # 启动模拟时，simulation_runner 会从 scripts/ 目录运行脚本
            
            # 更新状态
            state.status = SimulationStatus.READY
            self._save_simulation_state(state)
            
            logger.info(f"模拟准备完成: {simulation_id}, "
                       f"entities={state.entities_count}, profiles={state.profiles_count}")
            
            return state
            
        except Exception as e:
            logger.error(f"模拟准备失败: {simulation_id}, error={str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            state.status = SimulationStatus.FAILED
            state.error = str(e)
            self._save_simulation_state(state)
            raise
    
    def get_simulation(self, simulation_id: str) -> Optional[SimulationState]:
        """获取模拟状态"""
        return self._load_simulation_state(simulation_id)
    
    def list_simulations(self, project_id: Optional[str] = None) -> List[SimulationState]:
        """列出所有模拟"""
        simulations = []
        
        if os.path.exists(self.SIMULATION_DATA_DIR):
            for sim_id in os.listdir(self.SIMULATION_DATA_DIR):
                state = self._load_simulation_state(sim_id)
                if state:
                    if project_id is None or state.project_id == project_id:
                        simulations.append(state)
        
        return simulations

    def _load_profiles_file(self, sim_dir: str, platform: str) -> List[Dict[str, Any]]:
        """按平台读取 profile 文件，兼容 Reddit JSON 与 Twitter CSV/历史 JSON。"""
        normalized_platform = (platform or "reddit").lower()

        if normalized_platform == PlatformType.TWITTER.value:
            csv_path = os.path.join(sim_dir, "twitter_profiles.csv")
            if os.path.exists(csv_path):
                with open(csv_path, 'r', encoding='utf-8', newline='') as f:
                    rows = list(csv.DictReader(f))
                for row in rows:
                    for key in ("source_citations", "runtime_traits", "provenance"):
                        value = row.get(key)
                        if value:
                            try:
                                row[key] = json.loads(value)
                            except json.JSONDecodeError:
                                pass
                return rows

            legacy_json_path = os.path.join(sim_dir, "twitter_profiles.json")
            if os.path.exists(legacy_json_path):
                with open(legacy_json_path, 'r', encoding='utf-8') as f:
                    return json.load(f)

            return []

        profile_path = os.path.join(sim_dir, f"{normalized_platform}_profiles.json")
        if not os.path.exists(profile_path):
            return []

        with open(profile_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_profiles(self, simulation_id: str, platform: str = "reddit") -> List[Dict[str, Any]]:
        """获取模拟的Agent Profile"""
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"模拟不存在: {simulation_id}")
        
        sim_dir = self._get_simulation_dir(simulation_id)
        return self._load_profiles_file(sim_dir, platform)
    
    def get_simulation_config(self, simulation_id: str) -> Optional[Dict[str, Any]]:
        """获取模拟配置"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return None
        
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_run_instructions(self, simulation_id: str) -> Dict[str, str]:
        """获取运行说明"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))
        
        return {
            "simulation_dir": sim_dir,
            "scripts_dir": scripts_dir,
            "config_file": config_path,
            "commands": {
                "twitter": f"python {scripts_dir}/run_twitter_simulation.py --config {config_path}",
                "reddit": f"python {scripts_dir}/run_reddit_simulation.py --config {config_path}",
                "parallel": f"python {scripts_dir}/run_parallel_simulation.py --config {config_path}",
            },
            "instructions": (
                f"1. 激活conda环境: conda activate MiroFish\n"
                f"2. 运行模拟 (脚本位于 {scripts_dir}):\n"
                f"   - 单独运行Twitter: python {scripts_dir}/run_twitter_simulation.py --config {config_path}\n"
                f"   - 单独运行Reddit: python {scripts_dir}/run_reddit_simulation.py --config {config_path}\n"
                f"   - 并行运行双平台: python {scripts_dir}/run_parallel_simulation.py --config {config_path}"
            )
        }
