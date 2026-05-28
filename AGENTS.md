# Rayue Agent 开发说明

这个文件要让一个没有上下文的 agent 进来后，可以直接判断项目目标、架构、约束和下一步怎么开发。

结构和模块边界优先参考 `docs/architecture-guidelines.md`。这个文档只保留当前指导意见，架构调整时更新它，不要把长期过程记录塞进来。

## 项目目标

Rayue Agent 是一个内测用的网页智能体工作台。目标是让用户在浏览器里发任务、上传资料、观察执行进度、获取最终回复和生成文件。

核心体验参考 ChatGPT 和 Codex：

- 对话是主界面，不做营销页。
- 每个 conversation 有独立工作区和 sandbox。
- 用户上传的资料会进入 sandbox 的 `inputs/`。
- agent 生成的文件要持久化到本地，并在对话和右侧工作区里可下载。
- 过程展示要像 Codex 一样有阶段感：展示当前在做什么、关键历史步骤、运行过的命令摘要，不要把原始事件流水账直接甩给用户。
- 用户点停止时必须真正中断后端当前 turn，确认停止完成后才允许继续发送。

用户可见产品名只能是 Rayue。前端不能展示底层实现名、模型名、sandbox/thread id、provider 名或类似调试字段。

## 当前完成度

已经完成：

- Next.js 前端，端口 `8070`。
- FastAPI 后端，端口 `8071`。
- PostgreSQL 会话、消息、事件持久化，端口 `55432`。
- 每个 conversation 一个 E2B sandbox，默认模板 `rayue-agent-v2`。
- OpenAI-compatible 模型接入，默认 `MODEL_BASE_URL` + `MODEL_NAME` 配置。
- SSE 实时事件流，前端把事件聚合成轻量过程卡片。
- Markdown 回复渲染。
- 文件上传、本地持久化、同步到 sandbox 的 `inputs/`。
- 生成文件本地持久化、右侧工作区下载、对话内文件卡片展示；图片/视频支持预览缩略图。
- 管理员 skill：`backend/agent_skills/*` 会打包并同步到所有用户 sandbox。
- 常用 skill、`apply_patch`、Codex 常见命令行工具和常用包预装模板：`scripts/build_e2b_template.py`。
- ConvertX 本地文件转换服务，通过后端代理 `/api/tools/convertx` 暴露给 skill。
- 当前任务停止按钮：处理中发送按钮变停止按钮，后端调用 app-server interrupt，并处理排队期竞态。
- 每个用户消息会创建一个持久化 turn，事件和生成文件会绑定到对应 turn，刷新或多会话切换时不应串到其他回复下面。
- 后端有阶段感知 watchdog：长命令允许继续运行；命令结束后模型长时间无返回时，先读取 app-server 已保存结果，再 interrupt，最后保存生成文件并落终态。

暂未完成或不要假装完成：

- 没有正式用户系统和权限隔离，目前是内测形态。
- 生成文件先落本地 `storage/artifacts/`，S3 还没接。
- GitHub 集成暂不接。
- 多 worker 分布式调度还没有，当前是单后端进程内 worker 管理。
- sandbox 关闭后，历史文件会从本地同步回新 sandbox，但长期归档和清理策略还要补。

## 参考标准

开发时优先参考 `/home/ubuntu/akool/agent_web/codex` 的行为，而不是凭感觉重造：

- 任务停止：参考 Codex 的 `turn/interrupt`，由后端确认 `turn/completed` 或状态落回 idle 后再放开输入。
- 过程展示：参考 Codex 的“当前说明 + 命令/搜索/文件摘要 + 历史关键步骤”，不要用大量 if/else 猜中文阶段；能从 agent 事件和命令事件映射就从事件映射。
- 文件结果：参考 Codex 的 artifacts 处理，结果文件必须能下载，不能只让模型文字说“已生成”。
- 失败恢复：有文件生成时优先保存和展示文件，再给用户明确说明失败点。

通用标准：

- 先找根因，不做只遮 UI 的补丁。
- 后端状态是事实来源，前端只能乐观展示，不能伪造任务完成或停止。
- 用户可见信息要简单，不暴露底层实现细节。
- 新功能要优先贴合现有架构，少引入新抽象。
- 改前端后跑 `npx tsc --noEmit`。
- 改后端后跑 `python3 -m compileall backend/app`。
- 服务代码改完后重启 PM2。
- 不要提交或展示 `.env` 里的真实 key。
- 不要提交 `storage/`、`frontend/.next/`、`frontend/node_modules/`。

## 架构

```text
rayue-agent/
  frontend/                 Next.js UI
    app/page.tsx            主对话、过程卡片、工作区面板
    lib/api.ts              前端 API client
  backend/                  FastAPI backend
    app/main.py             FastAPI app 装配、lifespan、CORS、router 注册
    app/api/                REST/SSE/API 路由层
    app/core/               配置、数据库、事件总线等进程级基础设施
    app/domain/             SQLAlchemy models 和 Pydantic schemas
    app/agent/              worker、sandbox、app-server、artifact 同步核心逻辑
    app/files/              上传文件保存、打包和后续对象存储边界
    app/admin/              管理员 skill 打包
    app/tools/              ConvertX 等内部工具代理
    app/*                   若干旧路径兼容导出，避免脚本和部署命令断裂
    agent_skills/           管理员全局 skills
  scripts/
    build_e2b_template.py   构建预装工具、apply_patch 和 skills 的 E2B template
  storage/
    artifacts/              生成文件本地持久化
    uploads/                用户上传文件本地持久化
    upload-bundles/         上传同步包缓存
    skill-bundles/          skill 同步包缓存
```

运行链路：

1. 前端 `sendMessage` 调后端 `/api/conversations/{id}/messages`。
2. 后端写入 user message，状态置为 `queued`，调度一个 turn。
3. runtime 为 conversation 取或创建 worker。
4. worker 启动/连接 sandbox，准备 app-server，恢复历史 artifacts，同步 uploads 和 skills。
5. worker 发起 turn，并把 app-server events 持久化为 `AgentEvent`；当前 turn 状态落在 `AgentTurn`。
6. 前端通过 `/events` SSE 收事件，聚合成过程卡片。
7. turn 完成、失败、停止或被 watchdog 恢复时，后端同步 artifacts 到 `storage/artifacts/<conversation_id>/`，并把文件元数据落到 `ArtifactRecord`。
8. 前端通过 `/artifacts` 刷新文件列表，并按 turn 归属在对话内展示新文件。

## 运行方式

项目路径：

```bash
cd /home/ubuntu/akool/agent_web/rayue-agent
```

持久化启动优先用 PM2：

```bash
docker compose up -d postgres
docker start rayue-convertx || true
pm2 start ecosystem.config.cjs
pm2 save
```

服务代码改动后：

```bash
pm2 restart rayue-backend rayue-frontend
```

查看状态和日志：

```bash
pm2 status
docker ps --filter name=rayue
pm2 logs rayue-backend --lines 100
pm2 logs rayue-frontend --lines 100
```

健康检查：

```bash
curl -sS http://127.0.0.1:8071/api/health
curl -sS -o /tmp/rayue.html -w '%{http_code}\n' http://127.0.0.1:8070/
```

手动调试后端：

```bash
cd /home/ubuntu/akool/agent_web/rayue-agent/backend
../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8071
```

手动调试前端：

```bash
cd /home/ubuntu/akool/agent_web/rayue-agent/frontend
BACKEND_INTERNAL_URL=http://127.0.0.1:8071 npm run dev -- --hostname 0.0.0.0 --port 8070
```

## 环境和服务

必要环境变量在 `.env`，示例在 `.env.example`。

关键项：

- `DATABASE_URL=postgresql+asyncpg://rayue_agent:rayue_agent@localhost:55432/rayue_agent`
- `AGENT_RUN_MODE=e2b`
- `E2B_API_KEY`
- `CODEX_API_KEY`
- `MODEL_BASE_URL`
- `MODEL_NAME`
- `E2B_TEMPLATE=rayue-agent-v2`
- `SANDBOX_IDLE_TIMEOUT_SECONDS=900`
- `CONVERTX_SERVICE_URL=http://127.0.0.1:8072`
- `CONVERTX_API_BASE_URL=http://<server-host>:8071/api/tools/convertx`

ConvertX 只在本机监听：

```text
127.0.0.1:8072
```

agent 不能直接假设 ConvertX 公网可访问，应通过 Rayue 后端代理调用。

## UI 规则

- UI 品牌写 Rayue。
- 过程卡片要轻，最终答案要更明显。
- 运行中只展示一个当前过程模块，不要同一问答里出现多余的“思考了 1s 处理完成”。
- 生成文件要作为结果消息的一部分出现；图片/视频尽量预览，文档类用下载卡片。
- 输入框在 running/queued/stopping 时不允许发新消息；按钮改成停止。
- 如果用户滚动查看历史，不要强制自动弹到底部；只有用户本来贴近底部或新发送时才滚到底。

## 后端规则

- `Conversation.status` 是前端能否发送的事实来源。
- `AgentTurn` 是每次问答执行状态的事实来源；不要只依赖内存变量判断任务是否卡死。
- `queued/running/stopping` 时，`/messages` 必须返回 409。
- 停止必须走 `/api/conversations/{id}/stop`，并等待当前 turn 结束或排队期 stop request 被消费。
- artifact 同步失败要重试；最终至少尝试列本地已保存文件。
- 上传文件是输入资料，不得出现在 downloadable artifacts 中。
- sandbox idle timeout 默认 15 分钟，不要改短。
- 任何新生成的文件类型要同时考虑 artifact 识别、下载、对话展示和工作区展示。
