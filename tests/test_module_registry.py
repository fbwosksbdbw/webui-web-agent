import importlib.util
import sys
from pathlib import Path


def _load_module_registry():
    scripts_dir = Path(__file__).resolve().parents[1] / "knowledge" / "scripts"
    spec = importlib.util.spec_from_file_location("module_registry", scripts_dir / "module_registry.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_sql_extractor():
    scripts_dir = Path(__file__).resolve().parents[1] / "knowledge" / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("sql_extractor", scripts_dir / "sql_extractor.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_business_pension_uses_dxcp_module_not_pension():
    registry = _load_module_registry()

    assert registry.infer_module_from_text("商业养老金产品信息查询") == "dxcp"
    assert registry.infer_module_from_text("商养客户当前委托查询") == "dxcp"
    assert registry.module_to_chinese("dxcp") == "商业养老金"
    assert registry.log_path_key("dxcp") == "log_path_dxcp"
    assert registry.env_keyword("dxcp") == "dxcp"


def test_personal_pension_uses_per_module_not_dxcp():
    registry = _load_module_registry()

    assert registry.infer_module_from_text("个人养老金资金账户查询") == "per"
    assert registry.infer_module_from_text("tbpensiontransreq1") == "per"
    assert registry.module_to_chinese("per") == "个人养老金"
    assert registry.module_to_chinese("pension") == "个人养老金"
    assert registry.log_path_key("per") == "log_path_pension"
    assert registry.env_keyword("per") == "per"


def test_fina_and_lcdx_are_not_dxcp():
    registry = _load_module_registry()

    assert registry.infer_module_from_text("理财代销") == "fina"
    assert registry.infer_module_from_text("理财销售") == "fina"
    assert registry.module_to_chinese("fina") == "理财代销"
    assert registry.log_path_key("fina") == "log_path_fina"
    assert registry.env_keyword("fina") == "fina"


def test_db_family_mapping_keeps_commercial_and_personal_pension_separate():
    sql_extractor = _load_sql_extractor()

    assert sql_extractor._DB_FAMILY_TO_MODULE["ui_dxcp"] == "商业养老金"
    assert sql_extractor._DB_FAMILY_TO_MODULE["ui_per"] == "个人养老金"
    assert sql_extractor._MODULE_TO_DB_FAMILY["商业养老金"] == "ui_dxcp"
    assert sql_extractor._MODULE_TO_DB_FAMILY["个人养老金"] == "ui_per"
