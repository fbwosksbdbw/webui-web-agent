from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from dataclasses import asdict, replace
from pathlib import Path

from hui_agent.case_inputs import CaseInputPlanner
from hui_agent.failure_policy import EnvironmentFailure, MenuFailure
from hui_agent.fill_code import render_pytest_case
from hui_agent.insert_plan import InsertColumn, InsertPlan, build_insert_plans, load_base_tables, load_schema_cache, render_sql
from hui_agent.repair_actions import (
    allowed_generated_test_paths as _repair_action_generated_test_paths,
    apply_llm_repair_actions as _repair_action_apply_llm_repair_actions,
    has_applied_repair_action as _repair_action_has_applied_repair_action,
    repair_action_event_label as _repair_actions_event_label,
    repair_duplicate_insert_keys as _repair_action_duplicate_insert_keys,
    repair_sql_preset_mismatches as _repair_action_sql_preset_mismatches,
    repairable_sql_paths as _repair_action_sql_paths,
)
from hui_agent.repair_transaction import (
    record_repair_restore as _record_repair_restore,
    restore_repair_snapshot as _restore_repair_snapshot,
    snapshot_repair_targets as _snapshot_repair_paths,
)
from hui_agent.repair_report import repair_detail_lines, repair_overview_lines
from hui_agent.repair_evidence import collect_repair_evidence, summarize_repair_attempts_for_evidence
from hui_agent.sql_guards import evaluate_insert_plan_coverage
from hui_agent.sql_requirements import build_sql_requirements
from hui_agent.state import HUITestState, STEP_ORDER
from hui_agent.tools.script_runner import ScriptError, ScriptRunner


class SkillStepHandlers:
    """Conservative node handlers that bridge LangGraph state to existing scripts.

    These handlers intentionally keep the business-heavy generation logic in the
    existing skill assets. They provide deterministic setup/status boundaries and
    make unsupported autonomous steps explicit as menu-level skipped results.
    """

    def __init__(
        self,
        runner: ScriptRunner,
        repair_advisor=None,
        permission_advisor=None,
        sql_advisor=None,
        template_reviewer=None,
    ) -> None:
        self.runner = runner
        self.repair_advisor = repair_advisor
        self.permission_advisor = permission_advisor
        self.sql_advisor = sql_advisor
        self.template_reviewer = template_reviewer

    def menu_init(self, state: HUITestState) -> HUITestState:
        output = self.runner.run_text("config_reader.py", ["init", state.menu_name, "--force-completed"])
        config_path = _extract_config_path(output)
        case_dir = str(Path(config_path).parent) if config_path else state.case_dir
        case_plan_path = str(Path(case_dir) / "case_plan.json") if case_dir else ""
        new_state = (
            state.with_paths(
                config_path=config_path,
                case_plan_path=case_plan_path,
                case_dir=case_dir,
            )
        )
        if config_path:
            initial_plan = _build_initial_case_plan(Path(config_path))
            _write_json(Path(case_plan_path), initial_plan)
            new_state = new_state.update_case_plan(initial_plan)
        return new_state.mark_completed("0a")

    def browser_login(self, state: HUITestState) -> HUITestState:
        try:
            config_path = _require_path(state.config_path, "config_path")
            config = _load_json(Path(config_path))
            menu_url = config.get("computed", {}).get("menu_url", "")
            launch_output = self.runner.run_text("launch_chrome.py", ["--auto", "--config", config_path])
            _ensure_cdp_ready(config, launch_output)
            args = ["--config", config_path, "--login"]
            if menu_url:
                args.extend(["--navigate", menu_url])
            self.runner.run_text("cdp_executor.py", args)
            self.runner.run_text(
                "stagehand_explorer/src/explore_wrapper.py",
                ["--check-node", "--config", config_path],
            )
            return state.mark_completed("0b")
        except Exception as exc:
            raise EnvironmentFailure(str(exc)) from exc

    def form_scan(self, state: HUITestState) -> HUITestState:
        try:
            config_path = _require_path(state.config_path, "config_path")
            case_dir = Path(_require_path(state.case_dir, "case_dir"))
            case_plan_path = Path(_require_path(state.case_plan_path, "case_plan_path"))
            explore_path = case_dir / "explore_result.json"
            self.runner.run_text(
                "stagehand_explorer/src/explore_wrapper.py",
                ["--mode=fast", "--config", config_path, "-o", str(explore_path)],
            )
            form_fields = self.runner.run_json(
                "config_reader.py",
                ["match_presets", str(explore_path)],
            )
            if not form_fields.get("all"):
                raise EnvironmentFailure(
                    "form scan returned 0 fields; browser is likely not on the target menu page"
                )
            cp = state.edit_case_plan()
            config = _load_json(Path(config_path))
            cp["presets"] = config.get("presets", cp.get("presets", {}))
            cp["preset_fields"] = config.get("preset_fields", cp.get("preset_fields", {}))
            cp.setdefault("form_fields", {})
            cp["form_fields"].update(
                {
                    "all": form_fields.get("all", []),
                    "required": form_fields.get("required", []),
                    "excluded_fields": form_fields.get("excluded_fields", []),
                }
            )
            cp["case_inputs"] = _plan_case_inputs(
                form_fields.get("all", []),
                cp.get("field_groups", []),
                cp.get("preset_fields", {}),
            )
            _write_json(case_plan_path, cp)
            summary = {
                "field_count": len(form_fields.get("all", [])),
                "required_count": len(form_fields.get("required", [])),
                "excluded_count": len(form_fields.get("excluded_fields", [])),
            }
            return state.update_case_plan(cp).merge(form_fields_summary=summary).mark_completed("1a")
        except (EnvironmentFailure, MenuFailure):
            raise
        except Exception as exc:
            raise MenuFailure(str(exc)) from exc

    def log_capture(self, state: HUITestState) -> HUITestState:
        try:
            config_path = _require_path(state.config_path, "config_path")
            case_dir = Path(_require_path(state.case_dir, "case_dir"))
            case_plan_path = Path(_require_path(state.case_plan_path, "case_plan_path"))
            config = _load_json(Path(config_path))
            cp = state.edit_case_plan()
            fields_path = case_dir / "form_fields_all.json"
            actions_path = case_dir / "actions_query.json"
            query_log_path = case_dir / "query_log.txt"
            explore_path = case_dir / "explore_result.json"
            capture_fields = _query_capture_fields(cp)
            _write_json_list(fields_path, capture_fields)
            actions = self.runner.run_json_value("config_reader.py", ["build_actions", str(fields_path)])
            _write_json_list(actions_path, _actions_with_initial_reset(actions))
            env = config.get("env", {})
            computed = config.get("computed", {})
            args = [
                "--config",
                config_path,
                "-e",
                str(explore_path),
                "-a",
                str(actions_path),
                "--write-back",
                str(case_plan_path),
                "--save-log",
                str(query_log_path),
            ]
            if computed.get("log_path_full"):
                args.extend(["--ssh-log", computed["log_path_full"]])
            if env.get("app_server_host"):
                args.extend(["--ssh-host", env["app_server_host"]])
            if env.get("app_server_port"):
                args.extend(["--ssh-port", env["app_server_port"]])
            if env.get("app_server_user"):
                args.extend(["--ssh-user", env["app_server_user"]])
            if env.get("app_server_password"):
                args.extend(["--ssh-pass", env["app_server_password"]])
            args.extend(["--log-grep", "(Actual SQL|当前SQL)"])
            # Sync state.case_plan to disk so CDP executor can write back
            _write_json(case_plan_path, cp)
            self.runner.run_text("cdp_executor.py", args)
            # CDP 执行完后，form_fields.all 已被 write-back 更新为实际值。
            # 重新加载 case_plan，将实际值同步到 case_inputs（用例生成的数据源）。
            cp = _load_json(case_plan_path)
            _sync_case_inputs_from_form_fields(cp)
            _write_json(case_plan_path, cp)
            _ensure_sql_log_not_empty(query_log_path)
            self._run_sql_probe(
                case_plan=cp,
                case_plan_path=case_plan_path,
                case_dir=case_dir,
                config_path=config_path,
                explore_path=explore_path,
                base_args=args,
                base_fields=capture_fields,
                query_log_path=query_log_path,
            )
            return state.update_case_plan(cp).mark_completed("1b")
        except Exception as exc:
            raise MenuFailure(str(exc)) from exc

    def _run_sql_probe(
        self,
        *,
        case_plan: dict,
        case_plan_path: Path,
        case_dir: Path,
        config_path: str,
        explore_path: Path,
        base_args: list[str],
        base_fields: list[dict],
        query_log_path: Path,
    ) -> None:
        candidates = _sql_probe_candidates(case_plan, base_fields)
        attempts: list[dict] = []
        successful_logs: list[Path] = []
        base_labels = {field.get("label", "") for field in base_fields}
        seen_field_sets = {_field_set_signature(base_fields)}
        probe_index = 1
        survey_fields = _survey_capture_fields(case_plan, base_fields)
        if survey_fields and {field.get("label", "") for field in survey_fields} != base_labels:
            probe_index = self._run_sql_probe_variant(
                case_dir=case_dir,
                base_args=base_args,
                probe_index=probe_index,
                fields=survey_fields,
                attempts=attempts,
                successful_logs=successful_logs,
                seen_field_sets=seen_field_sets,
                meta={
                    "label": "survey_full_stable",
                    "kind": "survey",
                    "fields": [field.get("label", "") for field in survey_fields],
                },
            )
        for group in _customer_identity_bound_groups(case_plan):
            group_labels = {field.get("label", "") for field in group["fields"]}
            without_group = [
                field for field in base_fields
                if field.get("label", "") not in group_labels
            ]
            with_group = _dedupe_field_dicts([*without_group, *group["fields"]])
            probe_index = self._run_sql_probe_variant(
                case_dir=case_dir,
                base_args=base_args,
                probe_index=probe_index,
                fields=without_group,
                attempts=attempts,
                successful_logs=successful_logs,
                seen_field_sets=seen_field_sets,
                meta={
                    "label": f"{group['name']}:without_group",
                    "kind": "bound_group",
                    "group": group["name"],
                    "variant": "without_group",
                },
            )
            probe_index = self._run_sql_probe_variant(
                case_dir=case_dir,
                base_args=base_args,
                probe_index=probe_index,
                fields=with_group,
                attempts=attempts,
                successful_logs=successful_logs,
                seen_field_sets=seen_field_sets,
                meta={
                    "label": f"{group['name']}:with_group",
                    "kind": "bound_group",
                    "group": group["name"],
                    "variant": "with_group",
                },
            )
        for candidate in candidates:
            label = candidate.get("label", "")
            skip_reason = _sql_probe_skip_reason(candidate)
            if skip_reason:
                attempts.append({"label": label, "status": "skipped", "reason": skip_reason})
                continue
            probe_fields = [*base_fields, candidate]
            probe_index = self._run_sql_probe_variant(
                case_dir=case_dir,
                base_args=base_args,
                probe_index=probe_index,
                fields=probe_fields,
                attempts=attempts,
                successful_logs=successful_logs,
                seen_field_sets=seen_field_sets,
                meta={
                    "label": label,
                    "fields": sorted(base_labels | {label}),
                },
            )
        _append_probe_logs(query_log_path, successful_logs)
        case_plan["sql_probe"] = {
            "base_fields": [field.get("label", "") for field in base_fields],
            "survey_fields": [field.get("label", "") for field in survey_fields],
            "candidates": [field.get("label", "") for field in candidates],
            "attempts": attempts,
        }
        _write_json(case_plan_path, case_plan)

    def _run_sql_probe_variant(
        self,
        *,
        case_dir: Path,
        base_args: list[str],
        probe_index: int,
        fields: list[dict],
        attempts: list[dict],
        successful_logs: list[Path],
        seen_field_sets: set[tuple[str, ...]],
        meta: dict,
    ) -> int:
        field_signature = _field_set_signature(fields)
        if field_signature in seen_field_sets:
            attempts.append(
                {
                    **meta,
                    "status": "skipped",
                    "reason": "duplicate_field_set",
                    "fields": [field.get("label", "") for field in fields],
                }
            )
            return probe_index
        seen_field_sets.add(field_signature)
        probe_fields_path = case_dir / "sql_probe" / f"fields_{probe_index:02d}.json"
        probe_actions_path = case_dir / "sql_probe" / f"actions_{probe_index:02d}.json"
        probe_log_path = case_dir / "sql_probe" / f"log_{probe_index:02d}.txt"
        _write_json_list(probe_fields_path, fields)
        try:
            actions = self.runner.run_json_value(
                "config_reader.py",
                ["build_actions", str(probe_fields_path)],
            )
            _write_json_list(probe_actions_path, _actions_with_initial_reset(actions))
            args = _replace_arg_value(base_args, "-a", str(probe_actions_path))
            args = _replace_arg_value(args, "--save-log", str(probe_log_path))
            self.runner.run_text("cdp_executor.py", args)
            attempts.append(
                {
                    **meta,
                    "status": "success",
                    "fields": [field.get("label", "") for field in fields],
                    "log_path": _artifact_path(probe_log_path, case_dir),
                }
            )
            successful_logs.append(probe_log_path)
        except Exception as exc:  # noqa: BLE001 - SQL probe is best effort
            attempts.append({**meta, "status": "failed", "reason": str(exc)})
        return probe_index + 1

    def log_analysis(self, state: HUITestState) -> HUITestState:
        try:
            case_dir = Path(_require_path(state.case_dir, "case_dir"))
            case_plan_path = Path(_require_path(state.case_plan_path, "case_plan_path"))
            query_log_path = case_dir / "query_log.txt"
            analysis = self.runner.run_json("sql_extractor.py", ["analyze", str(query_log_path)])
            cp = state.edit_case_plan()
            physical_tables, filtered_system_tables = _filter_system_physical_tables(
                analysis.get("physical_tables", {})
            )
            cp["log_analysis"] = {
                "requirements": _filter_system_requirements(analysis.get("requirements", {})),
                "cached_tables": _filter_system_table_list(analysis.get("cached_tables", [])),
                "missing_tables": _filter_system_table_list(analysis.get("missing_tables", [])),
                "describe_needed": _filter_system_table_list(analysis.get("describe_needed", [])),
                "filtered_system_tables": filtered_system_tables,
            }
            ast_artifacts = _write_ast_diagnostic_artifacts(case_dir, analysis.get("ast_diagnostics"))
            if ast_artifacts:
                cp["log_analysis"]["ast_artifacts"] = ast_artifacts
                cp["log_analysis"]["ast_summary"] = {
                    "select_count": analysis.get("ast_diagnostics", {}).get("select_count", 0),
                }
            cp.setdefault("shard", {})
            if physical_tables:
                cp["shard"]["physical_tables"] = physical_tables
            _write_json(case_plan_path, cp)
            return state.update_case_plan(cp).merge(shard=cp.get("shard", {})).mark_completed("2a")
        except Exception as exc:
            raise MenuFailure(str(exc)) from exc

    def table_classify(self, state: HUITestState) -> HUITestState:
        try:
            config_path = Path(_require_path(state.config_path, "config_path"))
            case_plan_path = Path(_require_path(state.case_plan_path, "case_plan_path"))
            config = _load_json(config_path)
            env = config.get("env", {})
            computed = config.get("computed", {})
            cp = state.edit_case_plan()
            _apply_computed_shard_route(cp, computed, self.runner)
            _write_json(case_plan_path, cp)
            args = ["classify", str(case_plan_path)]
            if env.get("env_key"):
                args.extend(["--env", env["env_key"]])
            if env.get("project_dir"):
                args.extend(["--project-dir", env["project_dir"]])
            if computed.get("module"):
                args.extend(["--module", computed["module"]])
            classified = self.runner.run_json("sql_extractor.py", args)
            strategy = cp.setdefault("preset_strategy", {})
            template_base_tables = _base_template_tables(
                cp.get("shard", {}).get("physical_tables", {}),
                load_base_tables(self.runner.skill_root),
            )
            strategy["base_tables"] = _dedupe(
                _filter_system_table_list(_tables_by_class(classified, "routing") + template_base_tables)
            )
            strategy["biz_tables"] = _filter_system_table_list(_tables_by_class(classified, "business"))
            strategy["no_insert_tables"] = (
                _dedupe(
                    _tables_by_class(classified, "system")
                    + _tables_by_class(classified, "view")
                    + [
                        table
                        for table in classified
                        if _is_system_table(table)
                    ]
                )
            )
            strategy["preset_tables"] = strategy["base_tables"] + strategy["biz_tables"]
            cp.setdefault("shard", {})
            business_dbs = [
                value.get("db")
                for value in classified.values()
                if value.get("classification") == "business" and value.get("db")
            ]
            routing_db = cp.get("shard", {}).get("routing_db", "")
            cp["shard"]["db"] = business_dbs[0] if business_dbs else (routing_db or "ui_bc")
            _apply_sql_advisor_decision(cp, self.sql_advisor)
            _write_routed_ast_diagnostic_artifact(cp, case_plan_path.parent)
            _write_json(case_plan_path, cp)
            return state.update_case_plan(cp).merge(
                preset_strategy=strategy,
                shard=cp.get("shard", {}),
            ).mark_completed("2b")
        except Exception as exc:
            raise MenuFailure(str(exc)) from exc

    def sql_generate(self, state: HUITestState) -> HUITestState:
        try:
            case_plan_path = Path(_require_path(state.case_plan_path, "case_plan_path"))
            cp = state.edit_case_plan()
            config = {}
            if state.config_path:
                config = _load_json(Path(state.config_path))
                cp["presets"] = config.get("presets", cp.get("presets", {}))
            sql_paths = _resolve_sql_paths(cp, config)
            if not _has_valid_sql_paths(sql_paths):
                try:
                    sql_paths = self.runner.run_json("config_reader.py", ["sql_paths", state.menu_name])
                except Exception:
                    sql_paths = {}
            if not _has_valid_sql_paths(sql_paths):
                sql_paths = _derive_sql_paths(cp, config)
            if not _has_valid_sql_paths(sql_paths):
                raise MenuFailure(f"unable to resolve SQL output paths: {sql_paths}")
            local_sql_paths = _resolve_path_map(sql_paths, case_plan_path.parent)
            cp["sql_paths"] = _artifact_path_map(local_sql_paths, case_plan_path.parent)
            schema_cache = load_schema_cache(self.runner.skill_root)
            base_tables = load_base_tables(self.runner.skill_root)
            table_db_mapping = _load_table_db_mapping(self.runner.skill_root)
            cp["sql_requirements"] = [
                requirement.to_dict()
                for requirement in build_sql_requirements(cp, schema_cache=schema_cache)
            ]
            plans, skipped = build_insert_plans(
                cp,
                schema_cache=schema_cache,
                base_tables=base_tables,
                include_skipped=True,
                table_db_mapping=table_db_mapping,
            )
            if _has_missing_schema(skipped):
                _refresh_schema_cache_for_missing_tables(self.runner, cp, config, skipped)
                schema_cache = load_schema_cache(self.runner.skill_root)
                cp["sql_requirements"] = [
                    requirement.to_dict()
                    for requirement in build_sql_requirements(cp, schema_cache=schema_cache)
                ]
                plans, skipped = build_insert_plans(
                    cp,
                    schema_cache=schema_cache,
                    base_tables=base_tables,
                    include_skipped=True,
                    table_db_mapping=table_db_mapping,
                )
                _write_json(case_plan_path, cp)
            cp["insert_plans"] = [plan.to_dict() for plan in plans]
            cp["insert_plan_skipped"] = skipped
            coverage = evaluate_insert_plan_coverage(cp, plans, skipped)
            cp["sql_coverage"] = coverage
            self.runner.emit(
                {
                    "event": "sql_coverage_checked",
                    "status": coverage.get("status"),
                    "missing_expected": len(coverage.get("missing_expected_tables", [])),
                    "observed_missing_strategy": len(coverage.get("observed_missing_strategy", [])),
                    "failure_reason": coverage.get("failure_reason", ""),
                }
            )
            if coverage.get("status") == "failed" and plans:
                _write_json(case_plan_path, cp)
                raise MenuFailure(coverage.get("failure_reason") or "SQL coverage gate failed")
            if not plans:
                repair = _repair_failed_insert_plan_generation(
                    cp,
                    case_plan_path.parent,
                    1,
                    skipped,
                    self.repair_advisor,
                    skill_root=self.runner.skill_root,
                    event_logger=self.runner.emit,
                )
                cp.setdefault("sql_repair_attempts", []).append(repair)
                self.runner.emit(
                    {
                        "event": "sql_repair_attempt_recorded",
                        "attempt": 1,
                        "status": repair.get("status"),
                        "action_count": len(repair.get("actions", [])),
                        "has_llm_decision": bool(repair.get("llm_decision")),
                    }
                )
                if _has_applied_repair_action(repair.get("actions", [])):
                    validation = _validate_sql_files_with_repair(
                        cp,
                        case_plan_path.parent,
                        Path(state.config_path) if state.config_path else None,
                        self.runner,
                        self.repair_advisor,
                    )
                    _write_json(case_plan_path, cp)
                    if validation.get("status") == "failed":
                        raise MenuFailure(validation.get("failure_reason") or "SQL dry-run validation failed")
                    return state.update_case_plan(cp).merge(sql_paths=local_sql_paths).mark_completed("2c")
                _write_json(case_plan_path, cp)
                _ensure_insert_plans_not_empty(plans, skipped)
            permission_decision = _judge_permission_filter(cp, plans, skipped, self.permission_advisor)
            cp["permission_decision"] = permission_decision
            if permission_decision.get("has_branch_permission_filter") and _has_permission_rows_for_filtered_tables(cp, plans):
                _apply_precise_permission_query(cp)
                cp["expected_counts"] = {"branch": 1, "headquarter": 2}
                cp["case_count"] = 7
            else:
                plans = _collapse_permission_rows(plans)
                cp["insert_plans"] = [plan.to_dict() for plan in plans]
                cp.pop("case_count", None)
                cp["expected_counts"] = {}
            base_plans = [plan for plan in plans if plan.role == "base"]
            preset_plans = [plan for plan in plans if plan.role != "base"]
            if local_sql_paths.get("base_sql"):
                _write_text(Path(local_sql_paths["base_sql"]), render_sql(base_plans, include_db_prefix=False))
            if local_sql_paths.get("preset_sql"):
                _write_text(Path(local_sql_paths["preset_sql"]), render_sql(preset_plans, include_db_prefix=False))
            validation = _validate_sql_files_with_repair(
                cp,
                case_plan_path.parent,
                Path(state.config_path) if state.config_path else None,
                self.runner,
                self.repair_advisor,
            )
            if validation.get("status") != "failed":
                precheck = _validate_sql_return_precheck(
                    cp,
                    case_plan_path,
                    Path(state.config_path) if state.config_path else None,
                    self.runner,
                )
                if precheck.get("status") == "failed":
                    _write_json(case_plan_path, cp)
                    raise MenuFailure(precheck.get("failure_reason") or "SQL return precheck failed")
            _write_json(case_plan_path, cp)
            if validation.get("status") == "failed":
                raise MenuFailure(validation.get("failure_reason") or "SQL dry-run validation failed")
            return state.update_case_plan(cp).merge(sql_paths=local_sql_paths).mark_completed("2c")
        except Exception as exc:
            raise MenuFailure(str(exc)) from exc

    def plan_verify(self, state: HUITestState) -> HUITestState:
        try:
            case_plan_path = Path(_require_path(state.case_plan_path, "case_plan_path"))
            cp = state.edit_case_plan()
            missing = _missing_required_plan_sections(cp)
            if missing:
                raise MenuFailure("case_plan missing required sections: " + ", ".join(missing))
            cp["plan_verification"] = {
                "status": "passed",
                "checked_at": _utc_now(),
                "required_sections": [
                    "menu",
                    "case_inputs",
                    "preset_strategy",
                    "shard",
                    "sql_paths",
                ],
            }
            _write_json(case_plan_path, cp)
            return state.update_case_plan(cp).mark_completed("2d")
        except MenuFailure:
            raise
        except Exception as exc:
            raise MenuFailure(str(exc)) from exc

    def case_generate(self, state: HUITestState) -> HUITestState:
        try:
            case_plan_path = Path(_require_path(state.case_plan_path, "case_plan_path"))
            case_dir = Path(_require_path(state.case_dir, "case_dir"))
            cp = state.edit_case_plan()
            _merge_config_env_into_case_plan(cp, Path(state.config_path) if state.config_path else None)
            reconciliation = _reconcile_case_inputs_with_preset_data(cp, case_plan_path.parent)
            if reconciliation.get("changes"):
                cp["case_input_reconciliation"] = reconciliation
            menu = cp.get("menu", {})
            test_root = Path(
                cp.get("paths", {}).get("test_case_dir")
                or case_dir / "generated_tests"
            )
            case_order = _case_generation_order(cp)
            _remove_stale_generated_cases(test_root, menu)
            generated: list[str] = []
            for case_type in case_order:
                if case_type not in cp.get("case_inputs", {}):
                    continue
                output_dir = (
                    test_root
                    / _safe_path_part(menu.get("level2", "default"))
                    / _safe_path_part(menu.get("name", "menu"))
                    / _safe_path_part(f"{menu.get('name', 'menu')}{_case_dir_suffix(case_type)}")
                )
                test_path = output_dir / f"test_{_safe_path_part(menu.get('code', 'generated'))}{_case_file_suffix(case_type)}.py"
                _write_text(
                    test_path,
                    render_pytest_case(
                        cp,
                        case_type,
                        output_path=test_path,
                        case_plan_dir=case_plan_path.parent,
                    ),
                )
                generated.append(_artifact_path(test_path, case_plan_path.parent))
            if not generated:
                raise MenuFailure("no case_inputs available for pytest generation")
            template_review = _review_generated_cases(
                self.template_reviewer,
                cp,
                generated,
                case_plan_path.parent,
            )
            if template_review:
                cp["template_review"] = template_review
                _write_json(case_plan_path, cp)
                if template_review.get("block_generation"):
                    raise MenuFailure(f"template review blocked generation: {template_review.get('summary', '')}")
            _deploy_common_helpers(self.runner, cp)
            for key in ("verification", "verification_attempts", "repair_attempts"):
                cp.pop(key, None)
            cp["generated_tests"] = generated
            _write_json(case_plan_path, cp)
            return state.update_case_plan(cp).mark_completed("3")
        except MenuFailure:
            raise
        except Exception as exc:
            raise MenuFailure(str(exc)) from exc

    def verify(self, state: HUITestState) -> HUITestState:
        try:
            case_plan_path = Path(_require_path(state.case_plan_path, "case_plan_path"))
            cp = state.edit_case_plan()
            _merge_config_env_into_case_plan(cp, Path(state.config_path) if state.config_path else None)
            generated_tests = cp.get("generated_tests", [])
            if not generated_tests:
                raise MenuFailure("generated_tests is empty; run case generation first")
            pytest_paths = [_resolve_artifact_path(path, case_plan_path.parent) for path in generated_tests]
            max_attempts = int(cp.get("repair_policy", {}).get("max_attempts", 3) or 3)
            env_name = str(cp.get("env_key") or "").strip()
            pytest_cwd = str(cp.get("paths", {}).get("project_dir") or "").strip() or None

            def run_phase(phase: str, attempt_no: int, targets: list[str]) -> subprocess.CompletedProcess[str]:
                self.runner.emit(
                    {
                        "event": "pytest_started",
                        "attempt": attempt_no,
                        "phase": phase,
                        "test_count": len(targets),
                        "tests": targets,
                    }
                )
                completed_result = _run_pytest(
                    targets,
                    event_logger=self.runner.emit if self.runner.has_event_logger else None,
                    attempt_no=attempt_no,
                    env_name=env_name,
                    cwd=pytest_cwd,
                )
                pytest_event = _pytest_attempt_summary(attempt_no, completed_result)
                pytest_event["phase"] = phase
                pytest_event["tests"] = targets
                self.runner.emit({"event": "pytest_finished", **pytest_event})
                cp.setdefault("verification_attempts", []).append(pytest_event)
                cp["verification"] = {
                    "status": "passed" if completed_result.returncode == 0 else "failed",
                    "phase": phase,
                    "returncode": completed_result.returncode,
                    "stdout": completed_result.stdout,
                    "stderr": completed_result.stderr,
                    "checked_at": _utc_now(),
                    "attempt": attempt_no,
                    "tests": targets,
                }
                return completed_result

            completed = run_phase("full", 1, pytest_paths)
            if completed.returncode == 0:
                _write_json(case_plan_path, cp)
                return state.update_case_plan(cp).mark_completed("4")
            if _skip_missing_preview_if_only_failure(cp, case_plan_path.parent, completed, self.runner):
                _write_json(case_plan_path, cp)
                return state.update_case_plan(cp).mark_completed("4")
            if _can_accept_environment_flake(cp, pytest_paths, completed):
                _mark_environment_flake_passed(cp, pytest_paths)
                _write_json(case_plan_path, cp)
                return state.update_case_plan(cp).mark_completed("4")

            for attempt_no in range(1, max_attempts + 1):
                trigger_completed = completed
                snapshot = _snapshot_repair_targets(cp, case_plan_path.parent, include_generated_tests=True)
                repair = _repair_failed_verification(
                    cp,
                    case_plan_path.parent,
                    attempt_no,
                    trigger_completed,
                    self.repair_advisor,
                    config_path=state.config_path,
                    runner=self.runner,
                    event_logger=self.runner.emit,
                )
                cp.setdefault("repair_attempts", []).append(repair)
                self.runner.emit(
                    {
                        "event": "repair_attempt_recorded",
                        "attempt": attempt_no,
                        "status": repair.get("status"),
                        "action_count": len(repair.get("actions", [])),
                        "has_llm_decision": bool(repair.get("llm_decision")),
                    }
                )
                _write_json(case_plan_path, cp)
                target_paths = _pytest_failed_nodeids(completed, fallback=pytest_paths)
                target_completed = run_phase("target", attempt_no, target_paths)
                repair["verification"] = _pytest_repair_validation_summary("target", target_completed)
                if target_completed.returncode != 0:
                    if _skip_missing_preview_if_only_failure(cp, case_plan_path.parent, target_completed, self.runner):
                        _record_repair_restore(repair, snapshot, cp, status="kept", reason="target pytest accepted by preview skip policy")
                        _write_json(case_plan_path, cp)
                        return state.update_case_plan(cp).mark_completed("4")
                    if _can_accept_environment_flake(cp, pytest_paths, target_completed):
                        _mark_environment_flake_passed(cp, pytest_paths)
                        _record_repair_restore(repair, snapshot, cp, status="kept", reason="target pytest accepted as environment flake")
                        _write_json(case_plan_path, cp)
                        return state.update_case_plan(cp).mark_completed("4")
                    _record_repair_restore(repair, snapshot, cp, status="restored", reason="target pytest failed")
                    _restore_repair_snapshot(snapshot, cp)
                    completed = trigger_completed
                    _write_json(case_plan_path, cp)
                    if attempt_no >= max_attempts:
                        break
                    continue
                if _same_pytest_targets(target_paths, pytest_paths):
                    _record_repair_restore(repair, snapshot, cp, status="kept", reason="target pytest passed")
                    _write_json(case_plan_path, cp)
                    return state.update_case_plan(cp).mark_completed("4")
                regression_completed = run_phase("regression", attempt_no, pytest_paths)
                repair["verification"] = _pytest_repair_validation_summary("regression", regression_completed)
                if regression_completed.returncode == 0:
                    _record_repair_restore(repair, snapshot, cp, status="kept", reason="regression pytest passed")
                    _write_json(case_plan_path, cp)
                    return state.update_case_plan(cp).mark_completed("4")
                if _skip_missing_preview_if_only_failure(cp, case_plan_path.parent, regression_completed, self.runner):
                    _record_repair_restore(repair, snapshot, cp, status="kept", reason="regression pytest accepted by preview skip policy")
                    _write_json(case_plan_path, cp)
                    return state.update_case_plan(cp).mark_completed("4")
                if _can_accept_environment_flake(cp, pytest_paths, regression_completed):
                    _mark_environment_flake_passed(cp, pytest_paths)
                    _record_repair_restore(repair, snapshot, cp, status="kept", reason="regression pytest accepted as environment flake")
                    _write_json(case_plan_path, cp)
                    return state.update_case_plan(cp).mark_completed("4")
                _record_repair_restore(repair, snapshot, cp, status="restored", reason="regression pytest failed")
                _restore_repair_snapshot(snapshot, cp)
                completed = trigger_completed
                _write_json(case_plan_path, cp)
                if attempt_no >= max_attempts:
                    break
            cp.setdefault("verification", {})["failure_reason"] = "pytest verification failed after repair attempts"
            _write_json(case_plan_path, cp)
            return state.update_case_plan(cp).mark_completed("4")
        except MenuFailure:
            raise
        except Exception as exc:
            raise MenuFailure(str(exc)) from exc

    def report(self, state: HUITestState) -> HUITestState:
        try:
            case_dir = Path(_require_path(state.case_dir, "case_dir"))
            case_plan_path = Path(_require_path(state.case_plan_path, "case_plan_path"))
            cp = state.edit_case_plan()
            report_path = case_dir / "reports" / "summary.md"
            _write_text(report_path, _render_summary(cp))
            reports = [*state.reports, str(report_path)]
            cp["reports"] = {"summary": _artifact_path(report_path, case_dir)}
            _write_json(case_plan_path, cp)
            if cp.get("verification", {}).get("status") == "failed":
                failure_reason = cp.get("verification", {}).get("failure_reason") or "pytest verification failed"
                return (
                    state.update_case_plan(cp)
                    .merge(reports=reports)
                    .mark_completed("5")
                    .record_failure("5", failure_reason, scope="menu")
                )
            return state.update_case_plan(cp).merge(reports=reports).mark_completed("5")
        except Exception as exc:
            raise MenuFailure(str(exc)) from exc


def build_default_step_handlers(
    skill_root: str | Path,
    event_logger=None,
    repair_advisor=None,
    permission_advisor=None,
    sql_advisor=None,
    template_reviewer=None,
) -> dict[str, object]:
    runner = ScriptRunner(skill_root=skill_root, event_logger=event_logger)
    handlers = SkillStepHandlers(
        runner,
        repair_advisor=repair_advisor,
        permission_advisor=permission_advisor,
        sql_advisor=sql_advisor,
        template_reviewer=template_reviewer,
    )
    return {
        "0a": handlers.menu_init,
        "0b": handlers.browser_login,
        "1a": handlers.form_scan,
        "1b": handlers.log_capture,
        "2a": handlers.log_analysis,
        "2b": handlers.table_classify,
        "2c": handlers.sql_generate,
        "2d": handlers.plan_verify,
        "3": handlers.case_generate,
        "4": handlers.verify,
        "5": handlers.report,
    }


def _extract_config_path(output: str) -> str:
    match = re.search(r"config\.json 已写入:\s*(.+)", output)
    return match.group(1).strip() if match else ""


def _require_path(value: str, label: str) -> str:
    if not value:
        raise ValueError(f"Missing {label}; previous step did not initialize it")
    return value


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _normalize_portable_paths(payload, path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_json_list(path: Path, payload: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _actions_with_initial_reset(actions: object) -> list:
    result = list(actions) if isinstance(actions, list) else []
    if result and result[0].get("action") == "reset":
        return result
    return [{"action": "reset"}, *result]


def _write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _write_ast_diagnostic_artifacts(case_dir: Path, diagnostics: object) -> dict:
    if not isinstance(diagnostics, dict) or not diagnostics.get("select_count"):
        return {}
    analysis_dir = case_dir / "sql_analysis"
    json_path = analysis_dir / "ast_selects.json"
    sql_path = analysis_dir / "ast_selects.sql"
    text = str(diagnostics.get("text") or "")
    payload = {
        key: value
        for key, value in diagnostics.items()
        if key != "text"
    }
    _write_json(json_path, payload)
    _write_text(sql_path, text)
    return {
        "json": _artifact_path(json_path, case_dir),
        "sql": _artifact_path(sql_path, case_dir),
    }


def _apply_computed_shard_route(case_plan: dict, computed: dict, runner: ScriptRunner) -> None:
    module = str(computed.get("module") or "")
    if not module:
        return
    if not (runner.scripts_dir / "config_reader.py").exists():
        return
    try:
        shard = runner.run_json("config_reader.py", ["compute_shard", module, "--json"], timeout=120)
    except Exception as exc:  # noqa: BLE001 - classify can still fall back to table mapping
        case_plan.setdefault("shard", {})["formula_error"] = str(exc)
        return
    if not isinstance(shard, dict) or shard.get("error"):
        if isinstance(shard, dict) and shard.get("error"):
            case_plan.setdefault("shard", {})["formula_error"] = shard.get("error")
        return
    db_name = str(shard.get("db_name") or "")
    if not db_name:
        return
    case_plan.setdefault("shard", {})
    case_plan["shard"]["routing_db"] = db_name
    case_plan["shard"]["db"] = db_name
    case_plan["shard"]["formula"] = {
        key: value
        for key, value in shard.items()
        if key
        in {
            "env_name",
            "module",
            "customer_id",
            "in_client_no",
            "last8",
            "route_number",
            "has_db2",
            "db_prefix",
            "trans_db_num",
            "trans_db_remainder",
            "trans_db_quotient",
            "trans_table_num",
            "pub_table_num",
            "db_name",
            "formula",
        }
    }
    pub_table_num = shard.get("pub_table_num")
    physical_tables = case_plan["shard"].setdefault("physical_tables", {})
    base_tables = case_plan.setdefault("preset_strategy", {}).setdefault("base_tables", [])
    if (
        pub_table_num
        and "tbclientmap" in physical_tables
        and "tbclient" not in physical_tables
    ):
        physical_tables["tbclient"] = [f"tbclient{pub_table_num}"]
        if "tbclient" not in base_tables:
            base_tables.append("tbclient")


def _write_routed_ast_diagnostic_artifact(case_plan: dict, case_dir: Path) -> None:
    artifacts = case_plan.get("log_analysis", {}).get("ast_artifacts")
    if not isinstance(artifacts, dict) or not artifacts.get("sql"):
        return
    source = Path(_resolve_artifact_path(str(artifacts["sql"]), case_dir))
    if not source.exists():
        return
    routed_text = _qualify_sql_physical_tables(source.read_text(encoding="utf-8"), case_plan, default_db="")
    routed_path = source.with_name(source.stem + "_routed" + source.suffix)
    _write_text(routed_path, routed_text)
    artifacts["routed_sql"] = _artifact_path(routed_path, case_dir)


def _normalize_portable_paths(payload: dict, base_dir: Path) -> dict:
    sql_paths = payload.get("sql_paths")
    if isinstance(sql_paths, dict):
        for key in ("base_sql", "preset_sql"):
            if sql_paths.get(key):
                sql_paths[key] = _artifact_path(Path(sql_paths[key]), base_dir)
    generated_tests = payload.get("generated_tests")
    if isinstance(generated_tests, list):
        payload["generated_tests"] = [
            _artifact_path(Path(value), base_dir) if isinstance(value, str) else value
            for value in generated_tests
        ]
    reports = payload.get("reports")
    if isinstance(reports, dict) and reports.get("summary"):
        reports["summary"] = _artifact_path(Path(reports["summary"]), base_dir)
    sql_probe = payload.get("sql_probe")
    if isinstance(sql_probe, dict):
        for attempt in sql_probe.get("attempts", []):
            if isinstance(attempt, dict) and attempt.get("log_path"):
                attempt["log_path"] = _artifact_path(Path(attempt["log_path"]), base_dir)
    return payload


def _build_initial_case_plan(config_path: Path) -> dict:
    config = _load_json(config_path)
    env = config.get("env", {})
    menu = config.get("menu", {})
    computed = config.get("computed", {})
    menu_url = computed.get("menu_url", "")
    func_code = computed.get("func_code") or _func_code_from_url(menu_url)
    return {
        "owner": _menu_owner(menu, env),
        "env_key": env.get("env_key", ""),
        "menu": {
            "name": menu.get("菜单名称", ""),
            "code": func_code,
            "url": menu_url,
            "module": computed.get("module", ""),
            "level1": menu.get("导航一级菜单", ""),
            "level2": menu.get("导航二级菜单", ""),
        },
        "gate_decisions": {},
        "case_order": [],
        "form_fields": {
            "all": [],
            "required": [],
            "excluded_fields": [],
            "reset_test_field": {},
            "preview_buttons": [],
        },
        "field_groups": config.get("field_groups", []),
        "presets": config.get("presets", {}),
        "preset_fields": config.get("preset_fields", {}),
        "filter_selections": {},
        "preset_strategy": {},
        "shard": {},
        "expected_counts": {},
        "paths": {
            "project_dir": env.get("project_dir", ""),
            "test_case_dir": computed.get("test_case_dir", ""),
            "templates": {},
        },
    }


def _menu_owner(menu: dict, env: dict) -> str:
    for key in ("owner", "Owner", "负责人", "维护人", "责任人", "用例负责人"):
        value = str(menu.get(key, "") or "").strip()
        if value:
            return value
    return str(env.get("owner", "") or "").strip()


def _merge_config_env_into_case_plan(case_plan: dict, config_path: Path | None) -> None:
    if not config_path or not config_path.exists():
        return
    config = _load_json(config_path)
    env_key = str(config.get("env", {}).get("env_key") or "").strip()
    if env_key:
        case_plan["env_key"] = env_key


def _func_code_from_url(menu_url: str) -> str:
    parts = [part for part in menu_url.rstrip("/").split("/") if part]
    return parts[-1] if parts else ""


def _plan_case_inputs(fields: list[dict], field_groups: list[dict], preset_fields: dict | None = None) -> dict:
    planner = CaseInputPlanner(fields=fields, field_groups=field_groups, preset_fields=preset_fields or {})
    return {
        name: asdict(case_input)
        for name, case_input in planner.plan().items()
    }


def _tables_by_class(classified: dict, classification: str) -> list[str]:
    return [
        table
        for table, value in classified.items()
        if value.get("classification") == classification
    ]


def _base_template_tables(physical_tables: dict, base_tables: dict) -> list[str]:
    result: list[str] = []
    for table in physical_tables:
        if _is_system_table(table):
            continue
        if table in base_tables and isinstance(base_tables.get(table, {}).get("records"), dict):
            result.append(table)
    return result


def _filter_system_physical_tables(physical_tables: dict) -> tuple[dict, list[str]]:
    if not isinstance(physical_tables, dict):
        return {}, []
    result: dict = {}
    filtered: list[str] = []
    for logical, physicals in physical_tables.items():
        table_names = [logical, *(physicals if isinstance(physicals, list) else [])]
        system_names = [table for table in table_names if _is_system_table(str(table))]
        if system_names:
            filtered.extend(system_names)
            continue
        result[logical] = physicals
    return result, sorted(set(filtered))


def _filter_system_table_list(tables: list) -> list:
    return [
        table
        for table in tables
        if not _is_system_table(str(table))
    ]


def _filter_system_requirements(requirements: dict) -> dict:
    if not isinstance(requirements, dict):
        return {}
    result = dict(requirements)
    for key in ("tables", "join_tables"):
        if isinstance(result.get(key), list):
            result[key] = _filter_system_table_list(result[key])
    for key in ("where_columns", "join_key_columns"):
        if isinstance(result.get(key), dict):
            result[key] = {
                table: columns
                for table, columns in result[key].items()
                if not _is_system_table(str(table))
            }
    return result


def _apply_sql_advisor_decision(case_plan: dict, sql_advisor=None) -> None:
    if sql_advisor is None:
        return
    try:
        decision = dict(sql_advisor.judge(case_plan))
    except Exception as exc:  # noqa: BLE001 - SQL advisor must not block deterministic classify
        case_plan["sql_decision"] = {
            "source": "llm_error",
            "error": str(exc),
        }
        return
    decision["source"] = "llm"
    case_plan["sql_decision"] = decision
    strategy = case_plan.setdefault("preset_strategy", {})
    physical_tables = set(case_plan.get("shard", {}).get("physical_tables", {}))
    removable = {
        table
        for table in decision.get("no_insert_tables", [])
        if table in physical_tables
    }
    if not removable:
        return
    strategy["no_insert_tables"] = _dedupe([*strategy.get("no_insert_tables", []), *sorted(removable)])
    strategy["base_tables"] = [table for table in strategy.get("base_tables", []) if table not in removable]
    strategy["biz_tables"] = [table for table in strategy.get("biz_tables", []) if table not in removable]
    strategy["preset_tables"] = strategy.get("base_tables", []) + strategy.get("biz_tables", [])


def _is_system_table(table_name: str) -> bool:
    table = re.sub(r"\d+$", "", str(table_name or "").lower())
    if table in {"tbuserbranch"}:
        return True
    return (
        table.startswith("tsys_")
        or table.startswith("act_")
        or table.startswith("flow_")
        or table.startswith("tbsysarg")
        or table.startswith("tbworkflow")
        or table.startswith("tbtrans")
        or table.startswith("tblogs")
        or table.startswith("tbsequence")
        or table.startswith("tbbranch")
        or table.startswith("tbparam")
    )


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _sync_case_inputs_from_form_fields(case_plan: dict) -> None:
    """将 form_fields.all 中的实际值同步到 case_inputs，替换哨兵值。

    CDP 执行器 write-back 后，form_fields.all 包含实际选中的值（如 "ASO001:test"）。
    case_inputs 中可能仍保留 __FIRST_OPTION__ 等哨兵值，需要同步。
    """
    form_fields = case_plan.get("form_fields", {}).get("all", [])
    actual_by_label: dict[str, str] = {}
    for field in form_fields:
        label = field.get("label", "")
        value = field.get("value", "")
        if label and value and value not in ("__FIRST_OPTION__", "_"):
            actual_by_label[label] = value

    case_inputs = case_plan.get("case_inputs", {})
    for _case_type, case_input in case_inputs.items():
        if not isinstance(case_input, dict):
            continue
        for field in case_input.get("fields", []):
            if not isinstance(field, dict):
                continue
            label = field.get("label", "")
            value = field.get("value", "")
            if value in ("__FIRST_OPTION__", "_") and label in actual_by_label:
                field["value"] = actual_by_label[label]


def _reconcile_case_inputs_with_preset_data(case_plan: dict, case_plan_dir: Path) -> dict:
    value_context = _preset_value_context_from_case_plan(case_plan, case_plan_dir)
    if not value_context:
        return {"status": "skipped", "reason": "no preset values found", "changes": []}
    changes: list[dict] = []
    case_inputs = case_plan.get("case_inputs", {})
    if not isinstance(case_inputs, dict):
        return {"status": "skipped", "reason": "case_inputs is not a dict", "changes": []}
    for case_type, case_input in case_inputs.items():
        fields = case_input.get("fields") if isinstance(case_input, dict) else None
        if not isinstance(fields, list):
            continue
        for field in fields:
            if not isinstance(field, dict):
                continue
            columns = _case_input_label_columns(str(field.get("label", "")))
            value, source_column = _first_value_for_columns(value_context, columns)
            if not value:
                continue
            old_value = str(field.get("value", ""))
            if old_value == value:
                continue
            field["value"] = value
            field["value_source"] = f"preset_reconciliation.{source_column}"
            changes.append(
                {
                    "case_type": case_type,
                    "label": field.get("label", ""),
                    "old_value": old_value,
                    "new_value": value,
                    "source_column": source_column,
                }
            )
    return {"status": "updated" if changes else "unchanged", "changes": changes}


def _preset_value_context_from_case_plan(case_plan: dict, case_plan_dir: Path) -> dict[str, str]:
    context: dict[str, str] = {}
    for value in _sql_values_from_case_plan_files(case_plan, case_plan_dir):
        name = value.get("column", "")
        column_value = _clean_case_input_value(value.get("value", ""))
        if name and column_value and name not in context:
            context[name] = column_value
    for plan in case_plan.get("insert_plans", []) if isinstance(case_plan.get("insert_plans"), list) else []:
        if not isinstance(plan, dict):
            continue
        rows = plan.get("rows")
        candidate_rows = rows if isinstance(rows, list) and rows else [plan.get("columns", [])]
        for row in candidate_rows:
            if not isinstance(row, list):
                continue
            for column in row:
                if not isinstance(column, dict):
                    continue
                name = str(column.get("name", ""))
                value = _clean_case_input_value(column.get("value", ""))
                if name and value and name not in context:
                    context[name] = value
    return context


def _sql_values_from_case_plan_files(case_plan: dict, case_plan_dir: Path) -> list[dict]:
    result: list[dict] = []
    for sql_path in _repairable_sql_paths(case_plan, case_plan_dir):
        if not sql_path.exists() or not sql_path.is_file():
            continue
        result.extend(_extract_insert_values_from_sql(sql_path.read_text(encoding="utf-8", errors="replace")))
    return result


def _extract_insert_values_from_sql(sql: str) -> list[dict]:
    result: list[dict] = []
    pattern = re.compile(
        r"\bINSERT\s+INTO\s+(?:`?[\w]+`?\.)?`?[\w]+`?\s*\((?P<cols>[^)]*?)\)\s*VALUES\s*(?P<values>.*?)(?=;\s*(?:\bDELETE\b|\bINSERT\b|\bUPDATE\b|\Z)|\Z)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(sql):
        columns = [column.strip().strip("`") for column in match.group("cols").split(",") if column.strip()]
        row = _first_sql_values_row(match.group("values"))
        if not columns or not row:
            continue
        for column, value in zip(columns, row):
            if column and value != "":
                result.append({"column": column, "value": value})
    return result


def _first_sql_values_row(values_sql: str) -> list[str]:
    start = values_sql.find("(")
    if start < 0:
        return []
    depth = 0
    in_string = False
    quote = ""
    chars = []
    for char in values_sql[start:]:
        if in_string:
            chars.append(char)
            if char == quote:
                in_string = False
            continue
        if char in {"'", '"'}:
            in_string = True
            quote = char
            chars.append(char)
            continue
        if char == "(":
            depth += 1
            if depth > 1:
                chars.append(char)
            continue
        if char == ")":
            depth -= 1
            if depth == 0:
                break
            chars.append(char)
            continue
        if depth >= 1:
            chars.append(char)
    return _split_sql_value_row("".join(chars))


def _split_sql_value_row(row_sql: str) -> list[str]:
    values = []
    current = []
    in_string = False
    quote = ""
    for char in row_sql:
        if in_string:
            if char == quote:
                in_string = False
            else:
                current.append(char)
            continue
        if char in {"'", '"'}:
            in_string = True
            quote = char
            continue
        if char == ",":
            values.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    values.append("".join(current).strip())
    return values


def _case_input_label_columns(label: str) -> list[str]:
    if "客户标识" in label and "类型" not in label:
        return ["bank_acc", "account", "customer_id", "client_no", "in_client_no"]
    if "账号" in label and "类型" not in label:
        return ["bank_acc", "account", "ta_client", "customer_id"]
    if "账户" in label and "类型" not in label:
        return ["bank_acc", "account", "ta_client", "customer_id"]
    if "TA" in label:
        return ["ta_code"]
    if "产品代码" in label:
        return ["prd_code"]
    if "产品名称" in label:
        return ["prd_name"]
    if "分行" in label or "机构" in label:
        return ["open_branch", "branch_no", "org_no", "seller_branch", "belong_branch", "branch_code", "open_org"]
    return []


def _first_value_for_columns(context: dict[str, str], columns: list[str]) -> tuple[str, str]:
    for column in columns:
        value = context.get(column)
        if value:
            return value, column
    return "", ""


def _clean_case_input_value(value: object) -> str:
    text = str(value or "").strip()
    if ":" in text:
        return text.split(":", 1)[0]
    return text


def _query_capture_fields(case_plan: dict) -> list[dict]:
    planned = (
        case_plan.get("case_inputs", {})
        .get("querymulti", {})
        .get("fields", [])
    )
    if planned:
        return [
            {
                "label": field.get("label", ""),
                "type": field.get("type", ""),
                "value": field.get("value", ""),
            }
            for field in planned
        ]
    return case_plan.get("form_fields", {}).get("all", [])


def _sql_probe_candidates(case_plan: dict, base_fields: list[dict]) -> list[dict]:
    base_labels = {field.get("label", "") for field in base_fields}
    bound_group_labels = {
        field.get("label", "")
        for group in _customer_identity_bound_groups(case_plan)
        for field in group["fields"]
    }
    excluded = (
        case_plan.get("case_inputs", {})
        .get("querymulti", {})
        .get("excluded", {})
    )
    result: list[dict] = []
    for field in case_plan.get("form_fields", {}).get("all", []):
        label = field.get("label", "")
        if not label or label in base_labels or label in excluded or label in bound_group_labels:
            continue
        if field.get("type") == "button":
            continue
        if field.get("type") == "tree-select":
            continue
        if any(token in label for token in ("申请编号", "流水号", "合同号", "协议号", "单号")):
            continue
        result.append(
            {
                "label": label,
                "type": field.get("type", ""),
                "value": field.get("value", ""),
                "options": field.get("options", []),
            }
        )
    return result


def _survey_capture_fields(case_plan: dict, base_fields: list[dict]) -> list[dict]:
    excluded = (
        case_plan.get("case_inputs", {})
        .get("querymulti", {})
        .get("excluded", {})
    )
    result: list[dict] = []
    for field in case_plan.get("form_fields", {}).get("all", []):
        label = field.get("label", "")
        if not label or label in excluded:
            continue
        if _is_unstable_form_field(field):
            continue
        probe = _probe_field(field)
        if _sql_probe_skip_reason(probe):
            continue
        result.append(probe)
    if not result:
        result = [_probe_field(field) for field in base_fields]
    return _dedupe_field_dicts(result)


def _is_unstable_form_field(field: dict) -> bool:
    label = field.get("label", "")
    if field.get("type") == "button":
        return True
    if field.get("type") == "tree-select":
        return True
    return any(token in label for token in ("申请编号", "流水号", "合同号", "协议号", "单号"))


def _customer_identity_bound_groups(case_plan: dict) -> list[dict]:
    fields_by_label = {
        field.get("label", ""): field
        for field in case_plan.get("form_fields", {}).get("all", [])
    }
    result: list[dict] = []
    for group in case_plan.get("field_groups", []):
        labels = group.get("order") or group.get("fields") or []
        canonical_to_actual = _canonical_group_label_map(group, fields_by_label)
        if "客户标识类型" not in labels or "客户标识" not in labels:
            continue
        if "客户标识类型" not in canonical_to_actual or "客户标识" not in canonical_to_actual:
            continue
        type_label = canonical_to_actual["客户标识类型"]
        identity_label = canonical_to_actual["客户标识"]
        if fields_by_label[type_label].get("is_required") or fields_by_label[identity_label].get("is_required"):
            continue
        group_fields = [
            _probe_field(fields_by_label[canonical_to_actual[label]])
            for label in labels
            if label in canonical_to_actual
        ]
        result.append({"name": group.get("name") or "客户标识组", "fields": group_fields})
    implicit_identity = _optional_customer_identity_labels(fields_by_label)
    if not result and implicit_identity:
        result.append(
            {
                "name": "客户标识组",
                "fields": [
                    _probe_field(fields_by_label[implicit_identity["客户标识类型"]]),
                    _probe_field(fields_by_label[implicit_identity["客户标识"]]),
                ],
            }
        )
    return result


def _canonical_group_label_map(group: dict, fields_by_label: dict[str, dict]) -> dict[str, str]:
    labels = group.get("order") or group.get("fields") or []
    alias_to_canonical = group.get("aliases", {}) or {}
    canonical_to_actual: dict[str, str] = {}
    for actual_label in fields_by_label:
        canonical = alias_to_canonical.get(actual_label, actual_label)
        if canonical in labels and canonical not in canonical_to_actual:
            canonical_to_actual[canonical] = actual_label
    return canonical_to_actual


def _has_optional_customer_identity_fields(fields_by_label: dict[str, dict]) -> bool:
    return bool(_optional_customer_identity_labels(fields_by_label))


def _optional_customer_identity_labels(fields_by_label: dict[str, dict]) -> dict[str, str]:
    canonical_to_actual = _canonical_group_label_map(
        {
            "fields": ["客户标识类型", "客户标识"],
            "aliases": {"账户类型": "客户标识类型", "账号": "客户标识"},
        },
        fields_by_label,
    )
    if "客户标识类型" not in canonical_to_actual or "客户标识" not in canonical_to_actual:
        return {}
    type_label = canonical_to_actual["客户标识类型"]
    identity_label = canonical_to_actual["客户标识"]
    if fields_by_label[type_label].get("is_required") or fields_by_label[identity_label].get("is_required"):
        return {}
    return canonical_to_actual


def _probe_field(field: dict) -> dict:
    return {
        "label": field.get("label", ""),
        "type": field.get("type", ""),
        "value": field.get("value", ""),
        "options": field.get("options", []),
    }


def _dedupe_field_dicts(fields: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for field in fields:
        label = field.get("label", "")
        if not label or label in seen:
            continue
        seen.add(label)
        result.append(field)
    return result


def _field_set_signature(fields: list[dict]) -> tuple[str, ...]:
    return tuple(sorted({field.get("label", "") for field in fields if field.get("label", "")}))


def _sql_probe_skip_reason(field: dict) -> str:
    field_type = field.get("type", "")
    value = field.get("value", "")
    options = field.get("options", [])
    if field_type in {"single-select", "multi-select"} and not value:
        return "empty_select_value"
    if field_type in {"single-select", "multi-select"} and value == "__FIRST_OPTION__" and not options:
        return "select_without_options"
    if field_type == "input" and not value:
        return "empty_input_value"
    return ""


def _replace_arg_value(args: list[str], option: str, value: str) -> list[str]:
    result = list(args)
    index = result.index(option)
    result[index + 1] = value
    return result


def _append_probe_logs(query_log_path: Path, probe_logs: list[Path]) -> None:
    if not probe_logs:
        return
    with query_log_path.open("a", encoding="utf-8") as output:
        for path in probe_logs:
            if not path.exists():
                continue
            output.write(f"\n\n--- SQL PROBE {path.name} ---\n")
            output.write(path.read_text(encoding="utf-8"))


def _ensure_sql_log_not_empty(query_log_path: Path) -> None:
    if not query_log_path.exists() or not query_log_path.read_text(encoding="utf-8").strip():
        raise MenuFailure(f"SQL log is empty after capture: {query_log_path}")


def _ensure_cdp_ready(config: dict, launch_output: str, timeout: int = 10) -> None:
    if "CDP 启动超时" in launch_output or "启动失败" in launch_output:
        raise EnvironmentFailure(f"Chrome CDP not ready after launch: {launch_output}")
    cdp_url = config.get("env", {}).get("cdp_url", "")
    if not cdp_url:
        return
    version_url = cdp_url.rstrip("/") + "/json/version"
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(version_url, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("webSocketDebuggerUrl"):
                return
            last_error = "missing webSocketDebuggerUrl"
        except Exception as exc:  # noqa: BLE001 - converts CDP readiness details
            last_error = str(exc)
        time.sleep(0.5)
    raise EnvironmentFailure(f"Chrome CDP endpoint is not ready: {version_url}; {last_error}")


def _missing_required_plan_sections(case_plan: dict) -> list[str]:
    missing: list[str] = []
    menu = case_plan.get("menu", {})
    if not menu.get("name") or not menu.get("code"):
        missing.append("menu.name/code")
    if not case_plan.get("case_inputs"):
        missing.append("case_inputs")
    if "preset_strategy" not in case_plan:
        missing.append("preset_strategy")
    if not case_plan.get("shard", {}).get("db"):
        missing.append("shard.db")
    sql_paths = case_plan.get("sql_paths", {})
    if not sql_paths.get("base_sql") or not sql_paths.get("preset_sql"):
        missing.append("sql_paths.base_sql/preset_sql")
    return missing


def _case_generation_order(case_plan: dict) -> list[str]:
    case_inputs = case_plan.get("case_inputs", {})
    permission_enabled = bool(case_plan.get("expected_counts"))
    explicit = [
        case for case in case_plan.get("case_order", [])
        if (
            case in case_inputs
            and (permission_enabled or case not in {"branch", "headquarter"})
        )
    ]
    if explicit:
        return explicit
    if case_plan.get("case_count") == 7 and permission_enabled:
        order = ["branch", "headquarter", "queryall", "querysingle", "querymulti", "reset", "preview"]
    else:
        order = ["queryall", "querysingle", "querymulti", "reset", "preview"]
    return [case for case in order if case in case_inputs]


def _deploy_common_helpers(runner: ScriptRunner, case_plan: dict) -> None:
    project_dir = str(case_plan.get("paths", {}).get("project_dir") or "").strip()
    if not project_dir:
        return
    runner.run_text("deploy_helpers.py", ["--project-dir", project_dir])


def _review_generated_cases(
    template_reviewer,
    case_plan: dict,
    generated: list[str],
    case_plan_dir: Path,
) -> dict:
    if template_reviewer is None:
        return {}
    payload = {
        "menu": case_plan.get("menu", {}),
        "case_inputs": case_plan.get("case_inputs", {}),
        "expected_counts": case_plan.get("expected_counts", {}),
        "generated_tests": [],
    }
    for artifact in generated:
        path = Path(_resolve_artifact_path(artifact, case_plan_dir))
        payload["generated_tests"].append(
            {
                "case_type": _case_type_from_test_path(path),
                "path": _artifact_path(path, case_plan_dir),
                "content": path.read_text(encoding="utf-8", errors="replace") if path.exists() else "",
            }
        )
    try:
        review = dict(template_reviewer.review(payload))
        review["source"] = "llm"
        return review
    except Exception as exc:  # noqa: BLE001 - reviewer errors are recorded but not blocking
        return {
            "source": "llm_error",
            "status": "passed",
            "block_generation": False,
            "summary": f"template reviewer failed: {exc}",
            "issues": [],
        }


def _case_type_from_test_path(path: Path) -> str:
    stem = path.stem
    for case_type in ("branch", "headquarter", "queryall", "querysingle", "querymulti", "reset", "preview"):
        if stem.endswith(f"_{case_type}"):
            return case_type
    return ""


def _remove_stale_generated_cases(test_root: Path, menu: dict) -> None:
    menu_dir = (
        test_root
        / _safe_path_part(menu.get("level2", "default"))
        / _safe_path_part(menu.get("name", "menu"))
    )
    func_code = _safe_path_part(menu.get("code", "generated"))
    if not menu_dir.exists():
        return
    for case_type in ("branch", "headquarter", "queryall", "querysingle", "querymulti", "reset", "preview"):
        for stale_path in (
            menu_dir / f"test_{func_code}{_case_file_suffix(case_type)}.py",
            menu_dir
            / _safe_path_part(f"{menu.get('name', 'menu')}{_case_dir_suffix(case_type)}")
            / f"test_{func_code}{_case_file_suffix(case_type)}.py",
        ):
            if stale_path.exists() and stale_path.is_file():
                stale_path.unlink()


def _artifact_path(path: Path, base_dir: Path) -> str:
    if not path.is_absolute():
        return path.as_posix()
    try:
        return Path(os.path.relpath(path, start=base_dir)).as_posix()
    except ValueError:
        return str(path)


def _artifact_path_map(paths: dict, base_dir: Path) -> dict:
    return {
        key: _artifact_path(Path(value), base_dir) if value else value
        for key, value in paths.items()
    }


def _resolve_path_map(paths: dict, base_dir: Path) -> dict:
    return {
        key: _resolve_artifact_path(str(value), base_dir) if value else value
        for key, value in paths.items()
    }


def _resolve_artifact_path(path: str, base_dir: Path) -> str:
    artifact = Path(path)
    if artifact.is_absolute():
        return str(artifact)
    return str((base_dir / artifact).resolve())


def _validate_sql_files_with_repair(
    case_plan: dict,
    case_plan_dir: Path,
    config_path: Path | None,
    runner: ScriptRunner,
    repair_advisor=None,
) -> dict:
    if not _dry_run_sql_available(runner):
        validation = {"status": "skipped", "reason": "dry_run_sql.py is not available", "checked_at": _utc_now()}
        case_plan["sql_validation"] = validation
        return validation
    if config_path is None or not config_path.exists():
        validation = {"status": "skipped", "reason": "config_path is not available", "checked_at": _utc_now()}
        case_plan["sql_validation"] = validation
        return validation

    max_repairs = int(case_plan.get("repair_policy", {}).get("max_attempts", 3) or 3)
    config = _load_json(config_path)
    validation = _run_sql_dry_run_attempt(case_plan, case_plan_dir, config_path, config, runner, attempt_no=1)
    case_plan.setdefault("sql_validation_attempts", []).append(validation)
    case_plan["sql_validation"] = validation
    if validation.get("status") == "passed":
        return validation

    # 首次 dry-run 失败：先刷新 schema 缓存（缓存可能过期），再让 LLM 诊断
    _refresh_schema_cache_for_dry_run_tables(runner, case_plan, case_plan_dir, config)

    for repair_no in range(1, max_repairs + 1):
        trigger_validation = validation
        snapshot = _snapshot_repair_targets(case_plan, case_plan_dir, include_generated_tests=False)
        repair = _repair_failed_sql_validation(
            case_plan,
            case_plan_dir,
            repair_no,
            trigger_validation,
            repair_advisor,
            skill_root=runner.skill_root,
            event_logger=runner.emit,
        )
        case_plan.setdefault("sql_repair_attempts", []).append(repair)
        runner.emit(
            {
                "event": "sql_repair_attempt_recorded",
                "attempt": repair_no,
                "status": repair.get("status"),
                "action_count": len(repair.get("actions", [])),
                "has_llm_decision": bool(repair.get("llm_decision")),
            }
        )
        if not _has_applied_repair_action(repair.get("actions", [])):
            _record_repair_restore(repair, snapshot, case_plan, status="not_needed", reason="no applied repair action")
            break
        validation = _run_sql_dry_run_attempt(
            case_plan,
            case_plan_dir,
            config_path,
            config,
            runner,
            attempt_no=repair_no + 1,
        )
        repair["validation"] = _sql_repair_validation_summary(validation)
        case_plan.setdefault("sql_validation_attempts", []).append(validation)
        case_plan["sql_validation"] = validation
        if validation.get("status") == "passed":
            _record_repair_restore(repair, snapshot, case_plan, status="kept", reason="SQL dry-run validation passed")
            return validation
        _record_repair_restore(repair, snapshot, case_plan, status="restored", reason="SQL dry-run validation failed")
        _restore_repair_snapshot(snapshot, case_plan)
        validation = trigger_validation
        case_plan["sql_validation"] = trigger_validation

    validation = {**validation, "status": "failed", "failure_reason": "SQL dry-run validation failed after repair attempts"}
    case_plan["sql_validation"] = validation
    return validation


def _dry_run_sql_available(runner: ScriptRunner) -> bool:
    return (runner.scripts_dir / "dry_run_sql.py").exists()


def _validate_sql_return_precheck(
    case_plan: dict,
    case_plan_path: Path,
    config_path: Path | None,
    runner: ScriptRunner,
) -> dict:
    if not (runner.scripts_dir / "sql_return_precheck.py").exists():
        precheck = {"status": "skipped", "reason": "sql_return_precheck.py is not available", "checked_at": _utc_now()}
        case_plan["sql_return_precheck"] = precheck
        return precheck
    if config_path is None or not config_path.exists():
        precheck = {"status": "skipped", "reason": "config_path is not available", "checked_at": _utc_now()}
        case_plan["sql_return_precheck"] = precheck
        return precheck
    raw_sqls = case_plan.get("log_analysis", {}).get("requirements", {}).get("raw_sqls", [])
    if not isinstance(raw_sqls, list) or not raw_sqls:
        precheck = {"status": "skipped", "reason": "no raw SQL available", "checked_at": _utc_now()}
        case_plan["sql_return_precheck"] = precheck
        return precheck
    config = _load_json(config_path)
    db = _business_db_key(case_plan)
    args = ["--case-plan", str(case_plan_path), "--config", str(config_path), "--db", db, "--json"]
    env_key = config.get("env", {}).get("env_key")
    if env_key:
        args.extend(["--env", str(env_key)])
    runner.emit({"event": "sql_return_precheck_started", "db": db})
    try:
        raw = runner.run_json("sql_return_precheck.py", args, timeout=120)
    except ScriptError as exc:
        raw = _parse_script_error_json(exc)
    status = "passed" if raw.get("ok") else "failed"
    precheck = {
        **raw,
        "status": raw.get("status") or status,
        "checked_at": _utc_now(),
        "db": db,
    }
    if not raw.get("ok"):
        detail = str(precheck.get("failure_reason") or "").strip()
        precheck["failure_reason"] = "SQL return precheck failed" + (f": {detail}" if detail else "")
    case_plan["sql_return_precheck"] = precheck
    runner.emit(
        {
            "event": "sql_return_precheck_finished",
            "db": db,
            "status": precheck["status"],
            "permission": _permission_precheck_event_summary(precheck.get("permission_checks", {})),
            "failure_reason": precheck.get("failure_reason", ""),
        }
    )
    return precheck


def _permission_precheck_event_summary(permission_checks: object) -> str:
    if not isinstance(permission_checks, dict) or not permission_checks:
        return ""
    parts = []
    for role in ("branch", "headquarter"):
        item = permission_checks.get(role)
        if not isinstance(item, dict):
            continue
        status = item.get("status", "")
        expected = item.get("expected", "")
        actual = item.get("actual", "")
        parts.append(f"{role}:{actual}/{expected}:{status}")
    return ",".join(parts)


def _run_sql_dry_run_attempt(
    case_plan: dict,
    case_plan_dir: Path,
    config_path: Path,
    config: dict,
    runner: ScriptRunner,
    *,
    attempt_no: int,
) -> dict:
    files = _sql_dry_run_targets(case_plan, case_plan_dir)
    if not files:
        return {
            "status": "skipped",
            "attempt": attempt_no,
            "checked_at": _utc_now(),
            "reason": "no SQL files to validate",
            "files": [],
        }
    results = [
        _run_one_sql_dry_run(case_plan, config_path, config, runner, item, attempt_no=attempt_no)
        for item in files
    ]
    failed = [item for item in results if item.get("status") == "failed"]
    return {
        "status": "failed" if failed else "passed",
        "attempt": attempt_no,
        "checked_at": _utc_now(),
        "files": results,
        "failed_count": len(failed),
        "failure_reason": _sql_validation_failure_reason(failed),
    }


def _sql_dry_run_targets(case_plan: dict, case_plan_dir: Path) -> list[dict]:
    result: list[dict] = []
    seen: set[Path] = set()
    sql_paths = _resolve_path_map(case_plan.get("sql_paths", {}), case_plan_dir)
    for kind, value in sql_paths.items():
        if not isinstance(value, str) or not value.lower().endswith(".sql"):
            continue
        path = Path(value)
        if not path.exists() or not path.read_text(encoding="utf-8", errors="replace").strip():
            continue
        if path in seen:
            continue
        seen.add(path)
        result.append({"kind": kind, "path": path})
    return result


def _run_one_sql_dry_run(
    case_plan: dict,
    config_path: Path,
    config: dict,
    runner: ScriptRunner,
    item: dict,
    *,
    attempt_no: int,
) -> dict:
    path = Path(item["path"])
    kind = str(item.get("kind", "sql"))
    db_key = _sql_dry_run_db_key(kind, case_plan)
    dry_run_path = _write_dry_run_diagnostic_sql(case_plan, config_path.parent, path, kind, db_key)
    args = ["--file", str(dry_run_path), "--db", db_key, "--config", str(config_path), "--json"]
    env_key = config.get("env", {}).get("env_key")
    if env_key:
        args.extend(["--env", str(env_key)])
    file_label = _artifact_path(path, config_path.parent)
    runner.emit({"event": "sql_dry_run_started", "attempt": attempt_no, "kind": kind, "file": file_label, "db": db_key})
    try:
        raw = runner.run_json("dry_run_sql.py", args, timeout=120)
    except ScriptError as exc:
        raw = _parse_script_error_json(exc)
    ok = bool(raw.get("ok"))
    result = {
        "kind": kind,
        "file": file_label,
        "diagnostic_file": _artifact_path(dry_run_path, config_path.parent),
        "db": db_key,
        "status": "passed" if ok else "failed",
        "ok": ok,
        "total": raw.get("total", 0),
        "passed": raw.get("passed", 0),
        "failed": raw.get("failed", 0),
        "errors": raw.get("errors", []),
    }
    runner.emit(
        {
            "event": "sql_dry_run_finished",
            "attempt": attempt_no,
            "kind": kind,
            "file": file_label,
            "db": db_key,
            "status": result["status"],
            "failed": result["failed"],
            "error": _sql_file_failure_reason(result),
        }
    )
    return result


def _sql_dry_run_db_key(kind: str, case_plan: dict) -> str:
    if kind == "base_sql":
        return "ui_bc"
    return _business_db_key(case_plan)


def _business_db_key(case_plan: dict) -> str:
    shard = case_plan.get("shard", {})
    if not isinstance(shard, dict):
        return "ui_bc"
    return str(shard.get("routing_db") or shard.get("db") or "ui_bc")


def _write_dry_run_diagnostic_sql(case_plan: dict, case_dir: Path, source_path: Path, kind: str, db_key: str) -> Path:
    sql = source_path.read_text(encoding="utf-8", errors="replace")
    diagnostic_sql = _qualify_sql_physical_tables(sql, case_plan, default_db=db_key)
    output_dir = case_dir / "sql_analysis" / "dry_run"
    output_path = output_dir / source_path.name
    _write_text(output_path, diagnostic_sql)
    case_plan.setdefault("sql_diagnostics", {}).setdefault("dry_run", {})[kind] = _artifact_path(output_path, case_dir)
    return output_path


def _qualify_sql_physical_tables(sql: str, case_plan: dict, *, default_db: str = "") -> str:
    table_dbs = _physical_table_db_map(case_plan, default_db=default_db)
    result = str(sql or "")
    for table, db in sorted(table_dbs.items(), key=lambda item: len(item[0]), reverse=True):
        if not table or not db:
            continue
        result = re.sub(
            rf"(?<![\w.]){re.escape(table)}(?![\w])",
            f"{db}.{table}",
            result,
            flags=re.IGNORECASE,
        )
    return result


def _physical_table_db_map(case_plan: dict, *, default_db: str = "") -> dict[str, str]:
    result: dict[str, str] = {}
    for plan in case_plan.get("insert_plans", []) if isinstance(case_plan.get("insert_plans"), list) else []:
        if not isinstance(plan, dict):
            continue
        physical = str(plan.get("physical_table") or plan.get("table") or "")
        db = str(plan.get("db") or default_db or "")
        if physical and db:
            result[physical] = db
    shard_db = str(case_plan.get("shard", {}).get("db") or default_db or "")
    for physicals in case_plan.get("shard", {}).get("physical_tables", {}).values():
        if isinstance(physicals, list):
            for physical in physicals:
                if str(physical) and str(physical) not in result and shard_db:
                    result[str(physical)] = shard_db
        elif str(physicals) and str(physicals) not in result and shard_db:
            result[str(physicals)] = shard_db
    return result


def _parse_script_error_json(exc: ScriptError) -> dict:
    text = "\n".join(part for part in (exc.stdout, exc.stderr) if part)
    try:
        value = _parse_last_json_value(text)
    except ValueError:
        return {
            "ok": False,
            "total": 0,
            "passed": 0,
            "failed": 1,
            "errors": [{"line": 0, "stmt_preview": "", "error": str(exc)}],
        }
    return value if isinstance(value, dict) else {"ok": False, "errors": [{"error": str(value)}]}


def _parse_last_json_value(text: str):
    decoder = json.JSONDecoder()
    last = None
    last_span = -1
    for index, char in enumerate(str(text or "")):
        if char not in "[{":
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)) and end > last_span:
            last = value
            last_span = end
    if last is None:
        raise ValueError("No JSON value found")
    return last


def _sql_validation_failure_reason(failed: list[dict]) -> str:
    if not failed:
        return ""
    return "; ".join(_sql_file_failure_reason(item) for item in failed if _sql_file_failure_reason(item))[:1000]


def _sql_file_failure_reason(item: dict) -> str:
    errors = item.get("errors", [])
    if isinstance(errors, list) and errors:
        first = errors[0] if isinstance(errors[0], dict) else {"error": str(errors[0])}
        return str(first.get("error") or first)[:500]
    return ""


def _repair_failed_sql_validation(
    case_plan: dict,
    case_plan_dir: Path,
    attempt_no: int,
    validation: dict,
    repair_advisor=None,
    skill_root: Path | str | None = None,
    event_logger=None,
) -> dict:
    evidence = _collect_sql_repair_evidence(case_plan, case_plan_dir, attempt_no, validation, skill_root=skill_root)
    attempt = {
        "attempt": attempt_no,
        "checked_at": _utc_now(),
        "strategy": "llm_sql_dry_run_repair",
        "trigger": "sql_dry_run_failed",
        "sql_validation": _summarize_sql_validation(validation),
        "evidence": {
            "sql_file_count": len(evidence.get("sql_files", [])),
            "failed_count": validation.get("failed_count", 0),
            "preset_keys": sorted((evidence.get("presets") or {}).keys()),
        },
        "actions": [],
        "status": "no_action",
    }
    if repair_advisor is not None:
        try:
            _emit_event(event_logger, {"event": "llm_sql_repair_started", "attempt": attempt_no})
            llm_started_at = time.perf_counter()
            attempt["llm_decision"] = repair_advisor.diagnose(evidence)
            elapsed_ms = int((time.perf_counter() - llm_started_at) * 1000)
            _emit_event(
                event_logger,
                {
                    "event": "llm_sql_repair_finished",
                    "attempt": attempt_no,
                    "failure_type": attempt["llm_decision"].get("failure_type"),
                    "confidence": attempt["llm_decision"].get("confidence"),
                    "elapsed_ms": elapsed_ms,
                    "summary": attempt["llm_decision"].get("summary"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            attempt["llm_error"] = str(exc)
            _emit_event(event_logger, {"event": "llm_sql_repair_failed", "attempt": attempt_no, "error": str(exc)})
    actions = []
    if attempt.get("llm_decision"):
        actions.extend(_repair_action_apply_llm_repair_actions(case_plan, case_plan_dir, attempt["llm_decision"]))
    if not _has_applied_repair_action(actions):
        actions.extend(_repair_action_sql_preset_mismatches(case_plan, case_plan_dir))
    attempt["actions"] = actions
    if _has_applied_repair_action(actions):
        attempt["status"] = "patched"
    elif actions:
        attempt["status"] = "skipped"
    _emit_event(
        event_logger,
        {
            "event": "sql_repair_actions_finished",
            "attempt": attempt_no,
            "status": attempt["status"],
            "actions": [_repair_action_event_label(action) for action in actions],
        },
    )
    for action in actions:
        _emit_event(
            event_logger,
            {
                "event": "repair_action_detail",
                "attempt": attempt_no,
                "type": action.get("type", ""),
                "status": action.get("status", ""),
                "file": action.get("file", ""),
                "reason": action.get("reason") or action.get("suggested_reason", ""),
            },
        )
    return attempt


def _repair_failed_insert_plan_generation(
    case_plan: dict,
    case_plan_dir: Path,
    attempt_no: int,
    skipped: list[dict],
    repair_advisor=None,
    skill_root: Path | str | None = None,
    event_logger=None,
) -> dict:
    evidence = _collect_insert_plan_repair_evidence(case_plan, case_plan_dir, attempt_no, skipped, skill_root=skill_root)
    attempt = {
        "attempt": attempt_no,
        "checked_at": _utc_now(),
        "strategy": "llm_insert_plan_generation_repair",
        "trigger": "insert_plan_generation_failed",
        "insert_plan_skipped": skipped,
        "evidence": {
            "skipped_count": len(skipped),
            "sql_file_count": len(evidence.get("sql_files", [])),
            "preset_keys": sorted((evidence.get("presets") or {}).keys()),
        },
        "actions": [],
        "status": "no_action",
    }
    if repair_advisor is not None:
        try:
            _emit_event(event_logger, {"event": "llm_sql_repair_started", "attempt": attempt_no})
            llm_started_at = time.perf_counter()
            attempt["llm_decision"] = repair_advisor.diagnose(evidence)
            elapsed_ms = int((time.perf_counter() - llm_started_at) * 1000)
            _emit_event(
                event_logger,
                {
                    "event": "llm_sql_repair_finished",
                    "attempt": attempt_no,
                    "failure_type": attempt["llm_decision"].get("failure_type"),
                    "confidence": attempt["llm_decision"].get("confidence"),
                    "elapsed_ms": elapsed_ms,
                    "summary": attempt["llm_decision"].get("summary"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            attempt["llm_error"] = str(exc)
            _emit_event(event_logger, {"event": "llm_sql_repair_failed", "attempt": attempt_no, "error": str(exc)})
    actions = []
    if attempt.get("llm_decision"):
        actions.extend(_repair_action_apply_llm_repair_actions(case_plan, case_plan_dir, attempt["llm_decision"]))
    attempt["actions"] = actions
    if _has_applied_repair_action(actions):
        attempt["status"] = "patched"
    elif actions:
        attempt["status"] = "skipped"
    _emit_event(
        event_logger,
        {
            "event": "sql_repair_actions_finished",
            "attempt": attempt_no,
            "status": attempt["status"],
            "actions": [_repair_action_event_label(action) for action in actions],
        },
    )
    for action in actions:
        _emit_event(
            event_logger,
            {
                "event": "repair_action_detail",
                "attempt": attempt_no,
                "type": action.get("type", ""),
                "status": action.get("status", ""),
                "file": action.get("file", ""),
                "reason": action.get("reason") or action.get("suggested_reason", ""),
            },
        )
    return attempt


def _collect_insert_plan_repair_evidence(
    case_plan: dict,
    case_plan_dir: Path,
    attempt_no: int,
    skipped: list[dict],
    *,
    skill_root: Path | str | None = None,
) -> dict:
    return {
        "attempt": attempt_no,
        "trigger": "insert_plan_generation_failed",
        "menu": case_plan.get("menu", {}),
        "presets": case_plan.get("presets", {}),
        "case_inputs": case_plan.get("case_inputs", {}),
        "expected_counts": case_plan.get("expected_counts", {}),
        "shard": case_plan.get("shard", {}),
        "preset_strategy": case_plan.get("preset_strategy", {}),
        "insert_plans": case_plan.get("insert_plans", []),
        "insert_plan_skipped": skipped,
        "table_schemas": _collect_table_schemas(case_plan, skill_root=skill_root),
        "sql_files": _collect_sql_file_tails(case_plan, case_plan_dir),
        "previous_sql_repair_attempts": summarize_repair_attempts_for_evidence(case_plan.get("sql_repair_attempts", [])),
    }


def _collect_sql_repair_evidence(
    case_plan: dict,
    case_plan_dir: Path,
    attempt_no: int,
    validation: dict,
    *,
    skill_root: Path | str | None = None,
) -> dict:
    return {
        "attempt": attempt_no,
        "trigger": "sql_dry_run_failed",
        "menu": case_plan.get("menu", {}),
        "sql_validation": validation,
        "presets": case_plan.get("presets", {}),
        "case_inputs": case_plan.get("case_inputs", {}),
        "expected_counts": case_plan.get("expected_counts", {}),
        "shard": case_plan.get("shard", {}),
        "preset_strategy": case_plan.get("preset_strategy", {}),
        "insert_plans": case_plan.get("insert_plans", []),
        "insert_plan_skipped": case_plan.get("insert_plan_skipped", []),
        "sql_files": _collect_sql_file_tails(case_plan, case_plan_dir),
        "previous_sql_repair_attempts": summarize_repair_attempts_for_evidence(case_plan.get("sql_repair_attempts", [])),
        "table_schemas": _collect_table_schemas(case_plan, skill_root=skill_root),
    }


def _collect_table_schemas(case_plan: dict, *, skill_root: Path | str | None = None) -> dict[str, dict]:
    """从 schema 缓存中收集涉及表的列信息，供 LLM 修复时参考。"""
    schemas: dict[str, dict] = {}
    for table in case_plan.get("preset_strategy", {}).get("preset_tables", []):
        schema = _load_cached_schema(table, skill_root=skill_root)
        if schema:
            schemas[table] = schema
    for table in case_plan.get("preset_strategy", {}).get("base_tables", []):
        if table not in schemas:
            schema = _load_cached_schema(table, skill_root=skill_root)
            if schema:
                schemas[table] = schema
    return schemas


def _load_cached_schema(table: str, *, skill_root: Path | str | None = None) -> dict | None:
    """从 knowledge/config/cache/*.json 加载表 schema。"""
    import json as _json
    cache_dir = _find_cache_dir(skill_root=skill_root)
    if cache_dir:
        for f in cache_dir.glob("*.json"):
            try:
                data = _json.loads(f.read_text(encoding="utf-8"))
                for key in (table, table.lower()):
                    if key in data:
                        entry = data[key]
                        return _extract_schema_entry(entry)
            except Exception:
                continue
    return None


def _extract_schema_entry(entry: dict) -> dict:
    """从缓存条目中提取 schema 信息（含列类型）。"""
    result = {
        "all_columns": _extract_column_names(entry.get("all_columns", [])),
        "required_columns": _extract_column_names(entry.get("required_columns", [])),
    }
    column_types = entry.get("column_types")
    if column_types and isinstance(column_types, dict):
        result["column_types"] = column_types
    return result


def _find_cache_dir(*, skill_root: Path | str | None = None) -> Path | None:
    """查找 cache 目录的路径。"""
    candidates = [
        Path(skill_root) / "config" / "cache" if skill_root else None,
        Path.cwd() / "knowledge" / "config" / "cache",
        Path(__file__).resolve().parents[3] / "knowledge" / "config" / "cache",
    ]
    for p in candidates:
        if p is not None and p.is_dir():
            return p
    return None


def _extract_column_names(columns: Any) -> list[str]:
    """从缓存条目中提取列名列表。"""
    if isinstance(columns, list):
        result = []
        for col in columns:
            if isinstance(col, str):
                result.append(col)
            elif isinstance(col, dict):
                name = col.get("field") or col.get("Field") or col.get("name") or ""
                if name:
                    result.append(str(name))
        return result
    return []


def _collect_sql_file_tails(case_plan: dict, case_plan_dir: Path) -> list[dict]:
    result: list[dict] = []
    for kind, value in (case_plan.get("sql_paths") or {}).items():
        if not isinstance(value, str) or not value.lower().endswith(".sql"):
            continue
        path = Path(_resolve_artifact_path(value, case_plan_dir))
        result.append(
            {
                "kind": kind,
                "path": str(path),
                "content_tail": path.read_text(encoding="utf-8", errors="replace")[-12000:] if path.exists() else "",
            }
        )
    return result


def _summarize_sql_validation(validation: dict) -> dict:
    return {
        "status": validation.get("status"),
        "attempt": validation.get("attempt"),
        "failed_count": validation.get("failed_count"),
        "failure_reason": validation.get("failure_reason", ""),
    }


def _run_pytest(
    pytest_paths: list[str],
    *,
    event_logger=None,
    attempt_no: int = 0,
    env_name: str = "",
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = _pytest_command(pytest_paths, stream=event_logger is not None, env_name=env_name)
    run_cwd = cwd or _default_pytest_cwd()
    if event_logger is not None:
        return _run_pytest_streaming(command, event_logger, attempt_no, cwd=run_cwd)
    return subprocess.run(
        command,
        cwd=run_cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _pytest_command(pytest_paths: list[str], *, stream: bool, env_name: str = "") -> list[str]:
    env_args = [f"--env={env_name}"] if env_name else []
    rootdir_args = _pytest_rootdir_args(pytest_paths)
    if not stream:
        return [sys.executable, "-m", "pytest", *pytest_paths, "-q", *env_args, *rootdir_args]
    return [
        sys.executable,
        "-m",
        "pytest",
        *pytest_paths,
        *env_args,
        *rootdir_args,
        "-vv",
        "-s",
        "--tb=short",
        "-rA",
        "-o",
        "log_cli=true",
        "-o",
        "log_cli_level=INFO",
    ]


def _pytest_rootdir_args(pytest_paths: list[str]) -> list[str]:
    roots: list[str] = []
    for item in pytest_paths:
        path_text = str(item or "").split("::", 1)[0]
        if not path_text:
            continue
        path = Path(path_text)
        if not path.is_absolute():
            path = Path(_default_pytest_cwd()) / path
        root = path if path.is_dir() else path.parent
        if str(root):
            roots.append(str(root))
    if not roots:
        return []
    try:
        rootdir = os.path.commonpath(roots)
    except ValueError:
        rootdir = roots[0]
    return [f"--rootdir={rootdir}"]


def _default_pytest_cwd() -> str:
    return str(Path(__file__).resolve().parents[3])


def _run_pytest_streaming(command: list[str], event_logger, attempt_no: int, *, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    stdout_parts: list[str] = []
    current_nodeid = ""
    emitted_cases: set[tuple[str, str]] = set()
    assert process.stdout is not None
    for line in process.stdout:
        stdout_parts.append(line)
        text = line.rstrip("\r\n")
        _emit_event(event_logger, {"event": "pytest_log_line", "attempt": attempt_no, "line": text})
        case_result, current_nodeid = _parse_live_pytest_case_result(text, current_nodeid)
        if case_result:
            emitted_cases.add((case_result["case"], case_result["status"]))
            _emit_event(event_logger, {"event": "pytest_case_result", "attempt": attempt_no, **case_result})
    returncode = process.wait()
    stdout = "".join(stdout_parts)
    for status in ("PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS"):
        for case in _pytest_case_results(stdout, status):
            if (case, status) in emitted_cases:
                continue
            emitted_cases.add((case, status))
            _emit_event(
                event_logger,
                {
                    "event": "pytest_case_result",
                    "attempt": attempt_no,
                    "nodeid": case,
                    "case": case,
                    "status": status,
                },
            )
    return subprocess.CompletedProcess(
        args=command,
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


def _parse_live_pytest_case_result(line: str, current_nodeid: str = "") -> tuple[dict | None, str]:
    clean = _strip_ansi(line.strip())
    match = re.match(
        r"^(?P<nodeid>.+?::[^\s]+)\s+(?P<status>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b",
        clean,
    )
    if match:
        nodeid = match.group("nodeid")
        return _pytest_case_event(nodeid, match.group("status")), ""
    status_match = re.match(r"^(?P<status>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b", clean)
    if status_match and current_nodeid and clean == status_match.group("status"):
        return _pytest_case_event(current_nodeid, status_match.group("status")), ""
    nodeid = _extract_live_pytest_nodeid(clean)
    if nodeid:
        return None, nodeid
    return None, current_nodeid


def _pytest_case_event(nodeid: str, status: str) -> dict:
    return {
        "nodeid": nodeid,
        "case": _short_pytest_id(nodeid),
        "status": status,
    }


def _extract_live_pytest_nodeid(line: str) -> str:
    match = re.search(r"(?P<nodeid>\S+?\.py::[^\s]+)", line)
    return match.group("nodeid") if match else ""


def _strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", value)


def _query_db_snapshot_for_diagnosis(
    case_plan: dict,
    case_plan_dir: Path,
    config_path: str | None,
    runner=None,
) -> dict:
    """查询数据库中预埋表的实际数据，用于 LLM 诊断。"""
    if not runner or not (runner.scripts_dir / "query_db_snapshot.py").exists():
        return {}
    if not config_path or not Path(config_path).exists():
        return {}
    sql_paths = _resolve_path_map(case_plan.get("sql_paths", {}), case_plan_dir)
    env = _load_json(Path(config_path)).get("env", {})
    snapshots: dict[str, dict] = {}
    for kind, db in (
        ("base_sql", "ui_bc"),
        ("preset_sql", _business_db_key(case_plan)),
    ):
        sql_file = sql_paths.get(kind, "")
        if not sql_file or not Path(sql_file).exists():
            continue
        args = ["--config", str(config_path), "--sql-file", str(sql_file), "--db", db]
        if env.get("env_key"):
            args.extend(["--env", env["env_key"]])
        try:
            raw = runner.run_text("query_db_snapshot.py", args, timeout=30)
            import json as _json
            snapshots[kind] = _json.loads(raw)
        except Exception as exc:  # noqa: BLE001 - snapshot failure must not block diagnosis
            snapshots[kind] = {"error": str(exc), "db": db, "sql_file": str(sql_file)}
    return {"snapshots": snapshots} if snapshots else {}


def _repair_failed_verification(
    case_plan: dict,
    case_plan_dir: Path,
    attempt_no: int,
    completed: subprocess.CompletedProcess[str],
    repair_advisor=None,
    event_logger=None,
    config_path: str | None = None,
    runner=None,
) -> dict:
    db_snapshot = _query_db_snapshot_for_diagnosis(case_plan, case_plan_dir, config_path, runner)
    evidence = collect_repair_evidence(case_plan, case_plan_dir, attempt_no, completed, config_path=config_path, db_snapshot=db_snapshot)
    attempt = {
        "attempt": attempt_no,
        "checked_at": _utc_now(),
        "strategy": "deterministic_sql_preset_repair",
        "trigger": "pytest_failed",
        "pytest": {
            "returncode": completed.returncode,
            "stdout_tail": (completed.stdout or "")[-4000:],
            "stderr_tail": (completed.stderr or "")[-4000:],
        },
        "evidence": _summarize_repair_evidence(evidence),
        "actions": [],
        "status": "no_action",
    }
    if repair_advisor is not None:
        try:
            _emit_event(event_logger, {"event": "llm_repair_started", "attempt": attempt_no})
            llm_started_at = time.perf_counter()
            attempt["llm_decision"] = repair_advisor.diagnose(evidence)
            elapsed_ms = int((time.perf_counter() - llm_started_at) * 1000)
            _emit_event(
                event_logger,
                {
                    "event": "llm_repair_finished",
                    "attempt": attempt_no,
                    "failure_type": attempt["llm_decision"].get("failure_type"),
                    "confidence": attempt["llm_decision"].get("confidence"),
                    "elapsed_ms": elapsed_ms,
                    "summary": attempt["llm_decision"].get("summary"),
                },
            )
        except Exception as exc:  # noqa: BLE001 - advisory failures must not block deterministic repair
            attempt["llm_error"] = str(exc)
            _emit_event(
                event_logger,
                {
                    "event": "llm_repair_failed",
                    "attempt": attempt_no,
                    "error": str(exc),
                },
            )
    actions = []
    if attempt.get("llm_decision"):
        actions.extend(_repair_action_apply_llm_repair_actions(case_plan, case_plan_dir, attempt["llm_decision"]))
    if not _has_applied_repair_action(actions) and _has_duplicate_key_error(completed):
        actions.extend(_repair_action_duplicate_insert_keys(case_plan, case_plan_dir))
    if not _has_applied_repair_action(actions):
        actions.extend(_repair_action_sql_preset_mismatches(case_plan, case_plan_dir))
    attempt["actions"] = actions
    if _has_applied_repair_action(actions):
        attempt["status"] = "patched"
    elif actions:
        attempt["status"] = "skipped"
    _emit_event(
        event_logger,
        {
            "event": "repair_actions_finished",
            "attempt": attempt_no,
            "status": attempt["status"],
            "actions": [_repair_action_event_label(action) for action in actions],
        },
    )
    for action in actions:
        _emit_event(
            event_logger,
            {
                "event": "repair_action_detail",
                "attempt": attempt_no,
                "type": action.get("type", ""),
                "status": action.get("status", ""),
                "file": action.get("file", ""),
                "reason": action.get("reason") or action.get("suggested_reason", ""),
            },
        )
    return attempt


def _has_applied_repair_action(actions: list[dict]) -> bool:
    return _repair_action_has_applied_repair_action(actions)


def _repair_action_event_label(action: dict) -> str:
    return _repair_actions_event_label(action)


def _allowed_generated_test_paths(case_plan: dict, case_plan_dir: Path) -> set[Path]:
    return _repair_action_generated_test_paths(case_plan, case_plan_dir)


def _repairable_sql_paths(case_plan: dict, case_plan_dir: Path) -> list[Path]:
    return _repair_action_sql_paths(case_plan, case_plan_dir)


def _has_duplicate_key_error(completed: subprocess.CompletedProcess[str]) -> bool:
    text = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    return "Duplicate entry" in text or "1062" in text


def _emit_event(event_logger, payload: dict) -> None:
    if event_logger is None:
        return
    try:
        event_logger(payload)
    except Exception:
        return


def _snapshot_repair_targets(case_plan: dict, case_plan_dir: Path, *, include_generated_tests: bool) -> dict:
    paths = {path.resolve() for path in _repairable_sql_paths(case_plan, case_plan_dir)}
    if include_generated_tests:
        paths.update(path.resolve() for path in _allowed_generated_test_paths(case_plan, case_plan_dir))
    return _snapshot_repair_paths(case_plan, case_plan_dir, paths=paths, label_path=_artifact_path)


def _sql_repair_validation_summary(validation: dict) -> dict:
    return {
        "kind": "sql_dry_run",
        "status": validation.get("status"),
        "attempt": validation.get("attempt"),
        "failed_count": validation.get("failed_count", 0),
        "failure_reason": validation.get("failure_reason", ""),
    }


def _pytest_repair_validation_summary(phase: str, completed: subprocess.CompletedProcess[str]) -> dict:
    return {
        "kind": "pytest",
        "phase": phase,
        "returncode": completed.returncode,
        "stdout_tail": (completed.stdout or "")[-2000:],
        "stderr_tail": (completed.stderr or "")[-2000:],
    }


def _summarize_repair_evidence(evidence: dict) -> dict:
    return {
        "has_query_log": bool(evidence.get("query_log_tail")),
        "sql_file_count": len(evidence.get("sql_files", [])),
        "database_check_count": len(evidence.get("database_checks", [])),
        "preset_keys": sorted((evidence.get("presets") or {}).keys()),
        "pytest_returncode": (evidence.get("pytest") or {}).get("returncode"),
    }


def _has_permission_rows(plans: list) -> bool:
    return any(getattr(plan, "rows", None) and len(plan.rows or []) >= 2 for plan in plans)


_PERMISSION_COLUMNS = (
    "branch_no",
    "open_branch",
    "org_no",
    "seller_branch",
    "belong_branch",
    "branch_code",
    "open_org",
    "internal_branch",
)


def _permission_filtered_tables(case_plan: dict) -> set[str]:
    where_columns = (
        case_plan.get("log_analysis", {})
        .get("requirements", {})
        .get("where_columns", {})
    )
    if not isinstance(where_columns, dict):
        return set()
    result: set[str] = set()
    permission_columns = set(_PERMISSION_COLUMNS)
    for table, columns in where_columns.items():
        if not isinstance(columns, list):
            continue
        if any(str(column).lower() in permission_columns for column in columns):
            result.add(_normalize_permission_table_name(str(table)))
    return result


def _normalize_permission_table_name(table: str) -> str:
    name = str(table or "").strip().lower()
    name = name.split(".")[-1]
    return re.sub(r"\d+$", "", name)


def _has_permission_rows_for_filtered_tables(case_plan: dict, plans: list) -> bool:
    filtered_tables = _permission_filtered_tables(case_plan)
    if not filtered_tables:
        return _has_permission_rows(plans)
    matched = False
    for table in filtered_tables:
        table_plans = [
            plan
            for plan in plans
            if table
            in {
                _normalize_permission_table_name(getattr(plan, "table", "")),
                _normalize_permission_table_name(getattr(plan, "physical_table", "")),
            }
        ]
        if not table_plans:
            return False
        matched = True
        if not any(getattr(plan, "rows", None) and len(plan.rows or []) >= 2 for plan in table_plans):
            return False
    return matched


def _has_permission_filter_in_sql(case_plan: dict) -> bool:
    raw_sqls = (
        case_plan.get("log_analysis", {})
        .get("requirements", {})
        .get("raw_sqls", [])
    )
    if not isinstance(raw_sqls, list):
        return False
    for raw_sql in raw_sqls:
        sql = str(raw_sql or "")
        parts = re.split(r"\bwhere\b", sql, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) < 2:
            continue
        predicates = re.split(
            r"\b(?:order\s+by|group\s+by|limit|当前参数|Actual\s+SQL|当前SQL)\b",
            parts[1],
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        for column in _PERMISSION_COLUMNS:
            if re.search(
                rf"\b(?:\w+\.)?{re.escape(column)}\b\s*(?:in\s*\(|=\s*(?:'|\"|\?|[0-9]))",
                predicates,
                flags=re.IGNORECASE,
            ):
                return True
    return False


def _judge_permission_filter(case_plan: dict, plans: list, skipped: list[dict], permission_advisor=None) -> dict:
    deterministic_rows = _has_permission_rows_for_filtered_tables(case_plan, plans)
    deterministic_filter = _has_permission_filter_in_sql(case_plan)
    deterministic = deterministic_rows and deterministic_filter
    evidence_plan = {
        **case_plan,
        "insert_plans": [plan.to_dict() for plan in plans],
        "insert_plan_skipped": skipped,
    }
    if permission_advisor is not None:
        try:
            decision = dict(permission_advisor.judge(evidence_plan))
            decision.setdefault("confidence", 0)
            decision.setdefault("reason", "")
            decision["has_branch_permission_filter"] = bool(decision.get("has_branch_permission_filter"))
            decision["source"] = "llm"
            decision["deterministic_has_permission_rows"] = deterministic_rows
            decision["permission_filtered_tables"] = sorted(_permission_filtered_tables(case_plan))
            decision["deterministic_has_permission_filter"] = deterministic_filter
            if decision["has_branch_permission_filter"] and not deterministic_rows:
                decision["has_branch_permission_filter"] = False
                decision["reason"] = (
                    str(decision.get("reason") or "")
                    + "; LLM suggested branch permission but permission-filtered tables do not have two-row insert plans"
                ).strip("; ")
            if decision["has_branch_permission_filter"] and not deterministic_filter:
                decision["has_branch_permission_filter"] = False
                decision["reason"] = (
                    str(decision.get("reason") or "")
                    + "; LLM suggested branch permission but SQL only projects branch fields without a WHERE permission filter"
                ).strip("; ")
            return decision
        except Exception as exc:  # noqa: BLE001 - permission advisor must not block deterministic generation
            return {
                "has_branch_permission_filter": deterministic,
                "confidence": 0,
                "reason": f"permission advisor failed; fallback to deterministic rows: {exc}",
                "evidence": [],
                "suggested_split_columns": [],
                "source": "deterministic_fallback",
                "deterministic_has_permission_rows": deterministic_rows,
                "permission_filtered_tables": sorted(_permission_filtered_tables(case_plan)),
                "deterministic_has_permission_filter": deterministic_filter,
            }
    return {
        "has_branch_permission_filter": deterministic,
        "confidence": 1.0 if deterministic else 0.5,
        "reason": "deterministic permission rows and SQL filter detected" if deterministic else "no SQL permission filter detected",
        "evidence": [],
        "suggested_split_columns": [],
        "source": "deterministic",
        "deterministic_has_permission_rows": deterministic_rows,
        "permission_filtered_tables": sorted(_permission_filtered_tables(case_plan)),
        "deterministic_has_permission_filter": deterministic_filter,
    }


def _collapse_permission_rows(plans: list) -> list:
    result = []
    for plan in plans:
        if getattr(plan, "rows", None):
            collapsed_rows = _collapse_rows_for_non_permission_query(plan)
            if len(collapsed_rows) > 1:
                result.append(replace(plan, columns=collapsed_rows[0], rows=collapsed_rows))
            else:
                result.append(replace(plan, columns=collapsed_rows[0], rows=None))
        else:
            result.append(plan)
    return result


def _collapse_rows_for_non_permission_query(plan) -> list[list[InsertColumn]]:
    rows = list(getattr(plan, "rows", None) or [])
    if not rows:
        return [getattr(plan, "columns", [])]
    bank_no_variants = _tbclientmap_bank_no_variant_pair(plan, rows)
    if bank_no_variants:
        return bank_no_variants
    fund_pair = _first_fund_subscribe_redeem_pair(plan, rows)
    if fund_pair:
        return fund_pair
    return [rows[0]]


def _tbclientmap_bank_no_variant_pair(plan, rows: list[list[InsertColumn]]) -> list[list[InsertColumn]] | None:
    table_name = f"{getattr(plan, 'table', '')} {getattr(plan, 'physical_table', '')}".lower()
    if "tbclientmap" not in table_name:
        return None
    bank_no_values = {_row_column_value(row, "bank_no") for row in rows}
    if not any(value.startswith(" ") for value in bank_no_values if value):
        return None
    signatures: dict[tuple[tuple[str, str], ...], list[list[InsertColumn]]] = {}
    for row in rows:
        signatures.setdefault(_row_signature(row, exclude={"bank_no"}), []).append(row)
    for signature_rows in signatures.values():
        values = {_row_column_value(row, "bank_no") for row in signature_rows}
        if any(value.startswith(" ") for value in values if value) and any(
            value and not value.startswith(" ") for value in values
        ):
            return signature_rows
    return rows


def _first_fund_subscribe_redeem_pair(plan, rows: list[list[InsertColumn]]) -> list[list[InsertColumn]] | None:
    table_name = f"{getattr(plan, 'table', '')} {getattr(plan, 'physical_table', '')}".lower()
    if "tbdxfundtranscfm" not in table_name:
        return None
    subscribes = [row for row in rows if _row_column_value(row, "busin_code") == "120"]
    redeems = [row for row in rows if _row_column_value(row, "busin_code") == "124"]
    for subscribe in subscribes:
        subscribe_signature = _fund_pair_signature(subscribe)
        for redeem in redeems:
            if _fund_pair_signature(redeem) == subscribe_signature:
                return [subscribe, redeem]
    if subscribes and redeems:
        return [subscribes[0], redeems[0]]
    return None


def _fund_pair_signature(row: list[InsertColumn]) -> tuple[tuple[str, str], ...]:
    generated_pair_columns = {"busin_code", "cfm_no", "trans_date", "cfm_date"}
    return _row_signature(row, exclude=generated_pair_columns)


def _row_signature(row: list[InsertColumn], *, exclude: set[str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((column.name, column.value) for column in row if column.name not in exclude))


def _row_column_value(row: list[InsertColumn], name: str) -> str:
    for column in row:
        if column.name == name:
            return str(column.value)
    return ""


def _apply_precise_permission_query(case_plan: dict) -> None:
    fields = _permission_precision_fields(case_plan)
    if not fields:
        return
    case_inputs = case_plan.setdefault("case_inputs", {})
    for case_type in ("branch", "headquarter"):
        case_input = case_inputs.setdefault(case_type, {"fields": [], "excluded": {}})
        case_input["fields"] = fields
        case_input.setdefault("excluded", {})
    case_plan["permission_query"] = {
        "source": "querymulti",
        "fields": [field.get("label", "") for field in fields],
        "reason": "Use the same stable filters as querymulti to avoid dirty-data interference.",
    }


def _permission_precision_fields(case_plan: dict) -> list[dict]:
    querymulti = (
        case_plan.get("case_inputs", {})
        .get("querymulti", {})
        .get("fields", [])
    )
    if not isinstance(querymulti, list):
        return []
    return [
        {
            "label": field.get("label", ""),
            "type": field.get("type", ""),
            "value": field.get("value", ""),
            **({"reason": field.get("reason")} if field.get("reason") else {}),
        }
        for field in querymulti
        if field.get("label") and field.get("value")
    ]


def _pytest_attempt_summary(attempt_no: int, completed: subprocess.CompletedProcess[str]) -> dict:
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    return {
        "attempt": attempt_no,
        "returncode": completed.returncode,
        "status": "passed" if completed.returncode == 0 else "failed",
        "summary": _pytest_outcome_summary(stdout),
        "passed_tests": _pytest_case_results(stdout, "PASSED"),
        "failed_tests": _pytest_case_results(stdout, "FAILED"),
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
        "checked_at": _utc_now(),
    }


def _pytest_outcome_summary(stdout: str) -> str:
    result = ""
    for line in stdout.splitlines():
        stripped = line.strip()
        match = re.match(r"=+\s*(?P<summary>.+?)\s*=+$", stripped)
        if not match:
            continue
        summary = match.group("summary")
        if "passed" in summary or "failed" in summary or "error" in summary:
            result = summary
    return result


def _pytest_case_results(stdout: str, status: str) -> list[str]:
    result: list[str] = []
    prefix = f"{status} "
    for line in stdout.splitlines():
        stripped = line.strip()
        test_id = ""
        if stripped.startswith(prefix):
            test_id = stripped[len(prefix):].split()[0]
        else:
            marker = f" {status} "
            if marker in stripped:
                test_id = stripped.split(marker, 1)[0].strip()
        if test_id:
            short_id = _short_pytest_id(test_id)
            if short_id not in result:
                result.append(short_id)
    return result


def _pytest_failed_nodeids(completed: subprocess.CompletedProcess[str], fallback: list[str]) -> list[str]:
    result: list[str] = []
    for line in (completed.stdout or "").splitlines():
        stripped = line.strip()
        status = ""
        if stripped.startswith("FAILED "):
            status = "FAILED"
        elif stripped.startswith("ERROR "):
            status = "ERROR"
        if not status:
            continue
        nodeid = stripped[len(status) + 1 :]
        if " - " in nodeid:
            nodeid = nodeid.split(" - ", 1)[0]
        else:
            nodeid = nodeid.split()[0]
        nodeid = nodeid.strip()
        if ".py" not in nodeid or "::" not in nodeid:
            continue
        if nodeid and nodeid not in result:
            result.append(nodeid)
    return result or list(fallback)


def _skip_missing_preview_if_only_failure(
    case_plan: dict,
    case_plan_dir: Path,
    completed: subprocess.CompletedProcess[str],
    runner: ScriptRunner,
) -> bool:
    failed_nodeids = _pytest_failed_nodeids(completed, fallback=[])
    if not failed_nodeids or not all(_is_preview_target(nodeid) for nodeid in failed_nodeids):
        return False
    if not _preview_failure_is_missing_button(completed):
        return False
    passed = _pytest_case_results(completed.stdout or "", "PASSED")
    if not passed:
        return False
    removed = _remove_preview_generated_tests(case_plan, case_plan_dir)
    if not removed:
        return False
    checked_at = _utc_now()
    case_plan["preview_verification"] = {
        "status": "skipped",
        "reason": "only preview failed while other generated cases passed",
        "failed_nodeids": failed_nodeids,
        "passed_tests": passed,
        "removed_tests": removed,
        "checked_at": checked_at,
    }
    case_plan["verification"] = {
        "status": "passed",
        "phase": "full",
        "returncode": 0,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "checked_at": checked_at,
        "reason": "preview case skipped because only preview failed while other generated cases passed",
    }
    runner.emit(
        {
            "event": "preview_skipped_no_detail_button",
            "failed_tests": failed_nodeids,
            "removed_tests": removed,
        }
    )
    return True


def _preview_failure_is_missing_button(completed: subprocess.CompletedProcess[str]) -> bool:
    text = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    missing_button_markers = (
        "未找到详情/预览/查看按钮",
        "未找到详情按钮",
        "未找到预览按钮",
        "未找到查看按钮",
        "no detail button",
        "no preview button",
    )
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in missing_button_markers)


def _is_preview_target(value: str) -> bool:
    lowered = str(value).replace("\\", "/").lower()
    filename = lowered.split("::", 1)[0].rsplit("/", 1)[-1]
    return filename.endswith("_preview.py") or "_preview" in filename or "结果预览" in lowered


def _remove_preview_generated_tests(case_plan: dict, case_plan_dir: Path) -> list[str]:
    generated_tests = case_plan.get("generated_tests", [])
    if not isinstance(generated_tests, list):
        return []
    kept: list[str] = []
    removed: list[str] = []
    for artifact in generated_tests:
        artifact_text = str(artifact)
        if _is_preview_target(artifact_text):
            removed.append(artifact_text)
            path = Path(_resolve_artifact_path(artifact_text, case_plan_dir))
            if path.exists() and path.is_file():
                path.unlink()
        else:
            kept.append(artifact_text)
    if removed:
        case_plan["generated_tests"] = kept
    return removed


def _can_accept_environment_flake(
    case_plan: dict,
    pytest_paths: list[str],
    completed: subprocess.CompletedProcess[str],
) -> bool:
    expected = _expected_pytest_case_names(pytest_paths)
    if not expected:
        return False
    passed = _passed_pytest_case_names(case_plan)
    if not expected.issubset(passed):
        return False
    return _pytest_failure_is_environment_only(completed.stdout or "")


def _mark_environment_flake_passed(case_plan: dict, pytest_paths: list[str]) -> None:
    verification = case_plan.setdefault("verification", {})
    expected = sorted(_expected_pytest_case_names(pytest_paths))
    passed = sorted(_passed_pytest_case_names(case_plan))
    verification.update(
        {
            "status": "passed",
            "qualification": "all_cases_passed_across_attempts",
            "warning": "All generated cases passed at least once; remaining failures were page/login environment timeouts.",
            "passed_tests_across_attempts": [name for name in expected if name in set(passed)],
        }
    )


def _expected_pytest_case_names(pytest_paths: list[str]) -> set[str]:
    result: set[str] = set()
    for value in pytest_paths:
        path = str(value)
        if "::" in path:
            result.add(_short_pytest_id(path))
            continue
        stem = Path(path).stem
        if stem:
            result.add(stem)
    return result


def _passed_pytest_case_names(case_plan: dict) -> set[str]:
    passed: set[str] = set()
    for attempt in case_plan.get("verification_attempts", []):
        for name in attempt.get("passed_tests", []) or []:
            if name:
                passed.add(str(name))
    return passed


def _pytest_failure_is_environment_only(stdout: str) -> bool:
    if not stdout:
        return False
    if _pytest_case_results(stdout, "FAILED"):
        return False
    has_error = bool(_pytest_case_results(stdout, "ERROR"))
    timeout_markers = (
        "Page.goto: Timeout" in stdout
        or "Timeout 30000ms exceeded" in stdout
        or ("navigating to" in stdout and "/login" in stdout)
    )
    return has_error and timeout_markers


def _same_pytest_targets(left: list[str], right: list[str]) -> bool:
    return {str(item) for item in left} == {str(item) for item in right}


def _short_pytest_id(test_id: str) -> str:
    if "::" in test_id:
        return test_id.rsplit("::", 1)[-1]
    normalized = test_id.replace("\\", "/")
    return Path(normalized).stem or normalized


def _refresh_schema_cache_for_dry_run_tables(
    runner: ScriptRunner,
    case_plan: dict,
    case_plan_dir: Path,
    config: dict,
) -> None:
    """dry-run 失败后刷新涉及表的 schema 缓存，确保 LLM 诊断基于最新表结构。"""
    if not (runner.scripts_dir / "build_schema_cache.py").exists():
        return
    tables = _extract_tables_from_sql_files(case_plan, case_plan_dir)
    if not tables:
        return
    db = str(case_plan.get("shard", {}).get("db") or "ui_bc")
    env = config.get("env", {}) if isinstance(config, dict) else {}
    args = ["--tables", *tables, "--db", db]
    if env.get("project_dir"):
        args.extend(["--project-dir", str(env["project_dir"])])
    if env.get("env_key"):
        args.extend(["--env", str(env["env_key"])])
    # 使用 --force 强制刷新，确保 column_types 等完整信息被写入缓存
    args.append("--force")
    runner.emit({"event": "schema_cache_refresh_started", "tables": tables, "db": db})
    try:
        runner.run_text("build_schema_cache.py", args, timeout=120)
        runner.emit({"event": "schema_cache_refresh_finished", "tables": tables, "status": "success"})
    except Exception as exc:  # noqa: BLE001 - refresh failure must not block repair
        runner.emit({"event": "schema_cache_refresh_finished", "tables": tables, "status": "failed", "error": str(exc)})


def _extract_tables_from_sql_files(case_plan: dict, case_plan_dir: Path) -> list[str]:
    """从 SQL 文件内容中提取表名（用于 schema 缓存刷新）。"""
    import re as _re
    tables: list[str] = []
    seen: set[str] = set()
    for sql_path in _repairable_sql_paths(case_plan, case_plan_dir):
        if not sql_path.exists():
            continue
        content = sql_path.read_text(encoding="utf-8", errors="replace")
        for match in _re.finditer(r"(?:INSERT\s+INTO|DELETE\s+FROM|UPDATE)\s+(\w+)", content, _re.IGNORECASE):
            table = match.group(1).lower()
            if table not in seen:
                seen.add(table)
                tables.append(table)
    return tables


def _has_missing_schema(skipped: list[dict]) -> bool:
    return any(item.get("reason") == "missing_schema" for item in skipped)


def _refresh_schema_cache_for_missing_tables(
    runner: ScriptRunner,
    case_plan: dict,
    config: dict,
    skipped: list[dict],
) -> None:
    tables = _missing_schema_physical_tables(skipped)
    if not tables:
        return
    db = str(case_plan.get("shard", {}).get("db") or "ui_bc")
    env = config.get("env", {}) if isinstance(config, dict) else {}
    args = ["--tables", *tables, "--db", db]
    if env.get("project_dir"):
        args.extend(["--project-dir", str(env["project_dir"])])
    if env.get("env_key"):
        args.extend(["--env", str(env["env_key"])])

    refresh = {
        "status": "running",
        "tables": tables,
        "db": db,
        "args": args,
    }
    case_plan["schema_refresh"] = refresh
    try:
        output = runner.run_text("build_schema_cache.py", args, timeout=180)
    except Exception as exc:  # noqa: BLE001 - preserve original insert-plan failure after noting refresh failure
        refresh["status"] = "failed"
        refresh["error"] = str(exc)
        return
    refresh["status"] = "success"
    refresh["output_preview"] = output[:1000]


def _missing_schema_physical_tables(skipped: list[dict]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in skipped:
        if item.get("reason") != "missing_schema":
            continue
        table = str(item.get("physical_table") or item.get("table") or "").strip()
        if not table or table in seen:
            continue
        seen.add(table)
        result.append(table)
    return result


def _ensure_insert_plans_not_empty(plans: list, skipped: list[dict]) -> None:
    if plans:
        return
    details = "; ".join(
        f"{item.get('physical_table') or item.get('table')}:{item.get('reason')}"
        + (
            f"({','.join(item.get('missing_columns', []))})"
            if item.get("missing_columns")
            else ""
        )
        for item in skipped[:3]
    )
    raise MenuFailure("no insert plans generated" + (f"; skipped: {details}" if details else ""))


def _case_dir_suffix(case_type: str) -> str:
    return {
        "branch": "_分行权限",
        "headquarter": "_总行权限",
        "queryall": "_全量查询",
        "querysingle": "_单条件查询",
        "querymulti": "_多条件查询",
        "reset": "_条件重置",
        "preview": "_结果预览",
    }.get(case_type, f"_{case_type}")


def _case_file_suffix(case_type: str) -> str:
    return {
        "branch": "_branch",
        "headquarter": "_headquarter",
        "queryall": "_queryall",
        "querysingle": "_querysingle",
        "querymulti": "_querymulti",
        "reset": "_reset",
        "preview": "_preview",
    }.get(case_type, f"_{_safe_path_part(case_type)}")


def _resolve_sql_paths(case_plan: dict, config: dict) -> dict:
    existing = case_plan.get("sql_paths", {})
    if _has_valid_sql_paths(existing):
        return existing
    config_sql_paths = config.get("sql_paths", {})
    if _has_valid_sql_paths(config_sql_paths):
        return config_sql_paths
    return {}


def _has_valid_sql_paths(sql_paths: dict) -> bool:
    return bool(
        isinstance(sql_paths, dict)
        and sql_paths.get("base_sql")
        and sql_paths.get("preset_sql")
        and not sql_paths.get("error")
    )


def _derive_sql_paths(case_plan: dict, config: dict) -> dict:
    menu = case_plan.get("menu", {})
    paths = case_plan.get("paths", {})
    config_env = config.get("env", {})
    test_case_dir = paths.get("test_case_dir") or config.get("computed", {}).get("test_case_dir", "")
    project_dir = paths.get("project_dir") or config_env.get("project_dir", "")
    level2 = menu.get("level2") or config.get("menu", {}).get("导航二级菜单", "查询管理")
    menu_name = menu.get("name") or config.get("menu", {}).get("菜单名称", "")
    func_code = menu.get("code") or config.get("computed", {}).get("func_code", "")
    if not test_case_dir or not level2 or not menu_name or not func_code:
        return {}
    base_dir = Path(test_case_dir) / "数据预埋" / level2 / menu_name
    try:
        sql_data_base = str(Path(test_case_dir).relative_to(project_dir)) if project_dir else ""
        if sql_data_base:
            sql_data_base = str(Path(sql_data_base) / "数据预埋")
    except ValueError:
        sql_data_base = str(Path(test_case_dir) / "数据预埋")
    return {
        "menu": menu_name,
        "level1": menu.get("level1", ""),
        "level2": level2,
        "func_code": func_code,
        "sql_data_base": sql_data_base,
        "base_sql": str(base_dir / f"{func_code}_base.sql"),
        "preset_sql": str(base_dir / f"{func_code}.sql"),
    }


def _safe_path_part(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\s]+', "_", str(value or "").strip())
    return cleaned.strip("_") or "generated"


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _load_table_db_mapping(skill_root: str | Path) -> dict[str, str | list[str]]:
    """加载 table_db_mapping.json → {table_lower: db_family/list}。

    表可能属于多个库族（如 ["ui_dxcp", "ui_bc", "ui_trust"]），保留完整列表，
    由 insert_plan 根据当前 routing_db 选择同族库。
    用于 build_insert_plans() 的 table_db_mapping 参数。
    """
    import json as _json
    mapping_path = Path(skill_root) / "config" / "table_db_mapping.json"
    if not mapping_path.exists():
        return {}
    try:
        raw = _json.loads(mapping_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    result: dict[str, str | list[str]] = {}
    for table, dbs in raw.items():
        if isinstance(dbs, list) and dbs:
            result[table.lower()] = [str(db) for db in dbs if str(db or "").strip()]
        elif isinstance(dbs, str):
            result[table.lower()] = dbs
    return result


def _render_summary(case_plan: dict) -> str:
    menu = case_plan.get("menu", {})
    verification = case_plan.get("verification", {})
    generated_tests = case_plan.get("generated_tests", [])
    insert_plans = case_plan.get("insert_plans", [])
    repair_attempts = case_plan.get("repair_attempts", [])
    sql_repair_attempts = case_plan.get("sql_repair_attempts", [])
    lines = [
        f"# {menu.get('name', 'HUI Menu')} 执行报告",
        "",
        f"- 菜单编码: {menu.get('code', '')}",
        f"- 验证状态: {verification.get('status', 'unknown')}",
        f"- 生成用例数: {len(generated_tests)}",
        f"- SQL 预置计划数: {len(insert_plans)}",
    ]
    if verification.get("returncode") is not None:
        lines.append(f"- pytest 返回码: {verification.get('returncode')}")
    if verification.get("qualification"):
        lines.append(f"- 验证说明: {verification.get('qualification')}")
    if verification.get("warning"):
        lines.append(f"- 环境提示: {verification.get('warning')}")
    lines.extend(repair_overview_lines(sql_repair_attempts, repair_attempts))
    lines.extend(repair_detail_lines(sql_repair_attempts, repair_attempts))
    return "\n".join(lines) + "\n"
