# Repository Guidelines

## Project Structure & Module Organization
`frontend/` 是 Vue 3 + Vite 前端，主要代码在 `src/views/`、`src/components/`、`src/api/`、`src/router/`。`backend/` 是 Flask 服务，核心模块位于 `app/api/`、`app/services/`、`app/models/`、`app/utils/`，启动入口为 `backend/run.py`。示例数据与脚本放在 `examples/`、`demo.py` 和 `backend/scripts/`；静态文档与图片位于 `docs/`、`static/`。环境模板使用根目录 `.env.example` 和 `.env.local.example`。

## Build, Test, and Development Commands
在仓库根目录执行：

- `npm run setup:all`：安装根目录、前端与后端依赖。
- `npm run dev`：同时启动前端和后端，默认访问 `http://localhost:3000` 与 `http://localhost:5001`。
- `npm run frontend`：单独启动 Vite 开发服务器。
- `npm run backend`：通过 `uv run python run.py` 启动 Flask 后端。
- `npm run build`：构建前端生产包。
- `cd backend && uv run pytest backend/scripts/test_profile_format.py`：运行当前已存在的后端测试脚本。
- `python demo.py`：加载示例种子，快速验证主流程。

## Coding Style & Naming Conventions
前端使用 2 空格缩进、ES module、Vue SFC；组件文件采用 PascalCase，如 `GraphPanel.vue`，接口模块保持简短小写，如 `api/report.js`。后端遵循 PEP 8、4 空格缩进、snake_case 命名；服务逻辑优先放在 `app/services/`，路由仅保留请求编排。注释、文档和用户可见文案保持中文，与现有代码一致。当前仓库未提供统一 lint 脚本，提交前至少保证 `npm run build` 通过。

## Testing Guidelines
后端测试框架已声明为 `pytest` 与 `pytest-asyncio`。新增后端测试建议放入 `backend/tests/`，命名使用 `test_*.py`。前端暂未接入自动化测试；涉及界面或交互变更时，请在 PR 中附上手动验证步骤、关键截图，至少覆盖上传、图谱构建和模拟入口。

## Mandatory Completion Workflow
每次完成代码或配置修改后，必须执行以下底线流程，除非用户明确要求暂停或不要提交：

- 进行一次代码 review 自检，重点检查行为回归、无关改动、敏感信息、错误处理和测试覆盖。
- 运行与变更范围匹配的测试或构建；若无法运行，必须说明原因和风险。
- 使用中文提交信息提交本次相关改动，提交内容必须只包含本次任务相关文件，不能混入工作区里已有的无关改动。
- 提交完成后必须推送到当前远程分支；如推送失败，必须报告失败原因和后续处理建议。

## Commit & Pull Request Guidelines
提交信息遵循 Conventional Commits，仓库历史已使用 `feat: ...`、`fix(backend): ...`、`docs: ...`。推荐格式：`type(scope): summary`，例如 `feat(frontend): add simulation status panel`。PR 应包含变更目的、影响范围、验证命令、环境前提；如修改 UI，请附截图；如修改配置或本地部署流程，请同步更新 `README.md`、`LOCAL-STARTUP.md` 或示例环境文件。

## Security & Configuration Tips
不要提交真实的 API Key、`.env` 或本地数据库凭证。切换本地图谱模式前，先确认 `ZEP_BACKEND=graphiti` 和 `docker-compose.local.yml` 所需的 Neo4j 配置已就绪；云端模式则需校验 `LLM_API_KEY` 与 `ZEP_API_KEY`。
