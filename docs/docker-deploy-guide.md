# MiroFish Docker Compose 部署完整指南

## 部署架构概览

```
docker-compose.yml (3个服务)
├── neo4j (5.26)          ← 图数据库，端口 7474/7687
├── backend (Flask)       ← Python 3.11，端口 5001
└── frontend (Nginx)      ← Vue 3 静态文件 + 反向代理，端口 3000
```

前端 Nginx 通过 `proxy_pass http://backend:5001/api/` 反向代理到后端，无需暴露后端端口到外网。

## 第一步：服务器准备工作

```bash
# 1. 确保服务器已安装 Docker 和 Docker Compose
docker --version        # 需要 >= 20.10
docker compose version  # 需要 >= 2.0

# 2. 克隆项目并切换分支
git clone <你的仓库地址> /opt/mirofish
cd /opt/mirofish
git checkout feat/docker-depoly
```

## 第二步：创建生产环境 `.env` 文件

**关键注意事项**：`.dockerignore` 排除了 `.env` 文件，所以镜像内不会包含你的 `.env`。但 `docker-compose.yml` 第 35 行 `env_file: - .env` 会在容器启动时从宿主机加载。你**必须在服务器上手动创建** `.env` 文件。

```bash
cd /opt/mirofish

# 创建生产 .env（替换为你的真实 API Key）
cat > .env << 'EOF'
# ===== LLM 配置（必填）=====
LLM_API_KEY=sk-你的百炼API_KEY
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL_NAME=qwen3-max

# ===== Graphiti 模型配置 =====
GRAPHITI_LLM_MODEL=qwen3-max
GRAPHITI_EMBEDDING_MODEL=text-embedding-v4

# ===== Zep 后端 =====
ZEP_BACKEND=graphiti

# ===== Neo4j 配置 =====
NEO4J_USER=neo4j
NEO4J_PASSWORD=请设置一个强密码

# ===== Flask 配置 =====
FLASK_DEBUG=False
FLASK_HOST=0.0.0.0
FLASK_PORT=5001
EOF

# 重要：设置权限，避免其他用户读取 API Key
chmod 600 .env
```

## 第三步：构建并启动所有服务

```bash
cd /opt/mirofish

# 构建镜像（首次需要几分钟，因为要安装 Python 依赖）
# 国内服务器会自动使用阿里云镜像源加速
docker compose build

# 启动所有服务（后台运行）
docker compose up -d

# 查看启动日志
docker compose logs -f
```

启动顺序：`neo4j → (健康检查通过) → backend → (启动完成) → frontend`

## 第四步：验证部署

```bash
# 1. 检查所有容器状态
docker compose ps

# 期望输出：3 个服务都是 Up 状态
# mirofish-neo4j     Up (healthy)
# mirofish-backend   Up
# mirofish-frontend  Up

# 2. 检查 Neo4j
curl http://localhost:7474
# 或浏览器访问 http://<服务器IP>:7474 (neo4j/password)

# 3. 检查后端健康检查
curl http://localhost:5001/health

# 4. 检查前端
curl http://localhost:3000
# 浏览器访问 http://<服务器IP>:3000
```

## 第五步：配置防火墙/安全组

在云服务器安全组中开放以下端口：

| 端口 | 用途 | 建议 |
|------|------|------|
| `3000` | 前端页面 (Nginx) | 对外开放 |
| `7474` | Neo4j Browser | 仅开发用，生产建议不开放 |
| `7687` | Neo4j Bolt | 仅开发用，生产建议不开放 |
| `5001` | 后端 API | 建议不开放（前端 Nginx 内部代理） |

**生产环境最小开放**：只需开放 `3000` 端口即可，用户通过 `http://<服务器IP>:3000` 访问整个应用。

## 常用运维命令

```bash
# 查看日志
docker compose logs -f backend       # 后端日志
docker compose logs -f --tail=100    # 最近 100 行

# 重启单个服务
docker compose restart backend frontend

# 更新后重新构建并部署
git pull origin feat/docker-depoly
docker compose build backend frontend
docker compose up -d

# 停止所有服务
docker compose down

# 停止并删除所有数据卷（危险！会丢失 Neo4j 数据和上传文件）
docker compose down -v
```

## 数据备份

```bash
# Neo4j 数据和上传文件存储在 Docker volumes 中
# 查看 volume 路径
docker volume inspect mirofish-local_neo4j_data
docker volume inspect mirofish-local_backend_uploads

# 备份 Neo4j 数据
docker exec mirofish-neo4j neo4j-admin database dump neo4j --to-path=/data/dumps/
```

## 关键注意事项

1. **`.env` 文件**：`.dockerignore` 排除了 `.env`，必须手动在服务器上创建，否则后端会因为缺少 `LLM_API_KEY` 无法启动
2. **Neo4j 密码**：修改 `NEO4J_PASSWORD` 后需要同时清理 volume 才会生效：`docker compose down -v && docker compose up -d`
3. **镜像源**：Dockerfile 已经配置了阿里云镜像源（apt、pip、npm），国内服务器构建速度较快
4. **OASIS 仿真**：后端容器内有两个 venv，主 venv 用于 Flask + Graphiti，`.venv-simulation` 用于 OASIS 仿真引擎（解决 neo4j 驱动版本冲突）
