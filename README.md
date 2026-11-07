# 合意 AI 客服（离线演示版）

一个电商客服系统的完整演示。顾客在聊天里查询商品、订单、退货和工单；下单、申请退货、撤回退货、转人工都由助手先生成确认卡片，顾客点“确认”后才执行；政策和说明类问题从知识库检索原文，并在回复里标注引用。管理员在后台处理工单和退货申请，维护知识库文档。

本仓库是**离线演示版**：聊天使用内置的离线模型，按固定规则理解消息、调用工具、生成回复。“离线”是指**运行聊天时不调用任何外部大模型 API，也不需要 API Key**。接入真实大模型的接口已经留好，但没有用真实模型联调和验收过（见“功能完成情况”）。

首次构建镜像需要联网，下载基础镜像和 Python、npm 依赖。

技术栈：后端 FastAPI、LangGraph、SQLAlchemy（异步）、Alembic；业务查询通过独立进程的 MCP Server 提供；数据库 PostgreSQL 17 + pgvector；前端 Next.js 16、React 19、Tailwind CSS。领域术语和设计约定见 `CONTEXT.md`。

## 功能一览

**顾客端**（http://localhost:3000）

- 多个会话，回复逐字输出，可以中途停止
- 查询：商品（价格、库存）、我的订单（含订单项编号）、退货申请、工单
- 需要确认的操作：下单、申请退货、撤回退货申请、转人工。助手只生成确认卡片，顾客确认后才执行
- 政策和说明类问题：从知识库检索原文，回复里标注引用
- 聊天页顶部的“离线演示模式”说明，列出离线模型能理解的说法

**管理端**（http://localhost:3000/admin，用管理员账号登录）

- 工单：查看关联订单和来源会话的聊天记录（含引用），填写处理结果并关闭
- 退货申请：查看详情，完成或拒绝
- 知识库：上传 Markdown 文档、重新索引、启用和停用，查看索引状态

## 用 Docker 启动（推荐）

### 环境要求

- Docker Desktop（自带 Docker Compose v2）
- 本机 5432、8000、3000 端口空闲
- 首次构建时能联网

### 1. 克隆并配置

```powershell
git clone https://github.com/x1317808585/ai-customer-service.git
cd ai-customer-service
Copy-Item .env.example .env
```

（macOS / Linux 用 `cp .env.example .env`。）

打开 `.env`，填写 `JWT_SECRET` 和 `MCP_TOKEN_SECRET`：都至少 32 个字符，并且两者不能相同。可以用下面的命令生成（只需要 Docker），运行两次，分别填进去：

```powershell
docker run --rm python:3.12-slim-bookworm python -c "import secrets; print(secrets.token_urlsafe(32))"
```

其他配置保持默认即可：`LLM_PROVIDER=fake` 是离线模型，检索模式默认是关键词检索。

### 2. 构建并启动

在仓库根目录运行。运行前不要在终端里设置 `POSTGRES_DB` 这类和 `.env` 同名的环境变量，否则会覆盖 `.env` 里的值。

```powershell
docker compose up -d --build
```

首次构建要下载依赖（其中 CPU 版 PyTorch 约 200 MB），需要几分钟到十几分钟。然后查看状态：

```powershell
docker compose ps -a
```

正常的状态是 **4 个常驻服务加 1 个一次性迁移任务**：

| 服务 | 正常状态 | 说明 |
|---|---|---|
| `db` | Up (healthy) | PostgreSQL |
| `mcp` | Up (healthy) | MCP Server，只在容器网络内访问 |
| `backend` | Up (healthy) | 后端，端口 8000 |
| `frontend` | Up | 前端，端口 3000 |
| `migrate` | **Exited (0)** | 数据库迁移，完成后就退出，这是正常的 |

`migrate` 的退出码不是 0 时，后面的服务都不会启动，用 `docker compose logs migrate` 查看原因。

### 网络问题排查

构建时要访问这些地方：

- Docker Hub：`pgvector/pgvector:pg17`、`python:3.12-slim-bookworm`、`node:22-bookworm-slim`
- ghcr.io：`ghcr.io/astral-sh/uv:0.13.1`
- PyPI 和 download.pytorch.org：Python 依赖
- npm 仓库：前端依赖

常见情况：

- 报 `failed to fetch anonymous token`，或者连接 `auth.docker.io` 超时：Docker 连不上 Docker Hub。在 Docker Desktop 的 Settings → Resources → Proxies 里配置代理；如果构建时仍然拉不到基础镜像，先用 `docker pull <镜像名>` 把上面的镜像逐个拉到本地，再重新构建。
- 在 `uv sync` 或 `npm ci` 这一步下载失败：这是容器里下载依赖时的网络问题，预先拉取镜像解决不了，要让构建过程能访问 PyPI、download.pytorch.org 和 npm 仓库（比如配置代理）。

### 3. 初始化演示数据

下面四条命令都在仓库根目录运行，可以安全地重复运行：已有的账号、商品、订单和知识文档都会跳过，不会改密码，也不会覆盖数据。

创建管理员 ⚠️ **会提示输入两次密码**（至少 8 位，输入时不显示）：

```powershell
docker compose exec backend python -m scripts.create_user admin --role admin --if-missing
```

创建顾客 alice ⚠️ **会提示输入两次密码**：

```powershell
docker compose exec backend python -m scripts.create_user alice --if-missing
```

放入演示商品和 alice 的演示订单：

```powershell
docker compose exec backend python -m scripts.seed_demo_data alice
```

导入知识库文档（`backend/knowledge` 下的 7 篇）：

```powershell
docker compose exec backend python -m scripts.load_knowledge
```

说明：

- `--if-missing`：账号已存在且角色一致时跳过；角色不一致时报错退出，不会修改角色，也不会改密码。
- 忘记密码时重置（会提示输入新密码）：`docker compose exec backend python -m scripts.create_user alice --reset-password`
- `load_knowledge` 只导入库里还没有的文档，管理员在后台改过的文档不会被覆盖。

### 4. 打开

浏览器打开 http://localhost:3000 。**必须用 localhost，不要用 127.0.0.1**：前端构建时写入的后端地址是 `http://localhost:8000`，两边主机名不一致时，浏览器不会带上登录 Cookie。

### 停止、重启和清空

- 停止：`docker compose stop`；再次启动：`docker compose up -d`
- 数据保存在 Docker 数据卷 `db_data` 里，停止、重启、重新构建都不会丢失
- `docker compose down` 只删除容器，保留数据卷
- `docker compose down -v` 会**删除数据卷，所有账号、订单和聊天记录都会丢失**

## 演示账号和演示流程

| 账号 | 角色 | 密码 |
|---|---|---|
| `admin` | 管理员 | 初始化时自己设定 |
| `alice` | 顾客 | 初始化时自己设定 |

### 离线模型能理解的说法

离线模型按关键词和固定句式工作，不理解自由表达。聊天页顶部的“离线演示模式”里也有这份清单，点一下就会填进输入框。编号每次初始化都可能不同，要先查出来再用。

| 想做什么 | 怎么说 |
|---|---|
| 查看商品和商品编号 | 商品 |
| 查看订单和订单项编号 | 我的订单 |
| 下单 | 下单 商品 N（买多件时加上“2 件”） |
| 申请退货 | 退货 订单项 N（N 是订单项编号，不是订单号） |
| 查看退货申请 | 我的退货申请 |
| 撤回退货申请 | 撤回退货 N（N 是退货申请编号） |
| 转人工 | 转人工：问题描述（写上“订单 N”会关联这个订单） |
| 查看工单 | 我的工单 |
| 查政策和说明 | 消息里带“政策、规则、规定、说明、保修、运费”，比如“退货政策是什么” |

其他消息会去知识库检索，查不到时回复“知识库里没有找到”。

### 推荐的体验顺序

下单和退货是两条独立的流程。刚下的订单状态是“已下单”，还没签收，不能退货；演示退货要用初始化时就有的、已签收的订单。

1. **下单**：发送“商品”查看编号 → “下单 商品 N” → 在卡片上点确认 → 再发送“商品”，库存减少了。
2. **退货**：发送“我的订单”，找到状态为“已签收”、包含 SoundPods 降噪耳机和 USB-C 快充线的那笔订单（初始化时它是 2 天前签收的）→ “退货 订单项 N” → 确认 → 发送“我的退货申请”查看编号 → 可以“撤回退货 N”。演示数据里还有两个反例：定制刻字保温杯不支持退货；机械键盘 K8 是 15 天前签收的，已经超过 7 天退货期。
3. **转人工**：“转人工：耳机左边没声音 订单 N” → 确认 → 用 admin 登录管理端处理这个工单 → 回到 alice，发送“我的工单”查看处理结果。
4. **知识库**：发送“退货政策是什么”，回复会摘录原文并标注引用。管理端可以上传新的 Markdown 文档（`# 标题` 加若干 `## 小节`，UTF-8 编码，不超过 1 MB，文件名就是文档名称），停用后就不会再被引用。

**演示数据会随时间变化**：演示订单的签收时间按初始化的那一刻计算，重复运行初始化也不会刷新。初始化 5 天以后，“2 天前签收”的那笔订单也会超过 7 天的退货期。所以请在首次初始化后尽快演示退货，并且先发送“我的订单”查看实际的状态和编号。想重新演示退货，只能清空数据后重新初始化：运行 `docker compose down -v`（会删除全部数据），再重新执行上面的“构建并启动”和“初始化”两步。

## 本地开发（不用 Docker 运行应用）

需要 uv、Node.js 22（至少 20.9）和 Docker（只用来运行数据库）。后端和 MCP Server 读取根目录的 `.env`；本地开发时，数据库、后端、MCP 的地址都用 `.env.example` 里的默认值（localhost）。

1. 只启动数据库（仓库根目录）：

   ```powershell
   docker compose up -d db
   ```

2. 后端（`backend` 目录）：

   ```powershell
   uv sync
   uv run alembic upgrade head
   uv run uvicorn app.main:app --port 8000
   ```

3. MCP Server（另开一个终端，`backend` 目录）：

   ```powershell
   uv run uvicorn app.mcp_server.main:create_mcp_app --factory --port 8001
   ```

4. 初始化（`backend` 目录）：和 Docker 方式相同的四条命令，把 `docker compose exec backend python` 换成 `uv run python`，比如 `uv run python -m scripts.seed_demo_data alice`。

5. 前端（`frontend` 目录）：

   ```powershell
   npm ci
   Copy-Item .env.example .env.local
   npm run dev
   ```

后端只能运行一个进程，不要加 `--workers`，原因见“已知行为和限制”。

## 测试

### 后端

测试使用单独的数据库 `customer_service_test`，库名固定写在 `backend/tests/constants.py` 里，不需要另外设置环境变量。每个测试开始前会**清空并重建**这个库里的表。第一次运行前，在数据库容器运行的情况下创建一次（仓库根目录）：

```powershell
docker compose exec db sh -c 'createdb -U $POSTGRES_USER customer_service_test'
```

然后在 `backend` 目录运行：

```powershell
uv run pytest
```

测试覆盖业务规则、接口、MCP 工具和访问控制、数据库迁移，以及架构约束（比如业务层不能依赖接口层）。需要真实 BGE-M3 模型的混合检索评测带有 `embedding` 标记，默认不运行，见下面的“实验功能”。

### 前端（`frontend` 目录）

```powershell
npm test
npm run test:browser
npm run lint
```

`npm test` 运行单元测试（vitest）。`npm run test:browser` 运行浏览器测试（Playwright）：它会构建生产版本，在 3100 端口运行，后端接口全部在浏览器里模拟，不连接真实的后端和数据库。第一次运行前要安装浏览器：`npx playwright install chromium`。

## 架构

```
浏览器 ──► frontend（Next.js，:3000）
   │
   └────► backend（FastAPI，:8000）──► mcp（MCP Server，只在容器网络内，:8001）──► db
                  │                                                               ▲
                  └───────────────────────────────────────────────────────────────┘
                                                        db：PostgreSQL 17 + pgvector
```

- 后端负责登录、会话、聊天、确认卡片和管理端接口。助手查询业务数据全部通过 MCP 工具；后端每次调用都签发一个短期令牌，MCP 只返回当前顾客自己的数据。
- 写操作（下单、退货、撤回、转人工）不在 MCP 里执行：助手只能生成预览卡片，顾客确认后由后端执行。
- 启动顺序：db 健康 → migrate 成功退出 → mcp 就绪 → backend 健康 → frontend。
- 地址分两套：浏览器访问 `localhost:3000` 和 `localhost:8000`；容器之间用服务名（`db`、`mcp`、`backend`），这些地址写在 `docker-compose.yml` 里，会覆盖 `.env` 中的同名项。前端访问后端的地址在构建镜像时写入，修改后要重新构建。
- 密钥只从 `.env` 读取，`.dockerignore` 排除了 `.env*`，镜像里不包含密钥。

## 已知行为和限制

- **停止生成**：点“停止”后，没生成完的那段回复不保存。页面会重新读取服务器上的会话记录，所以半截回复会消失，顶部提示“已停止生成”。已经提交的订单、确认卡片和完整的回复，不会因为停止或断线被撤销，最终以重新读取的服务器记录为准。
- **连接中断**：回复期间连接断开（比如后端重启）时，页面提示连接中断，可以点“重新加载会话”核对，不会自动重发消息。
- **单进程**：知识库的索引任务在后端进程内排队执行，后端启动时还会把上次没做完的任务标记为失败，所以后端只能运行一个进程。改成多进程部署之前，要先把索引任务移出后端进程。
- **按字数切分**：知识文档按字数切成段落（约 600 字一段，相邻段重叠约 80 字），不是按 token 数切分。
- **容器里只用关键词检索**：镜像里不带 BGE-M3 模型。
- **只适合本机演示**：没有配置 HTTPS 和反向代理（`COOKIE_SECURE=false`），登录依赖前后端主机名一致。
- **离线模型**：只认上面列出的说法。
- **演示数据**：编号每次初始化都可能不同，签收时间按初始化的时刻计算。

## 功能完成情况

### 实验功能：已实现，但没有通过验收，默认关闭

**混合检索（关键词 + BGE-M3 向量）**：代码、数据库迁移和评测都已完成，设置 `RETRIEVAL_MODE=hybrid` 开启。两次验收都没有达标：相似度阈值区分不了“商品问对了、但文档里没写所问内容”的问题和“换了说法”的问题，无答案题的最高相似度（0.734）高于好几道正确答案的相似度。详细记录见 `backend/tests/business/test_knowledge_eval.py`。因此默认使用关键词检索。

本地开启方法（只用于本地开发，Docker 镜像里没有模型）：

1. 下载模型（有好几个 GB）：`uv run hf download BAAI/bge-m3 --local-dir <模型目录>`
2. 在 `.env` 里设置 `RETRIEVAL_MODE=hybrid` 和 `EMBEDDING_MODEL_DIR=<模型目录>`
3. 重建已有文档的索引：仓库自带的文档用 `uv run python -m scripts.load_knowledge --overwrite`（会用仓库里的文件覆盖同名文档）；管理端上传的文档在知识库页面点“重新索引”。文档的向量签名和当前模型对不上时，MCP Server 会拒绝启动。
4. 运行混合检索评测：`uv run pytest tests/business/test_knowledge_eval.py -m embedding -k tuning -s`

### 未实现

- **真实大模型**：已有接入接口（`LLM_PROVIDER=openai_compatible`，支持兼容 OpenAI 接口的服务），但没有完成真实模型的联调和验收。知识库引用的格式目前只有离线模型会按要求输出。
- **检索的重排序和问题改写**。
- **Checkpointer**：没有实现图执行状态的持久化。聊天历史和待确认的操作已经持久化在数据库里。
- **管理端的商品和订单维护、日志页**。
- **顾客端单独的“我的订单”页面**：这个页面没有做，但在聊天里查订单已经实现。

### 暂缓

- **顾客端查看和清除偏好的入口**：后台会自动记录顾客在聊天里明确说出的偏好，后端已经有查看和删除的接口，前端入口暂缓。在聊天里说“忘掉这个”不会删除偏好。

## 验收记录

- 日期：2026-11-07
- 代码：提交 `7f7e80d`。验收时后端镜像就是用这次提交的代码构建的；前端代码在 `c824362` 之后没有改动。
- 环境：Windows + Docker Desktop，全部服务用 Docker Compose 运行，离线模型，关键词检索。
- 自动化测试：后端 `uv run pytest` 394 项通过，3 项未运行（需要真实模型的混合检索评测）。前端单元测试和浏览器测试在 `413cbc9` 时全部通过，此后前端代码没有改动。
- 验收中发现并修复的问题：离线模型回复“我的订单”时只列出订单号，顾客查不到退货要用的订单项编号。在浏览器验收开始前修复（`7f7e80d`），下面的浏览器验收都在修复后进行。

### 路径 A：全新数据库

用单独的 Compose 项目（`docker compose -p heyi-fresh`，有独立的数据卷）从空数据库开始。

| # | 项目 | 结果 |
|---|---|---|
| A1 | 启动：migrate 为 Exited (0)，db、mcp、backend 为 healthy，frontend 为 Up | 通过 |
| A2 | 初始化：创建 admin 和 alice，放入演示数据，导入 7 篇知识文档 | 通过 |
| A3 | 四条初始化命令重跑一遍：账号跳过，不再提示输入密码，没有新增数据 | 通过 |
| A4 | 用 `--role admin --if-missing` 创建已存在的顾客 alice：报角色不一致，退出码为 1 | 通过 |
| A5 | 在后端容器里以 alice 的身份调用 MCP 工具 `list_my_orders`（经容器内地址 `mcp:8001`，带令牌） | 通过 |
| A6 | 登录后刷新页面，仍然保持登录（Cookie 生效） | 通过 |
| A7 | 显示离线演示说明；点带编号的说法时只填入前半句 | 通过 |
| A8 | 回复逐字输出时点停止：回复停止，提示“已停止生成”，半截回复不保存 | 通过 |
| A9 | 查商品 → 下单 2 件 → 确认 → 库存减少 2 | 通过 |
| A10 | “我的订单”列出订单状态和订单项编号 | 通过 |
| A11 | 对已签收的订单申请退货 → 确认 → 撤回 → 状态变为已撤回 | 通过 |
| A12 | 再申请一笔退货，留给管理员处理 | 通过 |
| A13 | 不支持退货的商品、超过退货期的订单：都不生成卡片，并说明原因 | 通过 |
| A14 | 问退货政策：回复摘录原文并显示引用 | 通过 |
| A15 | 转人工并关联订单，工单状态为待处理 | 通过 |
| A16 | 后端重启期间发送消息：出现错误提示；恢复后重新加载会话，记录完整，没有自动重发 | 通过 |
| A17 | 管理端：工单显示关联订单和带引用的聊天记录；填写处理结果，关闭工单 | 通过 |
| A18 | 管理端：完成退货申请 | 通过 |
| A19 | 管理端上传 Markdown 文档，变为可检索，顾客提问时引用了它 | 通过 |
| A20 | 顾客查看工单的处理结果、退货已完成 | 通过 |
| A21 | 停用这篇文档后，顾客再提问不再引用它 | 通过 |

### 路径 B：在路径 A 的数据上重启

| # | 项目 | 结果 |
|---|---|---|
| B1 | `down`（不删除数据卷）后重新 `up`：各服务状态和 A1 相同 | 通过 |
| B2 | 四条初始化命令重跑一遍：全部跳过，不提示输入密码 | 通过 |
| B3 | alice 用原来的密码登录 | 通过 |
| B4 | 会话、消息和确认卡片的状态不变 | 通过 |
| B5 | 库存保持下单后的数值 | 通过 |
| B6 | 退货申请（已撤回、已完成）和工单（已处理及处理结果）不变 | 通过 |
| B7 | 上传的文档仍然在，仍为停用状态；顾客提问仍然不引用它 | 通过 |

### 额外检查

用开发时一直在用的数据库（已有数据）启动全部服务：迁移没有改动任何数据，原有账号能登录，会话和订单都在。结果：通过。

### 没有在真实环境里测到的

- 45 秒收不到任何事件时按连接中断处理（心跳超时）：离线模型不会长时间没有输出，真实环境里触发不了。这一项由前端和后端的自动化测试覆盖。
