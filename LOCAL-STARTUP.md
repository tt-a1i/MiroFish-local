# MiroFish 本地版启动指南

本文档说明如何启动 MiroFish 本地版（使用 graphiti-core + Neo4j 替代 Zep Cloud）。

## 架构概览

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Frontend  │────▶│   Backend   │────▶│    Neo4j    │
│   (Vue 3)   │     │   (Flask)   │     │  (Docker)   │
└─────────────┘     └──────┬──────┘     └─────────────┘
                           │
                    ┌──────┴──────┐
                    │             │
              ┌─────▼─────┐ ┌─────▼─────┐
              │  主环境    │ │  模拟环境  │
              │ graphiti  │ │ camel-ai  │
              │ neo4j 6.x │ │ neo4j 5.x │
              └───────────┘ └───────────┘
```

**双环境隔离**：camel-ai 和 graphiti-core 对 neo4j driver 版本有冲突，通过独立虚拟环境解决。

## 前置要求

| 工具 | 版本要求 | 说明 |
|------|---------|------|
| Node.js | 18+ | 前端运行环境 |
| Python | 3.11 | camel-oasis 需要 3.10-3.11 |
| uv | 最新版 | Python 包管理器 |
| Docker | 最新版 | 运行 Neo4j |

## 快速启动

### 1. 启动 Neo4j

```bash
docker-compose -f docker-compose.local.yml up -d neo4j
```

验证：访问 http://localhost:7474，使用 `neo4j/password` 登录；如果 `.env` 中改过 `NEO4J_PASSWORD`，则使用修改后的密码。

如果 Neo4j 容器反复重启，先看初始化日志：

```bash
docker-compose -f docker-compose.local.yml logs --tail=120 neo4j
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`：

```env
# LLM（推荐阿里百炼 qwen-plus）
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL_NAME=qwen-plus

# Neo4j
NEO4J_URI=bolt://localhost:7687
# 使用仓库内置 docker-compose 时用户名固定为 neo4j。
NEO4J_USER=neo4j
# 初始密码至少 8 位，建议只使用字母、数字、下划线、连字符和点。
NEO4J_PASSWORD=password

# Embedding（DashScope）
EMBEDDING_API_KEY=your_dashscope_api_key
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_MODEL=text-embedding-v3
```

### 3. 安装依赖

```bash
# 一键安装
npm run setup:all

# 或分步
npm run setup          # Node 依赖
npm run setup:backend  # Python 依赖
```

### 4. 创建模拟环境

解决 neo4j 版本冲突：

```bash
cd backend
uv venv .venv-simulation --python 3.11
source .venv-simulation/bin/activate
uv pip install camel-oasis openai python-dotenv
deactivate
```

### 5. 启动服务

```bash
npm run dev
```

服务地址：
- 前端：http://localhost:3000
- 后端：http://localhost:5001
- Neo4j：http://localhost:7474

## 数据清理

```bash
cd backend

# 清理 Neo4j
.venv/bin/python -c "
from neo4j import GraphDatabase
d = GraphDatabase.driver('bolt://localhost:7687', auth=('neo4j', 'password'))
with d.session() as s: s.run('MATCH (n) DETACH DELETE n')
d.close()
"

# 清理模拟数据
rm -rf uploads/simulations/* uploads/projects/*
```

## 验证环境

```bash
cd backend

# 检查 neo4j 版本隔离
echo "主环境: $(.venv/bin/python -c 'import neo4j; print(neo4j.__version__)')"
echo "模拟环境: $(.venv-simulation/bin/python -c 'import neo4j; print(neo4j.__version__)')"
# 预期：主环境 6.x，模拟环境 5.23.0
```

## 问题排查

详见 [docs/zep-localization/troubleshooting.md](docs/zep-localization/troubleshooting.md)

## 开发工具

本项目使用 [Claude Code](https://claude.ai/claude-code) 开发，配合 [planning-with-files](https://github.com/OthmanAdi/planning-with-files) skill 管理复杂任务。
