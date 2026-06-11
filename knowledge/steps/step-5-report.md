# Step 5 — 导出报告

## Precondition

编排器已确认步骤 0a~4 完成。

## Actions

每个 PASSED 用例生成独立 Markdown 报告，格式见 `references/report-spec.md`。

## 🔴 Output Format（上下文卫生）

1. **报告直写文件**：用 Write 工具写入
2. **允许输出**：报告数量、文件路径
3. **禁止输出**：完整报告内容

## Checklist

- [ ] 每个 PASSED 用例已生成报告

## Exit

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" done
```
