# Step 0a — 菜单发现 + 读取配置

## Precondition

编排器已初始化（`orchestrator.py init` 已在 skill.md 入口执行）。

## Actions

### 0. 部署框架文件（首次运行）

```bash
python "{SKILL_DIR}/scripts/deploy_helpers.py" --project-dir "{project_dir}"
```

确保 `{project_dir}/common/helpers.py` 存在且版本不低于 skill 内权威副本。首次运行时创建，后续版本比较后决定是否覆盖（覆盖前自动备份 `.bak`）。

退出码 0 = OK（已是最新/已部署），非 0 = 错误。

### 1. 列出待办菜单（如用户未指定菜单名）

```bash
python "{SKILL_DIR}/scripts/config_reader.py" menus
```

Agent 按列表顺序逐个处理，每个菜单独立走完整流程（0a→4）。

### 2. 读取配置（选定菜单后一条命令）

```bash
python "{SKILL_DIR}/scripts/config_reader.py" init "{菜单名}"
```

`config_reader.py init` 做两件事：
1. **输出 JSON 到 stdout**：包含 `env`、`presets`、`menu`、`menu_url`、`module`、`paths`、`field_groups`
2. **写入 `config.json`** 到用例目录（`{test_case_dir}/{二级菜单}/{菜单名}/config.json`），包含 Excel 三个 sheet 的完整快照 + 推导值

### 3. 缓存预热（如有 `update_cache` 配置）

从 `config.json` 的 `env.update_cache` 读取模块名（如 `信托`），映射到数据库并刷新缓存：

| update_cache 值 | 数据库 |
|:---:|:---:|
| 信托 | ui_trust1, ui_trust2 |
| 基金 | ui_fund1, ui_fund2 |
| 理财 | ui_fina1, ui_fina2 |
| 公共 | ui_bc |
| 商业养老金 | ui_fina1, ui_fina2 |

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/build_schema_cache.py" --auto --force --dbs ui_bc ui_trust1 ui_trust2
```

如果 `update_cache` 为空或不存在，跳过此步。

### 4. 创建 case_plan.json

`config.json` 是后续所有步骤读取 env/presets 的唯一来源，禁止再读 Excel。

**🔴 必须按 `assets/templates/case_plan.json` 全字段结构初始化。** 所有后续步骤依赖的字段不可遗漏。当前已知的值直接填入，未知的值用占位符或空值，后续步骤逐步填充。

从输出的 `menu` 对象中提取字段，创建 `case_plan.json`：

```json
{
  "menu": {
    "name": "<menu.菜单名称>",
    "code": "<menu.功能代码>",
    "url": "<menu_url>",
    "module": "<module>",
    "level1": "<menu.导航一级菜单>",
    "level2": "<menu.导航二级菜单>"
  },
  "gate_decisions": {},
  "case_order": [],
  "form_fields": {
    "all": [],
    "required": [],
    "reset_test_field": {},
    "preview_buttons": []
  },
  "field_groups": "<从 config.json 的 field_groups 复制>",
  "filter_selections": {},
  "preset_strategy": {},
  "shard": {},
  "expected_counts": {},
  "paths": {
    "project_dir": "<paths.project_dir>",
    "test_case_dir": "<paths.test_case_dir>",
    "templates": {}
  }
}
```

## 🔴 Output Format（上下文卫生）

1. **config.json / case_plan.json 完整内容不复述**（太长），但可以输出关键字段值
2. **允许输出**：菜单名、功能代码、模块、路径、缓存状态等关键信息
3. **禁止输出**：config_reader.py init 的完整 JSON 原文、case_plan.json 完整结构

## Checklist

- [ ] `deploy_helpers.py` 已执行（common/helpers.py 已部署且为最新版本）
- [ ] 已运行 `config_reader.py init`，config.json 已生成
- [ ] 已检查 `update_cache` 并按需执行缓存预热
- [ ] case_plan.json 已创建，menu / field_groups / paths 非空

## Exit

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" done
```
