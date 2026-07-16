# MiroFish 本地开发调试指南

本文档面向本地开发调试场景，参考 `docs/docker-deploy-guide.md` 的部署结构，但本地模式只用 Docker 启动 Neo4j，前端和后端直接在宿主机运行，方便调试代码。

## 本地开发架构

```text
本机终端
├── frontend: Vue 3 + Vite，端口 3000
├── backend: Flask，端口 5001
└── neo4j: Docker Compose 本地服务，端口 7474 / 7687
```

本地 Graphiti 模式下：

- `backend/.venv`：后端主环境，运行 Flask + Graphiti。
- `backend/.venv-simulation`：OASIS 独立模拟环境，用于解决 `graphiti-core` 和 `camel-oasis` 对 `neo4j` driver 版本冲突的问题。
- `docker-compose.local.yml`：只启动 Neo4j，不启动前后端。

## 前置要求

```bash
node -v
python --version
uv --version
docker --version
docker compose version
```

建议版本：

| 工具 | 建议 |
|---|---|
| Node.js | 18+ |
| Python | 3.11 |
| uv | 最新稳定版 |
| Docker Compose | 2.x |

## 环境配置

首次启动前，在仓库根目录创建 `.env`：

```bash
cd /Users/linshibo/GithubProject/MiroFish-local
cp .env.local.example .env
chmod 600 .env
```

编辑 `.env`，至少确认以下配置：

```env
# LLM（OpenAI-compatible；阿里百炼使用 compatible-mode）
LLM_API_KEY=你的API_KEY
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL_NAME=qwen3-max

# Graphiti 模型
GRAPHITI_LLM_MODEL=qwen3-max
GRAPHITI_EMBEDDING_MODEL=text-embedding-v4

# 本地图谱后端
ZEP_BACKEND=graphiti
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password

# Embedding / DashScope
EMBEDDING_API_KEY=你的DashScope_KEY
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_MODEL=text-embedding-v4

# 博查 Web Search API（使用关键词联网搜索时必填）
BOCHA_API_KEY=你的博查API_KEY
BOCHA_BASE_URL=https://api.bochaai.com/v1
BOCHA_WEB_SEARCH_ENDPOINT=/web-search
BOCHA_WEB_SEARCH_TIMEOUT=30
BOCHA_WEB_SEARCH_MAX_RESULTS=8
BOCHA_VALIDATE_LINKS=true
```

注意：

- `.env` 不要提交到 Git。
- 本地 Neo4j 默认用户名密码是 `neo4j/password`，对应 `docker-compose.local.yml`。
- 如果你修改了 Neo4j 密码，已有 volume 里的旧密码不会自动变更，需要清理 volume 后重新启动。

## 第一次本地执行

第一次执行包含：配置环境、启动 Neo4j、安装前端依赖、安装后端 Graphiti 依赖、安装 OASIS 独立环境、启动前后端。

```bash
cd /Users/linshibo/GithubProject/MiroFish-local

# 1. 创建并编辑 .env
cp .env.local.example .env
chmod 600 .env
# 然后手动编辑 .env，填入 LLM_API_KEY、EMBEDDING_API_KEY、BOCHA_API_KEY 等

# 2. 启动本地 Neo4j
docker compose -f docker-compose.local.yml up -d
docker compose -f docker-compose.local.yml ps

# 3. 安装 Node 依赖（根目录 + frontend）
npm run setup

# 4. 安装后端主环境依赖（Graphiti 本地模式）
npm run setup:backend:graphiti

# 5. 安装 OASIS 独立模拟环境
npm run setup:simulation

# 6. 启动前后端开发服务
npm run dev
```

启动后访问：

- 前端：`http://localhost:3000`
- 后端健康检查：`http://localhost:5001/health`
- Neo4j Browser：`http://localhost:7474`

Neo4j Browser 登录：

```text
username: neo4j
password: password
```

## 第二次本地执行

第二次不需要重新安装依赖，只需要确认 Neo4j 启动，然后启动前后端。

```bash
cd /Users/linshibo/GithubProject/MiroFish-local

docker compose -f docker-compose.local.yml up -d
npm run dev
```

如果你只想分别启动：

终端 1：

```bash
cd /Users/linshibo/GithubProject/MiroFish-local
npm run backend
```

终端 2：

```bash
cd /Users/linshibo/GithubProject/MiroFish-local
npm run frontend
```

## 第三次及以后本地执行

第三次以后和第二次一样，不要再跑安装命令。

```bash
cd /Users/linshibo/GithubProject/MiroFish-local

docker compose -f docker-compose.local.yml up -d
npm run dev
```

只有在以下情况才需要重新安装依赖：

- `package.json` 或 `frontend/package.json` 变化。
- `backend/pyproject.toml` 或 `backend/uv.lock` 变化。
- 你删除了 `node_modules`、`frontend/node_modules`、`backend/.venv` 或 `backend/.venv-simulation`。
- 切换 Python 版本或 Node 版本。
- 拉取了明确说明依赖变化的代码。

对应命令：

```bash
# 前端或根目录 Node 依赖变化
npm run setup

# 后端 Graphiti 依赖变化
npm run setup:backend:graphiti

# OASIS 模拟依赖变化
npm run setup:simulation
```

## 如果后端依赖已经装好，如何避免每次看起来重新下载

当前根目录脚本：

```json
"backend": "cd backend && uv run python run.py"
```

`uv run` 每次启动前会检查 `pyproject.toml`、`uv.lock` 和 `.venv` 是否同步。正常情况下只是快速校验，不应该每次大量下载。

如果它每次都在下载，常见原因是：

- 你每次都执行了 `npm run setup:backend:graphiti`，而不是只执行 `npm run dev`。
- `backend/.venv` 被删除或不在当前项目目录。
- `uv.lock` 或 `pyproject.toml` 发生变化。
- Python 版本变化，uv 重新创建环境。
- uv 缓存目录变化或被清理。

如果你确认 `backend/.venv` 已经安装好，可以绕过 `uv run`，直接启动后端：

终端 1：

```bash
cd /Users/linshibo/GithubProject/MiroFish-local/backend
.venv/bin/python run.py
```

终端 2：

```bash
cd /Users/linshibo/GithubProject/MiroFish-local/frontend
npm run dev
```

这种方式不会触发 `uv run` 的同步检查，但前提是 `.venv` 本身已经正确安装。

## 常用本地调试命令

```bash
# 查看 Neo4j 容器状态
docker compose -f docker-compose.local.yml ps

# 查看 Neo4j 日志
docker compose -f docker-compose.local.yml logs -f neo4j

# 停止 Neo4j，但保留数据
docker compose -f docker-compose.local.yml down

# 停止 Neo4j 并删除数据卷（危险，会清空图数据库）
docker compose -f docker-compose.local.yml down -v

# 前端构建检查
npm run build

# 后端测试示例
cd backend
uv run pytest tests/test_real_entity_resolver.py
```

## 本地数据清理

清理项目上传数据和模拟数据：

```bash
cd /Users/linshibo/GithubProject/MiroFish-local/backend
rm -rf uploads/projects/* uploads/simulations/*
```

清理 Neo4j 图数据但保留容器和 volume：

```bash
cd /Users/linshibo/GithubProject/MiroFish-local/backend
.venv/bin/python - <<'PY'
from neo4j import GraphDatabase

d = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "password"))
with d.session() as s:
    s.run("MATCH (n) DETACH DELETE n")
d.close()
print("neo4j data cleared")
PY
```

## 环境验证

检查两个 Python 环境是否隔离：

```bash
cd /Users/linshibo/GithubProject/MiroFish-local/backend

echo "主环境 Python: $(.venv/bin/python --version)"
echo "模拟环境 Python: $(.venv-simulation/bin/python --version)"

echo "主环境 neo4j: $(.venv/bin/python -c 'import neo4j; print(neo4j.__version__)')"
echo "模拟环境 neo4j: $(.venv-simulation/bin/python -c 'import neo4j; print(neo4j.__version__)')"
```

预期：

- 主环境用于 Flask + Graphiti。
- 模拟环境用于 OASIS。
- 两个环境的 `neo4j` driver 版本可能不同，这是正常现象。

## 推荐日常工作流

第一次：

```bash
cp .env.local.example .env
docker compose -f docker-compose.local.yml up -d
npm run setup
npm run setup:backend:graphiti
npm run setup:simulation
npm run dev
```

第二次：

```bash
docker compose -f docker-compose.local.yml up -d
npm run dev
```

第三次以后：

```bash
docker compose -f docker-compose.local.yml up -d
npm run dev
```

如果依赖没有变化，不要重复执行 `npm run setup:*`。
