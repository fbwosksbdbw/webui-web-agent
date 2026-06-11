from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PlannedField:
    label: str
    type: str
    value: Any
    reason: str


@dataclass(frozen=True)
class CaseInput:
    purpose: str
    fields: list[PlannedField] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)


class CaseInputPlanner:
    def __init__(
        self,
        *,
        fields: list[dict[str, Any]],
        field_groups: list[dict[str, Any]],
        preset_fields: dict[str, Any] | None = None,
    ) -> None:
        self.preset_fields = preset_fields or {}
        self.fields = [self._with_preset_value(field) for field in fields]
        self.field_groups = field_groups
        self.by_label = {field["label"]: field for field in self.fields}

    def plan(self, *, single_filter_label: str | None = None) -> dict[str, CaseInput]:
        required = self._required_closure()
        excluded = self._excluded()
        single = self._single_filter(single_filter_label, excluded)
        stable_multi = self._stable_multi_fields(required, excluded)
        reset = self._reset_field(excluded)
        preview = stable_multi or required
        return {
            "queryall": CaseInput("验证必填条件或空表单全量查询", required, excluded),
            "querysingle": CaseInput("验证单一业务过滤条件", self._dedupe(required + single), excluded),
            "querymulti": CaseInput("验证稳定多条件组合", stable_multi, excluded),
            "branch": CaseInput("验证分行权限精准查询条件", stable_multi or required, excluded),
            "headquarter": CaseInput("验证总行权限精准查询条件", stable_multi or required, excluded),
            "reset": CaseInput("验证重置清空稳定输入字段", reset, excluded),
            "preview": CaseInput("驱动列表出现可预览记录", preview, excluded),
        }

    def _required_closure(self) -> list[PlannedField]:
        labels = [field["label"] for field in self.fields if field.get("is_required")]
        return self._dedupe(self._close_groups(labels, "required"))

    def _single_filter(
        self,
        preferred_label: str | None,
        excluded: dict[str, str],
    ) -> list[PlannedField]:
        label = preferred_label or self._first_label(
            [
                "客户标识",
                "产品代码",
                "TA代码",
                "账号",
                "账户",
            ],
            excluded,
        )
        if not label:
            label = next(
                (field["label"] for field in self.fields if field["label"] not in excluded),
                None,
            )
        return self._close_groups([label], "single_filter") if label else []

    def _stable_multi_fields(
        self,
        required: list[PlannedField],
        excluded: dict[str, str],
    ) -> list[PlannedField]:
        labels = [field.label for field in required]
        for candidate in ("客户标识", "账号", "账户", "TA代码", "产品代码", "监控天数", "开始日期", "结束日期"):
            actual = self._actual_label_for_candidate(candidate, excluded)
            if actual:
                labels.append(actual)
        return self._dedupe(self._close_groups(labels, "stable_multi"))

    def _reset_field(self, excluded: dict[str, str]) -> list[PlannedField]:
        date = next(
            (
                field
                for field in self.fields
                if field["label"] not in excluded
                and field.get("type") == "input"
                and "日期" in field["label"]
                and self._has_stable_value(field)
            ),
            None,
        )
        selected = date or next(
            (
                field
                for field in self.fields
                if field["label"] not in excluded
                and field.get("type") == "input"
                and self._has_stable_value(field)
            ),
            None,
        )
        if selected:
            return [self._planned(selected, "resettable")]
        select = next(
            (
                field
                for field in self.fields
                if field["label"] not in excluded
                and field.get("type") in {"single-select", "multi-select"}
                and self._has_stable_value(field)
            ),
            None,
        )
        return self._dedupe(self._close_groups([select["label"]], "resettable")) if select else []

    def _excluded(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for field in self.fields:
            label = field["label"]
            if field.get("type") == "tree-select":
                result[label] = "tree_select_unstable"
            elif any(token in label for token in ("申请编号", "流水号", "合同号", "协议号", "单号")):
                result[label] = "volatile_unique"
        return result

    def _close_groups(self, labels: list[str | None], reason: str) -> list[PlannedField]:
        expanded: list[str] = []
        for label in labels:
            if not label:
                continue
            group = self._group_for(label)
            if group:
                expanded.extend(
                    self._actual_group_order(group)
                    or group.get("order")
                    or group.get("fields")
                    or []
                )
            else:
                expanded.append(label)
        return [
            self._planned(self.by_label[label], reason)
            for label in expanded
            if label in self.by_label
        ]

    def _group_for(self, label: str) -> dict[str, Any] | None:
        for group in self.field_groups:
            aliases = group.get("aliases", {}) or {}
            canonical = aliases.get(label, label)
            if canonical in group.get("fields", []):
                return group
        return None

    def _actual_group_order(self, group: dict[str, Any]) -> list[str]:
        order = group.get("order") or group.get("fields") or []
        aliases = group.get("aliases", {}) or {}
        canonical_to_actual: dict[str, str] = {}
        for actual_label in self.by_label:
            canonical = aliases.get(actual_label, actual_label)
            if canonical in order and canonical not in canonical_to_actual:
                canonical_to_actual[canonical] = actual_label
        return [canonical_to_actual[label] for label in order if label in canonical_to_actual]

    def _first_label(self, candidates: list[str], excluded: dict[str, str]) -> str | None:
        for candidate in candidates:
            actual = self._actual_label_for_candidate(candidate, excluded)
            if actual:
                return actual
        return None

    def _actual_label_for_candidate(self, candidate: str, excluded: dict[str, str]) -> str | None:
        if candidate in self.by_label and candidate not in excluded:
            return candidate
        for actual_label in self.by_label:
            if actual_label in excluded:
                continue
            for group in self.field_groups:
                aliases = group.get("aliases", {}) or {}
                canonical = aliases.get(actual_label, actual_label)
                if canonical == candidate:
                    return actual_label
        return None

    def _with_preset_value(self, field: dict[str, Any]) -> dict[str, Any]:
        label = field.get("label", "")
        if not label:
            return field
        preset_value = self._preset_value_for_label(label)
        if not preset_value:
            preset_value = self._semantic_value_for_label(label)
        if not preset_value:
            return field
        patched = dict(field)
        patched["value"] = preset_value
        patched["value_source"] = "preset"
        return patched

    def _preset_value_for_label(self, label: str) -> str:
        for info in self.preset_fields.values():
            if not isinstance(info, dict):
                continue
            if info.get("name") == label:
                return str(info.get("value") or "")
        return ""

    def _semantic_value_for_label(self, label: str) -> str:
        normalized = label.replace(" ", "")
        if "监控天数" in normalized:
            return "30"
        if "开始日期" in normalized or "起始日期" in normalized:
            return self._preset_value_by_keys("query_date_start", "start_date", "begin_date")
        if "结束日期" in normalized or "截止日期" in normalized:
            return self._preset_value_by_keys("query_date_end", "end_date")
        return ""

    def _preset_value_by_keys(self, *keys: str) -> str:
        for key in keys:
            info = self.preset_fields.get(key)
            if isinstance(info, dict) and info.get("value"):
                return str(info.get("value") or "")
        return ""

    @staticmethod
    def _has_stable_value(field: dict[str, Any]) -> bool:
        value = str(field.get("value") or "")
        return bool(value and value != "__FIRST_OPTION__")

    @staticmethod
    def _planned(field: dict[str, Any], reason: str) -> PlannedField:
        return PlannedField(
            label=field["label"],
            type=field.get("type", ""),
            value=field.get("value", ""),
            reason=reason,
        )

    @staticmethod
    def _dedupe(fields: list[PlannedField]) -> list[PlannedField]:
        seen: set[str] = set()
        result: list[PlannedField] = []
        for field in fields:
            if field.label in seen:
                continue
            seen.add(field.label)
            result.append(field)
        return result
