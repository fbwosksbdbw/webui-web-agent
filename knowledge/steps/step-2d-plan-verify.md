# Step 2d — 验证 case_plan.json

## Precondition

编排器已确认步骤 0a~2c 完成。

## Actions

确认所有字段已填满，无残留占位符。后续步骤 3 完全依赖此文件。

逐项检查：
- [ ] `menu` — 全部字段非空
- [ ] `gate_decisions` — 全部字段非空
- [ ] `case_order` — 数组长度 == case_count
- [ ] `form_fields` — required / reset_test_field / preview_buttons 均非空
- [ ] `filter_selections` — single / multi 已填入
- [ ] `shard` — db / physical_tables 均非空；routing_db 可为空（全 ui_bc 表时）；db 来自 step 2b `classify` 命令输出的 `db` 字段（权威数据源），禁止根据模块名推断
- [ ] `expected_counts` — case_count=7 时 branch / headquarter 已填入；case_count=5 时可留空
- [ ] 🔴 DB 一致性：对 `preset_strategy.biz_tables` 中每张表，`resolve_shard_db(table, routing_db)` 与 `shard.db` 一致（跨库的表在 `shard.extra_dbs` 中）
- [ ] `paths` — project_dir / test_case_dir / templates 已填入

## 🔴 Output Format（上下文卫生）

1. **允许输出**：验证结论（PASS/缺失字段列表）
2. **禁止输出**：逐字段检查过程、case_plan.json 完整内容

## Checklist

- [ ] 以上所有字段均已逐项验证

## Exit

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" done
```
