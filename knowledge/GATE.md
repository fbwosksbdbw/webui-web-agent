# GATE.md — 决策矩阵索引

每个步骤开始时，**按 skill.md 步骤表查阅对应的 Gate 文件**。所有决策数据写入 `case_plan.json`（增量更新，每步追加）。

| 步骤 | Gate 文件 | 决策内容 |
|:---:|---|---|
| 1a | [gates/gate-1a.md](gates/gate-1a.md) | 字段特征、查询策略、客户路由、queryall 模板 |
| 1b | [gates/gate-1b.md](gates/gate-1b.md) | 分行过滤、用例数（5/7）、预埋策略、筛选字段、重置字段覆盖 |
| 3 | [gates/gate-3.md](gates/gate-3.md) | 用例生成顺序、模板选择、输出路径、命名规则 |

**索引规则**：

| 规则 | 详见 |
|------|------|
| 禁止用固定关键字 grep 判定分行过滤 | gate-1b.md |
| branch 必须第一个生成 | gate-3.md |
| querymulti 去重（排除 required 已填字段） | gate-3.md |
| 重置测试字段覆盖 | gate-1b.md |
| 配置表 → ui_bc，分片表 → 分片库 | gate-1b.md |
| 🔴 `shard.db` 权威来源是 `classify` 命令的 `db` 字段，禁止根据模块名推断 | step-2b |
