# Step 0b — 启动浏览器 + 自动登录

## Precondition

编排器已确认步骤 0a 完成。

## Actions

从 `config.json` 的 `env` 字段中提取：
- `login_url` — 登录地址（SkillConfig 从 `conf/config.py` 读取）
- `branch_user` / `branch_password` — 分行账号

`{config_json}` = `{paths.project_dir}/{paths.test_case_dir}/{menu.level2}/{menu.name}/config.json`

### 第 1 步：检查浏览器是否可复用（批量模式）

**批量模式下**（非首个菜单），浏览器已在运行。先做健康检查：

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/cdp_executor.py" \
  --config "{config_json}" \
  --cdp-url {cdp_url} \
  --health-check
```

- exit 0 → 浏览器健康，跳到第 3 步（直接 navigate）
- exit 1 → 浏览器异常，需要恢复或重启

**健康检查失败时的恢复**：
```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/cdp_executor.py" \
  --config "{config_json}" \
  --cdp-url {cdp_url} \
  --recover --navigate "{menu_url}"
```

恢复仍失败 → 执行第 2 步重新启动 Chrome。

### 第 2 步：启动 Chrome（自动，无交互确认）

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/launch_chrome.py" --auto --config "{config_json}"
```

`--auto` 模式：`kill_chrome()` → `launch_chrome()` → `check_cdp()`，不弹确认提示。

### 第 3 步：登录 + 导航到目标菜单

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/cdp_executor.py" \
  --config "{config_json}" \
  --cdp-url {cdp_url} \
  --login --login-user {branch_user} --login-pass {branch_password} --login-url {login_url} \
  --navigate "{menu_url}"
```

cdp_executor 内部执行顺序：
1. 导航到登录页 → 填写用户名/密码 → 点击登录 → 等待"欢迎" → 处理弹窗
2. 导航到目标菜单（`navigate()` 含页面元素验证）

### 第 4 步：验证 Node.js 环境

```bash
PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/stagehand_explorer/src/explore_wrapper.py" --check-node
```
退出码 0 = OK，非 0 = 需安装 Node.js >= 18。

## 🔴 Output Format（上下文卫生）

1. **允许输出**：登录状态、导航结果、Node.js 版本
2. **禁止输出**：launch_chrome / cdp_executor 的完整 stdout 原文

## Checklist

- [ ] Chrome 已启动（`--auto` 已包含 CDP 验证）
- [ ] 已登录，导航到目标菜单成功
- [ ] Node.js 环境可用

## Exit

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" done
```
