# HUI Playwright Agent Web 平台设计文档

> 日期: 2026-06-11
> 目标: 将现有 CLI Agent 改造为 Web 平台，降低使用门槛
> 部署方式: 本地部署（Web 服务和 Agent 跑在同一台机器上）
> 技术栈: Python FastAPI + Vue 3 + Element Plus

---

## 1. 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         浏览器 (Vue 3)                           │
│                                                                 │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐  │
│  │ 菜单管理 │ │ 执行控制 │ │ 实时日志 │ │ 历史记录 │ │ 配置编辑 │  │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘  │
│       └──────────┴──────────┴──────────┴──────────┘           │
│                           ↕ HTTP + WebSocket                    │
├─────────────────────────────────────────────────────────────────┤
│                    FastAPI 后端 (Python)                         │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    API 层 (routes/)                       │  │
│  │  /api/menus    /api/tasks    /api/config    /api/auth    │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                   服务层 (services/)                      │  │
│  │  MenuService  TaskService  ConfigService  AuthService    │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                   核心层 (adapters/)                      │  │
│  │  AgentAdapter — 包装现有 hui_agent 逻辑                   │  │
│  │  · run_menu(menu_name) → 流式返回进度                     │  │
│  │  · batch_run(menus) → 流式返回进度                        │  │
│  │  · resume(thread_id) → 流式返回进度                       │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                   基础设施层                              │  │
│  │  WebSocket Manager / Task Queue / SQLite / Auth          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                           ↕ 直接调用                            │
├─────────────────────────────────────────────────────────────────┤
│              HUI Agent 核心 (src/hui_agent/) — 现有代码          │
│                                                                 │
│  state.py  graph.py  cli.py  skill_steps.py  llm.py  ...      │
└─────────────────────────────────────────────────────────────────┘
```

**核心原则**:
- 现有 Agent 代码**零修改**（或极少修改），通过 `AgentAdapter` 层包装
- 前后端通过 REST API + WebSocket 通信
- 任务执行在后端线程/进程中，前端通过 WebSocket 接收实时进度

---

## 2. 模块划分

### 2.1 后端目录结构

```
web/
├── main.py                     # FastAPI 应用入口
├── config.py                   # Web 服务配置
├── requirements.txt            # Web 依赖
│
├── routes/                     # API 路由层
│   ├── __init__.py
│   ├── menus.py                # GET /api/menus — 菜单列表
│   ├── tasks.py                # POST /api/tasks — 触发执行
│   │                           # GET /api/tasks — 任务列表
│   │                           # GET /api/tasks/{id} — 任务详情
│   │                           # POST /api/tasks/{id}/resume — 恢复
│   │                           # POST /api/tasks/{id}/retry — 重试
│   ├── config.py               # GET/PUT /api/config — 配置读写
│   ├── ws.py                   # WebSocket /ws/tasks/{id} — 实时日志
│   └── auth.py                 # POST /api/auth/login — 登录
│
├── services/                   # 业务逻辑层
│   ├── __init__.py
│   ├── menu_service.py         # 菜单查询、过滤
│   ├── task_service.py         # 任务创建、执行、查询
│   ├── config_service.py       # Excel/JSON 配置读写
│   └── auth_service.py         # 用户认证
│
├── adapters/                   # Agent 适配层
│   ├── __init__.py
│   └── agent_adapter.py        # 包装现有 hui_agent 逻辑
│
├── models/                     # 数据模型
│   ├── __init__.py
│   ├── task.py                 # Task, TaskStatus
│   └── user.py                 # User
│
├── ws/                         # WebSocket 管理
│   ├── __init__.py
│   └── manager.py              # ConnectionManager
│
└── queue/                      # 任务队列
    ├── __init__.py
    └── task_queue.py           # 执行队列管理
```

### 2.2 前端目录结构

```
web/frontend/
├── package.json
├── vite.config.js
│
├── src/
│   ├── App.vue
│   ├── main.js
│   ├── router/index.js         # Vue Router
│   ├── stores/                 # Pinia 状态管理
│   │   ├── menu.js             # 菜单状态
│   │   ├── task.js             # 任务状态
│   │   └── auth.js             # 认证状态
│   │
│   ├── views/                  # 页面
│   │   ├── LoginView.vue       # 登录页
│   │   ├── DashboardView.vue   # 主面板（菜单列表 + 执行状态）
│   │   ├── TaskDetailView.vue  # 任务详情（实时日志 + 用例预览）
│   │   ├── HistoryView.vue     # 历史记录
│   │   └── ConfigView.vue      # 配置编辑
│   │
│   ├── components/             # 可复用组件
│   │   ├── MenuList.vue        # 菜单列表
│   │   ├── TaskCard.vue        # 任务卡片
│   │   ├── StepProgress.vue    # 步骤进度条
│   │   ├── LogViewer.vue       # 实时日志查看器
│   │   ├── DiffViewer.vue      # 结果对比
│   │   └── ConfigEditor.vue    # 配置编辑器
│   │
│   └── api/                    # API 调用封装
│       ├── client.js           # axios 实例
│       ├── menu.js
│       ├── task.js
│       └── config.js
│
└── public/
```

### 2.3 各模块职责

| 模块 | 职责 | 关键接口 |
|------|------|---------|
| `routes/` | HTTP 路由定义、请求验证、响应格式化 | 薄层，只做参数解析和调用 service |
| `services/` | 业务逻辑 | MenuService.list_menus()、TaskService.create_task() |
| `adapters/` | 包装现有 Agent 逻辑 | AgentAdapter.run_menu() → 流式返回进度事件 |
| `models/` | 数据库模型 | Task(id, menu_name, status, created_at, ...) |
| `ws/` | WebSocket 连接管理 | ConnectionManager.broadcast(task_id, event) |
| `queue/` | 任务队列 | TaskQueue.enqueue(task)、串行执行 |

---

## 3. 数据模型

使用 SQLite 存储（与现有 checkpoint 共用或独立数据库）。

### 3.1 Task（任务）

```python
class Task(Base):
    __tablename__ = "tasks"

    id: str              # 主键，UUID
    menu_name: str       # 菜单名称
    thread_id: str       # Agent 执行 ID（hui-xxx），可为空（排队中）
    status: str          # pending / running / success / failed / skipped / blocked
    current_step: str    # 当前步骤（0a ~ 5），可为空
    completed_steps: str # 已完成步骤，JSON 数组字符串
    failure_scope: str   # menu / environment / config / null
    failure_reason: str  # 失败原因
    created_at: datetime # 创建时间
    started_at: datetime # 开始执行时间，可为空
    finished_at: datetime # 完成时间，可为空
    created_by: str      # 创建人用户名
    result_json: str     # 完整结果（HUITestState 的 JSON），可为空
    case_dir: str        # 生成物目录，可为空
    reports: str         # 报告路径，JSON 数组字符串
```

### 3.2 User（用户）

```python
class User(Base):
    __tablename__ = "users"

    id: str              # 主键，UUID
    username: str        # 用户名，唯一
    password_hash: str   # bcrypt 密码哈希
    role: str            # admin / operator / viewer
    created_at: datetime
    last_login: datetime # 最后登录时间，可为空
```

### 3.3 ExecutionLog（执行日志）

```python
class ExecutionLog(Base):
    __tablename__ = "execution_logs"

    id: int              # 自增主键
    task_id: str         # 关联任务 ID（外键）
    timestamp: datetime
    step: str            # 步骤编号
    event: str           # 事件类型
    message: str         # 日志内容
    payload: str         # 详细数据，JSON 字符串
```

---

## 4. API 接口设计

### 4.1 REST API

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| **认证** | | | |
| POST | `/api/auth/login` | 登录，返回 JWT token | 公开 |
| GET | `/api/auth/me` | 获取当前用户信息 | 已登录 |
| **菜单** | | | |
| GET | `/api/menus` | 获取未完成菜单列表 | viewer+ |
| GET | `/api/menus/{name}/config` | 获取菜单配置详情 | viewer+ |
| **任务** | | | |
| POST | `/api/tasks` | 创建并执行任务（单菜单） | operator+ |
| POST | `/api/tasks/batch` | 批量创建任务 | operator+ |
| GET | `/api/tasks` | 任务列表（支持分页、筛选） | viewer+ |
| GET | `/api/tasks/{id}` | 任务详情 | viewer+ |
| POST | `/api/tasks/{id}/resume` | 恢复执行 | operator+ |
| POST | `/api/tasks/{id}/retry` | 从指定步骤重试 | operator+ |
| DELETE | `/api/tasks/{id}` | 删除任务记录 | admin |
| **配置** | | | |
| GET | `/api/config/sheets` | 获取 Excel 的 sheet 列表 | viewer+ |
| GET | `/api/config/sheets/{name}` | 获取 sheet 数据 | viewer+ |
| PUT | `/api/config/sheets/{name}` | 更新 sheet 数据 | operator+ |
| GET | `/api/config/base-tables` | 获取 base_tables.json | viewer+ |
| PUT | `/api/config/base-tables` | 更新 base_tables.json | operator+ |
| **系统** | | | |
| GET | `/api/system/health` | 健康检查 | 公开 |
| GET | `/api/system/stats` | 统计信息 | viewer+ |

### 4.2 WebSocket 接口

```
ws://localhost:8000/ws/tasks/{task_id}
```

**服务端推送的消息格式**:

```json
{
  "type": "step_started|step_finished|step_failed|log|pytest_result|progress",
  "data": {
    "step": "2c",
    "event": "sql_dry_run_started",
    "message": "attempt=1 kind=preset_sql",
    "timestamp": "2026-06-11T17:07:29"
  }
}
```

**消息类型说明**:

| type | 含义 | 前端处理 |
|------|------|---------|
| `step_started` | 步骤开始 | 高亮当前步骤 |
| `step_finished` | 步骤完成 | 标记步骤完成，更新进度 |
| `step_failed` | 步骤失败 | 标记步骤失败 |
| `log` | 日志消息 | 追加到日志面板 |
| `pytest_result` | pytest 用例结果 | 更新用例状态 |
| `progress` | 进度更新 | 更新进度百分比 |

---

## 5. AgentAdapter 适配层

### 5.1 设计思路

把现有的 CLI 调用逻辑包装成可被 Web 服务调用的形式，通过 callback 机制实现实时进度推送。

```python
class AgentAdapter:
    """包装现有 hui_agent 逻辑，供 Web 服务调用。"""

    def __init__(self, skill_root: str, checkpoint_db: str):
        self.skill_root = skill_root
        self.checkpoint_db = checkpoint_db

    def run_menu(
        self,
        menu_name: str,
        callback: Callable[[dict], None],
    ) -> HUITestState:
        """
        执行单个菜单，通过 callback 实时推送进度。

        callback 接收的事件格式:
        {"type": "step_started", "step": "0a", "message": "menu_init"}
        {"type": "log", "step": "0a", "message": "config.json 已写入"}
        {"type": "step_finished", "step": "0a", "status": "success"}
        {"type": "progress", "step": "0a", "percent": 9}
        """
        # ... 实现细节见下方
```

### 5.2 事件转换

将现有的 script_event_logger 事件转换为统一格式:

| 现有事件 | 转换后的 type | 说明 |
|---------|-------------|------|
| `step_started` | `step_started` | 直接映射 |
| `step_finished` | `step_finished` | 直接映射 |
| `step_failed` | `step_failed` | 直接映射 |
| `script_started` | `log` | 转为日志消息 |
| `script_finished` | `log` | 转为日志消息 |
| `script_failed` | `log` | 转为日志消息 |
| `pytest_started` | `log` | 转为日志消息 |
| `pytest_finished` | `pytest_result` | 转为用例结果 |
| `pytest_case_result` | `pytest_result` | 转为用例结果 |
| `llm_sql_repair_started` | `log` | 转为日志消息 |
| `llm_sql_repair_finished` | `log` | 转为日志消息 |
| `sql_dry_run_started` | `log` | 转为日志消息 |
| `sql_dry_run_finished` | `log` | 转为日志消息 |
| 其他 | `log` | 默认转为日志消息 |

### 5.3 进度计算

```python
STEP_ORDER = ("0a", "0b", "1a", "1b", "2a", "2b", "2c", "2d", "3", "4", "5")

def calculate_progress(step: str) -> int:
    """计算进度百分比 (0-100)。"""
    if step not in STEP_ORDER:
        return 0
    return int((STEP_ORDER.index(step) + 1) / len(STEP_ORDER) * 100)
```

---

## 6. 前端页面设计

### 6.1 登录页

简单的用户名+密码登录表单。

### 6.2 主面板（Dashboard）

分为三个区域:
- **概览卡片**: 总任务数、运行中任务数、成功率、今日执行数
- **运行中的任务**: 实时显示当前执行的任务，带步骤进度条
- **最近完成**: 最近完成的任务列表，带状态标识

### 6.3 任务详情（Task Detail）

分为四个区域:
- **步骤进度条**: 11 个步骤的完成状态（✓ 完成 / ● 运行中 / ○ 未开始）
- **实时日志面板**: WebSocket 推送的日志，支持自动滚动、暂停、导出
- **生成的用例列表**: 可点击查看文件内容
- **SQL 预埋文件列表**: 可点击查看文件内容

### 6.4 历史记录

- **筛选**: 按状态、日期范围、菜单名筛选
- **列表**: 显示任务的基本信息和操作按钮
- **对比**: 选中两个任务后可对比执行结果

### 6.5 配置编辑

分为五个 Tab:
- **环境配置**: Chrome/CDP、日志服务器、数据库连接信息
- **菜单导入**: 菜单列表的增删改查
- **预埋数据**: Excel 预埋数据的可视化编辑
- **基础表**: base_tables.json 的编辑
- **表结构缓存**: schema_cache.json 的查看

---

## 7. 认证与权限

### 7.1 登录流程

```
用户输入用户名+密码 → POST /api/auth/login
→ 后端验证密码哈希（bcrypt）
→ 返回 JWT token（有效期 24 小时）
→ 前端存储 token 到 localStorage
→ 后续请求带 Authorization: Bearer <token>
```

### 7.2 角色权限矩阵

| 功能 | admin | operator | viewer |
|------|-------|----------|--------|
| 查看菜单列表 | ✓ | ✓ | ✓ |
| 查看任务详情 | ✓ | ✓ | ✓ |
| 查看历史记录 | ✓ | ✓ | ✓ |
| 触发执行 | ✓ | ✓ | ✗ |
| 恢复/重试 | ✓ | ✓ | ✗ |
| 编辑配置 | ✓ | ✓ | ✗ |
| 用户管理 | ✓ | ✗ | ✗ |
| 删除任务 | ✓ | ✗ | ✗ |

### 7.3 默认管理员

首次启动时自动创建默认管理员账号:
- 用户名: `admin`
- 密码: `admin123`（强制首次登录修改）

---

## 8. 任务队列

### 8.1 为什么需要队列

- Chrome/CDP 是共享资源，不能并发执行多个菜单
- SSH 日志服务器也是共享的
- 数据库连接池有限

### 8.2 队列策略

- 最大并发数: 1（串行执行，与现有 batch 行为一致）
- 排队中的任务: 显示队列位置
- 支持取消排队中的任务

### 8.3 执行流程

```
用户点击"开始执行"
  → 创建 Task(status=pending)
  → 入队
  → 队列取出任务
  → Task(status=running)
  → 执行（WebSocket 实时推送进度）
  → 完成
  → Task(status=success/failed)
  → 取下一个排队任务
```

---

## 9. 错误处理

| 错误场景 | 前端表现 | 后端处理 |
|---------|---------|---------|
| Chrome 连不上 | 任务失败，提示"环境异常" | EnvironmentFailure → blocked |
| SSH 连接超时 | 步骤失败，可重试 | MenuFailure → 可 resume |
| LLM API 不可用 | 步骤失败，可重试 | 跳过 LLM 修复，用确定性逻辑 |
| pytest 全部失败 | 任务失败，显示失败用例 | 记录到 repair_attempts |
| WebSocket 断连 | 自动重连（3 秒间隔），日志不丢失 | 日志同时写数据库 |
| 数据库写入失败 | 提示"保存失败" | 重试一次，仍失败则报错 |

---

## 10. 技术栈明细

### 10.1 后端

| 组件 | 技术选型 | 版本 | 说明 |
|------|---------|------|------|
| Web 框架 | FastAPI | >=0.100 | 异步、自动文档、性能好 |
| ASGI 服务器 | Uvicorn | >=0.20 | FastAPI 的标准服务器 |
| WebSocket | FastAPI 内置 | - | 实时日志推送 |
| ORM | SQLAlchemy | >=2.0 | 数据库操作 |
| 数据库 | SQLite | - | 本地部署，零运维 |
| 认证 | python-jose + passlib | - | JWT + bcrypt |
| 任务队列 | threading + queue.Queue | 标准库 | 串行执行，无需 Celery |
| 配置 | Pydantic Settings | >=2.0 | 类型安全的配置管理 |
| Excel 处理 | openpyxl | - | 读写 Excel 文件 |
| CORS | FastAPI CORSMiddleware | - | 跨域支持 |

### 10.2 前端

| 组件 | 技术选型 | 版本 | 说明 |
|------|---------|------|------|
| 框架 | Vue | ^3.4 | 响应式、组件化 |
| 构建工具 | Vite | ^5.0 | 快速开发构建 |
| 状态管理 | Pinia | ^2.0 | Vue 3 官方推荐 |
| 路由 | Vue Router | ^4.0 | 页面导航 |
| UI 组件库 | Element Plus | ^2.0 | 中文友好的 UI 库 |
| HTTP | Axios | ^1.6 | API 调用 |
| 代码高亮 | highlight.js | ^11.0 | 日志/代码高亮 |
| 图标 | @element-plus/icons-vue | - | Element Plus 图标 |

---

## 11. 分阶段实施计划

### Phase 1: 后端基础（1 周）

**目标**: 后端 API 可用，能通过 HTTP 触发执行并收到 WebSocket 推送。

**任务**:
- [ ] FastAPI 应用骨架（main.py、config.py、requirements.txt）
- [ ] 数据库模型定义（Task、User、ExecutionLog）
- [ ] 数据库初始化脚本（自动建表 + 默认管理员）
- [ ] AgentAdapter 适配层（run_menu 方法）
- [ ] REST API: 菜单列表、任务 CRUD
- [ ] WebSocket 实时日志推送
- [ ] 简单的 JWT 认证

**交付物**: 可通过 curl/Postman 测试的 API。

### Phase 2: 前端基础（1.5 周）

**目标**: 可通过浏览器操作的基本 UI。

**任务**:
- [ ] Vue 3 项目搭建（Vite + Element Plus + Pinia + Vue Router）
- [ ] 登录页
- [ ] 主面板（菜单列表 + 执行状态卡片）
- [ ] 任务详情页（步骤进度条 + 实时日志面板）
- [ ] API 对接（axios 封装、WebSocket 连接）
- [ ] 路由守卫（未登录跳转登录页）

**交付物**: 可通过浏览器登录、查看菜单、触发执行、查看实时日志。

### Phase 3: 核心功能（1.5 周）

**目标**: 完整的执行控制和历史管理。

**任务**:
- [ ] 任务队列（串行执行、排队显示）
- [ ] 暂停/恢复/重试功能
- [ ] 批量执行
- [ ] 历史记录页（分页、筛选）
- [ ] 结果对比功能
- [ ] 任务详情页完善（用例列表、SQL 文件预览）

**交付物**: 可批量执行、查看历史、对比结果。

### Phase 4: 配置 + 权限（1 周）

**目标**: 配置可视化编辑和完整的权限控制。

**任务**:
- [ ] 配置编辑页（环境配置、菜单导入、预埋数据）
- [ ] base_tables.json 可视化编辑
- [ ] 用户管理页（admin 可用）
- [ ] 角色权限中间件
- [ ] 健康检查 + 统计信息 API

**交付物**: 可在网页上编辑配置、管理用户。

### Phase 5: 打磨（1 周）

**目标**: 生产可用的质量。

**任务**:
- [ ] 错误处理完善（全局异常处理、友好的错误提示）
- [ ] UI 样式优化（响应式布局、暗色主题可选）
- [ ] WebSocket 自动重连 + 离线日志补推
- [ ] 性能优化（懒加载、虚拟滚动）
- [ ] 部署脚本（一键启动）
- [ ] 使用文档

**交付物**: 可交付给团队使用的完整平台。

---

## 12. 部署方式

### 12.1 启动命令

```powershell
# 后端
cd web
pip install -r requirements.txt
python main.py
# 启动在 http://localhost:8000

# 前端（开发模式）
cd web/frontend
npm install
npm run dev
# 启动在 http://localhost:5173

# 前端（生产模式）
cd web/frontend
npm run build
# 构建产物在 web/frontend/dist/，由 FastAPI 静态文件服务
```

### 12.2 生产部署

```powershell
# 一键启动（后端服务前端静态文件）
cd web
python main.py --host 0.0.0.0 --port 8000
# 访问 http://localhost:8000
```

---

## 13. 对现有代码的影响

### 13.1 需要修改的现有文件

| 文件 | 修改内容 | 影响范围 |
|------|---------|---------|
| `cli.py` | 无修改，Web 端通过 AgentAdapter 独立调用 | 无 |
| `skill_steps.py` | 无修改 | 无 |
| `graph.py` | 无修改 | 无 |
| `state.py` | 无修改 | 无 |
| `llm.py` | 无修改 | 无 |

### 13.2 新增的文件

全部在 `web/` 目录下，不与现有代码冲突。

### 13.3 依赖管理

Web 依赖单独在 `web/requirements.txt` 中管理，不影响现有项目的 `pyproject.toml`。

---

## 14. 风险与应对

| 风险 | 影响 | 应对措施 |
|------|------|---------|
| Agent 执行时间长（5-10 分钟） | WebSocket 可能断连 | 心跳机制 + 断连重连 + 日志持久化到数据库 |
| Excel 文件并发编辑 | 数据冲突 | 配置编辑加锁（乐观锁） |
| SQLite 并发写入 | 性能瓶颈 | WAL 模式 + 写入队列 |
| 现有 Agent 代码改动 | 影响 CLI 功能 | AgentAdapter 层隔离，不修改现有代码 |
| 前端打包体积大 | 首次加载慢 | 路由懒加载 + 代码分割 |
