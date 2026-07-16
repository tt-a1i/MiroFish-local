"""
配置管理
统一从项目根目录的 .env 文件加载配置
"""

import os
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件
# 路径: MiroFish/.env (相对于 backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env)
else:
    # 如果根目录没有 .env，尝试加载环境变量（用于生产环境）
    load_dotenv()

# Graphiti 需要 OPENAI_* 环境变量，从 LLM_* 映射
# 仅在未显式设置时才映射，避免覆盖用户的显式配置
if not os.environ.get('OPENAI_API_KEY') and os.environ.get('LLM_API_KEY'):
    os.environ['OPENAI_API_KEY'] = os.environ['LLM_API_KEY']
if not os.environ.get('OPENAI_BASE_URL') and os.environ.get('LLM_BASE_URL'):
    os.environ['OPENAI_BASE_URL'] = os.environ['LLM_BASE_URL']


class Config:
    """Flask配置类"""
    
    # Flask配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    # JSON配置 - 禁用ASCII转义，让中文直接显示（而不是 \uXXXX 格式）
    JSON_AS_ASCII = False
    
    # LLM配置（统一使用OpenAI格式）
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')
    LLM_BOOST_API_KEY = os.environ.get('LLM_BOOST_API_KEY')
    LLM_BOOST_BASE_URL = os.environ.get('LLM_BOOST_BASE_URL')
    LLM_BOOST_MODEL_NAME = os.environ.get('LLM_BOOST_MODEL_NAME')
    LLM_WEB_SEARCH_API_KEY = os.environ.get('LLM_WEB_SEARCH_API_KEY') or LLM_API_KEY
    LLM_WEB_SEARCH_BASE_URL = os.environ.get('LLM_WEB_SEARCH_BASE_URL') or LLM_BASE_URL
    LLM_WEB_SEARCH_MODEL = os.environ.get('LLM_WEB_SEARCH_MODEL') or LLM_MODEL_NAME
    LLM_WEB_SEARCH_STRATEGY = os.environ.get('LLM_WEB_SEARCH_STRATEGY', 'max')
    LLM_WEB_SEARCH_VALIDATE_LINKS = os.environ.get('LLM_WEB_SEARCH_VALIDATE_LINKS', 'true').lower() in {'1', 'true', 'yes', 'on'}
    REAL_ENTITY_BATCH_SIZE = int(os.environ.get('REAL_ENTITY_BATCH_SIZE', '30'))
    REAL_ENTITY_RESOLVE_CONCURRENCY = int(os.environ.get('REAL_ENTITY_RESOLVE_CONCURRENCY', '3'))

    # 联网搜索 provider 配置
    # 默认优先使用阿里百炼 OpenAI 兼容模式的 websearch 能力；博查仅在显式启用时使用。
    USE_BOCHA_WEB_SEARCH = os.environ.get('USE_BOCHA_WEB_SEARCH', 'false').lower() in {'1', 'true', 'yes', 'on'}
    WEB_SEARCH_PROVIDER = os.environ.get('WEB_SEARCH_PROVIDER', 'bailian').strip().lower()
    DEFAULT_WEB_SEARCH_PROVIDER = 'bocha' if USE_BOCHA_WEB_SEARCH else (WEB_SEARCH_PROVIDER or 'bailian')
    
    # Zep配置
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    ZEP_BACKEND = os.environ.get('ZEP_BACKEND', 'cloud')  # 'cloud' | 'graphiti'

    # 博查 Web Search 配置（仅在调用搜索 seed 时按需校验）
    BOCHA_API_KEY = os.environ.get('BOCHA_API_KEY')
    BOCHA_BASE_URL = os.environ.get('BOCHA_BASE_URL', 'https://api.bochaai.com/v1')
    BOCHA_WEB_SEARCH_ENDPOINT = os.environ.get('BOCHA_WEB_SEARCH_ENDPOINT', '/web-search')
    BOCHA_WEB_SEARCH_MAX_RESULTS = int(
        os.environ.get('BOCHA_WEB_SEARCH_MAX_RESULTS')
        or os.environ.get('BOCHA_DEFAULT_COUNT', '8')
    )
    BOCHA_WEB_SEARCH_TIMEOUT = int(
        os.environ.get('BOCHA_WEB_SEARCH_TIMEOUT')
        or os.environ.get('BOCHA_TIMEOUT_SECONDS', '30')
    )
    BOCHA_WEB_SEARCH_FRESHNESS = os.environ.get('BOCHA_WEB_SEARCH_FRESHNESS', 'twoMonths')
    BOCHA_WEB_SEARCH_RECENT_DAYS = int(os.environ.get('BOCHA_WEB_SEARCH_RECENT_DAYS', '60'))
    BOCHA_VALIDATE_LINKS = os.environ.get('BOCHA_VALIDATE_LINKS', 'true').lower() in {'1', 'true', 'yes', 'on'}
    BOCHA_LINK_CHECK_TIMEOUT = int(os.environ.get('BOCHA_LINK_CHECK_TIMEOUT', '5'))
    BOCHA_LINK_CHECK_MAX_BYTES = int(os.environ.get('BOCHA_LINK_CHECK_MAX_BYTES', '16384'))
    # 兼容早期实现中的内部命名
    BOCHA_DEFAULT_COUNT = BOCHA_WEB_SEARCH_MAX_RESULTS
    BOCHA_TIMEOUT_SECONDS = BOCHA_WEB_SEARCH_TIMEOUT

    # Graphiti / Neo4j 配置（本地部署时使用）
    NEO4J_URI = os.environ.get('NEO4J_URI', 'bolt://localhost:7687')
    NEO4J_USER = os.environ.get('NEO4J_USER', 'neo4j')
    NEO4J_PASSWORD = os.environ.get('NEO4J_PASSWORD', 'password')
    
    # 文件上传配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # 文本处理配置
    DEFAULT_CHUNK_SIZE = int(os.environ.get('DEFAULT_CHUNK_SIZE', '4000'))  # 默认切块大小
    DEFAULT_CHUNK_OVERLAP = int(os.environ.get('DEFAULT_CHUNK_OVERLAP', '100'))  # 默认重叠大小
    ONTOLOGY_MAX_TEXT_LENGTH_FOR_LLM = int(os.environ.get('ONTOLOGY_MAX_TEXT_LENGTH_FOR_LLM', '30000'))  # 本体生成传给 LLM 的最大文本长度
    GRAPH_BUILD_BATCH_SIZE = int(os.environ.get('GRAPH_BUILD_BATCH_SIZE', '5'))  # 图谱构建批次大小
    GRAPH_BUILD_CONCURRENCY = int(os.environ.get('GRAPH_BUILD_CONCURRENCY', '2'))  # 图谱构建并发批次数
    GRAPH_BUILD_BATCH_DELAY_SECONDS = float(os.environ.get('GRAPH_BUILD_BATCH_DELAY_SECONDS', '0'))  # 批次节流延迟
    GRAPH_BUILD_DUAL_LLM_ENABLED = os.environ.get('GRAPH_BUILD_DUAL_LLM_ENABLED', 'true').lower() in {'1', 'true', 'yes', 'on'}  # 是否启用图谱构建双模型分摊
    GRAPH_BUILD_LLM_BASE_WEIGHT = int(os.environ.get('GRAPH_BUILD_LLM_BASE_WEIGHT', '1'))  # 图谱构建默认 LLM 分摊权重
    GRAPH_BUILD_LLM_BOOST_WEIGHT = int(os.environ.get('GRAPH_BUILD_LLM_BOOST_WEIGHT', '1'))  # 图谱构建加速 LLM 分摊权重
    GRAPH_BUILD_LLM_ROUTE_RETRY_ENABLED = os.environ.get('GRAPH_BUILD_LLM_ROUTE_RETRY_ENABLED', 'true').lower() in {'1', 'true', 'yes', 'on'}  # 单路由失败时是否换另一路由重试该批次
    GRAPHITI_EPISODE_BATCH_SIZE = int(os.environ.get('GRAPHITI_EPISODE_BATCH_SIZE', '1'))  # Graphiti 单次写入 episode 数
    GRAPHITI_INGEST_CONCURRENCY = int(os.environ.get('GRAPHITI_INGEST_CONCURRENCY', '2'))  # Graphiti 批次提交并发数
    GRAPHITI_USE_BULK_INGEST = os.environ.get('GRAPHITI_USE_BULK_INGEST', 'false').lower() in {'1', 'true', 'yes', 'on'}  # 是否启用 Graphiti bulk ingestion
    GRAPHITI_OPERATION_TIMEOUT_SECONDS = int(os.environ.get('GRAPHITI_OPERATION_TIMEOUT_SECONDS', '900'))  # Graphiti 单次写入超时
    GRAPHITI_RATE_LIMIT_MAX_RETRIES = int(os.environ.get('GRAPHITI_RATE_LIMIT_MAX_RETRIES', '3'))  # Graphiti 限流重试次数
    GRAPHITI_RATE_LIMIT_RETRY_SECONDS = float(os.environ.get('GRAPHITI_RATE_LIMIT_RETRY_SECONDS', '20'))  # Graphiti 限流退避秒数
    GRAPHITI_LLM_CONCURRENCY = int(os.environ.get('GRAPHITI_LLM_CONCURRENCY', '2'))  # Graphiti 内部 LLM 抽取并发数
    GRAPHITI_LLM_MIN_INTERVAL_SECONDS = float(os.environ.get('GRAPHITI_LLM_MIN_INTERVAL_SECONDS', '0.5'))  # Graphiti LLM 请求最小间隔
    GRAPHITI_LLM_REQUEST_TIMEOUT_SECONDS = float(os.environ.get('GRAPHITI_LLM_REQUEST_TIMEOUT_SECONDS', '120'))  # Graphiti 单次 LLM 请求超时
    GRAPHITI_LLM_MAX_TOKENS = int(os.environ.get('GRAPHITI_LLM_MAX_TOKENS', '16384'))  # Graphiti LLM 抽取输出上限，宽松值让 LLM 自行控制
    GRAPHITI_LLM_SMALL_MODEL = os.environ.get('GRAPHITI_LLM_SMALL_MODEL')  # Graphiti 小任务模型（可选）
    GRAPHITI_LLM_TEMPERATURE = float(os.environ.get('GRAPHITI_LLM_TEMPERATURE', '0'))  # Graphiti LLM 抽取温度
    GRAPHITI_EMBEDDING_REQUEST_TIMEOUT_SECONDS = float(os.environ.get('GRAPHITI_EMBEDDING_REQUEST_TIMEOUT_SECONDS', '60'))  # Graphiti 单次 embedding 请求超时
    GRAPHITI_USE_PREVIOUS_EPISODE_CONTEXT = os.environ.get('GRAPHITI_USE_PREVIOUS_EPISODE_CONTEXT', 'false').lower() in {'1', 'true', 'yes', 'on'}  # 是否把历史 episode 注入后续抽取提示词
    GRAPHITI_EMBEDDING_API_KEY = os.environ.get('GRAPHITI_EMBEDDING_API_KEY')  # Graphiti embedding 独立 API Key（可选）
    GRAPHITI_EMBEDDING_BASE_URL = os.environ.get('GRAPHITI_EMBEDDING_BASE_URL')  # Graphiti embedding 独立 endpoint（可选）
    GRAPHITI_EMBEDDING_MODEL = os.environ.get('GRAPHITI_EMBEDDING_MODEL')  # Graphiti embedding 模型名
    GRAPHITI_EMBEDDING_DIM = int(os.environ.get('GRAPHITI_EMBEDDING_DIM', '1024'))  # Graphiti embedding 写入维度
    GRAPHITI_EMBEDDING_BATCH_SIZE = int(os.environ.get('GRAPHITI_EMBEDDING_BATCH_SIZE', '10'))  # Graphiti embedding 分块大小
    GRAPHITI_EMBEDDING_MIN_INTERVAL_SECONDS = float(os.environ.get('GRAPHITI_EMBEDDING_MIN_INTERVAL_SECONDS', '0.5'))  # Graphiti embedding 请求最小间隔
    GRAPH_EXTRACTION_CONTEXT_MAX_SUMMARY_CHARS = int(os.environ.get('GRAPH_EXTRACTION_CONTEXT_MAX_SUMMARY_CHARS', '1500'))  # 每个 chunk 注入的事件摘要长度，放宽以适应复杂事件
    GRAPH_EXTRACTION_CONTEXT_MAX_ENTITY_HINTS = int(os.environ.get('GRAPH_EXTRACTION_CONTEXT_MAX_ENTITY_HINTS', '100'))  # 每个 chunk 注入的实体提示数量，大幅放宽避免截断
    GRAPH_EXTRACTION_CONTEXT_MAX_HINT_CHARS = int(os.environ.get('GRAPH_EXTRACTION_CONTEXT_MAX_HINT_CHARS', '2000'))  # 每个 chunk 注入的实体提示总长度，放宽以适应长名称实体
    GRAPH_MIN_ENTITY_TARGET = int(os.environ.get('GRAPH_MIN_ENTITY_TARGET', '50'))  # Step1 图谱构建的事件相关实体目标下限
    GRAPH_ENTITY_ENRICHMENT_ENABLED = os.environ.get('GRAPH_ENTITY_ENRICHMENT_ENABLED', 'true').lower() in {'1', 'true', 'yes', 'on'}  # 低于实体目标时是否联网补充材料继续建图
    GRAPH_ENTITY_ENRICHMENT_QUERY_LIMIT = int(os.environ.get('GRAPH_ENTITY_ENRICHMENT_QUERY_LIMIT', '3'))  # 实体补充检索最多派生查询数
    GRAPH_ENTITY_ENRICHMENT_SEARCH_COUNT = int(os.environ.get('GRAPH_ENTITY_ENRICHMENT_SEARCH_COUNT', '10'))  # 每个补充查询抓取来源数
    GRAPH_ENTITY_ENRICHMENT_MAX_SOURCES = int(os.environ.get('GRAPH_ENTITY_ENRICHMENT_MAX_SOURCES', '24'))  # 单次补充最多写入来源数
    GRAPH_ENTITY_ENRICHMENT_MAX_MATERIAL_CHARS = int(os.environ.get('GRAPH_ENTITY_ENRICHMENT_MAX_MATERIAL_CHARS', '30000'))  # 补充材料写入 Graphiti 的最大字符数
    GRAPH_MEMORY_STOP_TIMEOUT_SECONDS = float(os.environ.get('GRAPH_MEMORY_STOP_TIMEOUT_SECONDS', '3'))  # 图谱记忆写回停止等待秒数
    
    # OASIS模拟配置
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')
    
    # OASIS平台可用动作配置
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent配置
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    REPORT_TOOL_CONCURRENCY = int(os.environ.get('REPORT_TOOL_CONCURRENCY', '3'))
    REPORT_SECTION_CONCURRENCY = int(os.environ.get('REPORT_SECTION_CONCURRENCY', '2'))
    SIMULATION_CONFIG_CONCURRENCY = int(os.environ.get('SIMULATION_CONFIG_CONCURRENCY', '3'))
    PROFILE_GENERATION_CONCURRENCY = int(os.environ.get('PROFILE_GENERATION_CONCURRENCY', '5'))
    
    @classmethod
    def validate(cls):
        """验证必要配置"""
        errors = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY 未配置")
        # 根据后端类型验证配置
        if cls.ZEP_BACKEND == 'cloud':
            if not cls.ZEP_API_KEY:
                errors.append("ZEP_API_KEY 未配置（ZEP_BACKEND=cloud 时必需）")
        elif cls.ZEP_BACKEND == 'graphiti':
            if not all([cls.NEO4J_URI, cls.NEO4J_USER, cls.NEO4J_PASSWORD]):
                errors.append("Neo4j 配置不完整（ZEP_BACKEND=graphiti 时必需）")
        return errors
