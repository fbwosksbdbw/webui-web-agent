from hui_agent.case_inputs import CaseInputPlanner


FIELDS = [
    {"label": "客户标识类型", "type": "single-select", "value": "0:入账账号", "is_required": True},
    {"label": "客户标识", "type": "input", "value": "A3060210020001", "is_required": True},
    {"label": "TA代码", "type": "single-select", "value": "JS:嘉实基金", "is_required": False},
    {"label": "产品代码", "type": "single-select", "value": "11:AI自动化测试产品", "is_required": False},
    {"label": "开始日期", "type": "input", "value": "2026-01-01", "is_required": False},
    {"label": "结束日期", "type": "input", "value": "2026-12-31", "is_required": False},
    {"label": "交易机构", "type": "tree-select", "value": "__FIRST_OPTION__", "is_required": False},
    {"label": "申请编号", "type": "input", "value": "SQ20260606001", "is_required": False},
]


def test_querymulti_uses_stable_filter_set_not_all_fields():
    planner = CaseInputPlanner(
        fields=FIELDS,
        field_groups=[
            {"name": "客户标识组", "fields": ["客户标识类型", "客户标识"], "order": ["客户标识类型", "客户标识"]},
            {"name": "TA产品组", "fields": ["TA代码", "产品代码"], "order": ["TA代码", "产品代码"]},
        ],
    )

    case_inputs = planner.plan()
    labels = [field.label for field in case_inputs["querymulti"].fields]

    assert "客户标识类型" in labels
    assert "客户标识" in labels
    assert "TA代码" in labels
    assert "产品代码" in labels
    assert "交易机构" not in labels
    assert "申请编号" not in labels
    assert len(labels) < len(FIELDS)


def test_querysingle_closes_dependencies_for_selected_filter():
    planner = CaseInputPlanner(fields=FIELDS, field_groups=[
        {"name": "TA产品组", "fields": ["TA代码", "产品代码"], "order": ["TA代码", "产品代码"]},
    ])

    case_inputs = planner.plan(single_filter_label="产品代码")
    labels = [field.label for field in case_inputs["querysingle"].fields]

    assert labels.index("TA代码") < labels.index("产品代码")


def test_reset_uses_safe_input_and_excludes_volatile_fields():
    planner = CaseInputPlanner(fields=FIELDS, field_groups=[])

    reset = planner.plan()["reset"]

    assert reset.fields[0].label == "开始日期"
    assert reset.excluded["申请编号"] == "volatile_unique"
    assert reset.excluded["交易机构"] == "tree_select_unstable"


def test_reset_can_use_stable_select_when_no_input_field_exists():
    planner = CaseInputPlanner(
        fields=[
            {"label": "TA代码", "type": "single-select", "value": "HT:HT基金", "is_required": False},
            {"label": "产品代码", "type": "single-select", "value": "UI_HT_P001:UI自动化HT信托产品", "is_required": False},
        ],
        field_groups=[
            {"name": "TA产品组", "fields": ["TA代码", "产品代码"], "order": ["TA代码", "产品代码"]},
        ],
    )

    reset = planner.plan()["reset"]

    assert [field.label for field in reset.fields] == ["TA代码", "产品代码"]


def test_preview_uses_stable_query_fields_to_make_preview_record_available():
    planner = CaseInputPlanner(fields=FIELDS, field_groups=[])

    preview = planner.plan()["preview"]
    labels = [field.label for field in preview.fields]

    assert "产品代码" in labels
    assert "开始日期" in labels


def test_preset_fields_override_stale_explored_values():
    planner = CaseInputPlanner(
        fields=[
            {"label": "客户标识类型", "type": "single-select", "value": "0:入账账号", "is_required": False},
            {"label": "客户标识", "type": "input", "value": "TA33060210021113", "is_required": False},
            {"label": "TA代码", "type": "single-select", "value": "JS:嘉实基金", "is_required": False},
            {"label": "产品代码", "type": "single-select", "value": "11:AI自动化测试产品", "is_required": False},
        ],
        field_groups=[
            {"name": "客户标识组", "fields": ["客户标识类型", "客户标识"], "order": ["客户标识类型", "客户标识"]},
            {"name": "TA产品组", "fields": ["TA代码", "产品代码"], "order": ["TA代码", "产品代码"]},
        ],
        preset_fields={
            "customer_id_type": {"name": "客户标识类型", "value": "0:入账账号"},
            "customer_id": {"name": "客户标识", "value": "A33060210021113"},
            "ta_client": {"name": "TA账号", "value": "TA33060210021113"},
            "ta_code": {"name": "TA代码", "value": "HT:HT基金"},
            "prd_code": {"name": "产品代码", "value": "UI_HT_P001:UI自动化HT信托产品"},
        },
    )

    case_inputs = planner.plan(single_filter_label="产品代码")
    querysingle = {field.label: field.value for field in case_inputs["querysingle"].fields}
    querymulti = {field.label: field.value for field in case_inputs["querymulti"].fields}

    assert querysingle["产品代码"] == "UI_HT_P001:UI自动化HT信托产品"
    assert querysingle["TA代码"] == "HT:HT基金"
    assert querymulti["客户标识"] == "A33060210021113"
    assert querymulti["客户标识"] != "TA33060210021113"


def test_account_type_and_account_aliases_close_customer_identity_group():
    planner = CaseInputPlanner(
        fields=[
            {"label": "账户类型", "type": "single-select", "value": "0:入账账号", "is_required": False},
            {"label": "账号", "type": "input", "value": "A33060210021113", "is_required": False},
            {"label": "产品代码", "type": "single-select", "value": "P1:产品1", "is_required": False},
        ],
        field_groups=[
            {
                "name": "客户标识组",
                "fields": ["客户标识类型", "客户标识"],
                "order": ["客户标识类型", "客户标识"],
                "aliases": {"账户类型": "客户标识类型", "账号": "客户标识"},
            }
        ],
    )

    case_inputs = planner.plan(single_filter_label="账号")
    labels = [field.label for field in case_inputs["querysingle"].fields]

    assert labels == ["账户类型", "账号"]


def test_account_alias_group_is_used_for_querymulti_and_preview_when_no_canonical_labels():
    planner = CaseInputPlanner(
        fields=[
            {"label": "账户类型", "type": "single-select", "value": "0:入账账号", "is_required": False},
            {"label": "账号", "type": "input", "value": "A33060210021113", "is_required": False},
        ],
        field_groups=[
            {
                "name": "客户标识组",
                "fields": ["客户标识类型", "客户标识"],
                "order": ["客户标识类型", "客户标识"],
                "aliases": {"账户类型": "客户标识类型", "账号": "客户标识"},
            }
        ],
    )

    case_inputs = planner.plan()

    assert [field.label for field in case_inputs["querymulti"].fields] == ["账户类型", "账号"]
    assert [field.label for field in case_inputs["preview"].fields] == ["账户类型", "账号"]


def test_required_alias_group_is_not_duplicated_when_both_fields_are_required():
    planner = CaseInputPlanner(
        fields=[
            {"label": "账户类型", "type": "single-select", "value": "0:入账账号", "is_required": True},
            {"label": "账号", "type": "input", "value": "A33060210021113", "is_required": True},
        ],
        field_groups=[
            {
                "name": "客户标识组",
                "fields": ["客户标识类型", "客户标识"],
                "order": ["客户标识类型", "客户标识"],
                "aliases": {"账户类型": "客户标识类型", "账号": "客户标识"},
            }
        ],
    )

    case_inputs = planner.plan()

    assert [field.label for field in case_inputs["queryall"].fields] == ["账户类型", "账号"]
    assert [field.label for field in case_inputs["branch"].fields] == ["账户类型", "账号"]
    assert [field.label for field in case_inputs["headquarter"].fields] == ["账户类型", "账号"]


def test_semantic_business_inputs_are_filled_with_stable_values():
    planner = CaseInputPlanner(
        fields=[
            {"label": "监控天数", "type": "input", "value": "AUTO_0610001045", "is_required": False},
            {"label": "开始日期", "type": "input", "value": "AUTO_0610001046", "is_required": False},
            {"label": "结束日期", "type": "input", "value": "AUTO_0610001047", "is_required": False},
            {"label": "客户标识", "type": "input", "value": "A1", "is_required": False},
        ],
        field_groups=[],
        preset_fields={
            "query_date_start": {"name": "查询起始日期", "value": "2026-01-01"},
            "query_date_end": {"name": "查询结束日期", "value": "2026-01-30"},
        },
    )

    case_inputs = planner.plan()
    querymulti = {field.label: field.value for field in case_inputs["querymulti"].fields}
    branch = {field.label: field.value for field in case_inputs["branch"].fields}

    assert querymulti["监控天数"] == "30"
    assert querymulti["开始日期"] == "2026-01-01"
    assert querymulti["结束日期"] == "2026-01-30"
    assert branch["监控天数"] == "30"
