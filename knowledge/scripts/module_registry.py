# -*- coding: utf-8 -*-
"""Business module registry shared by HUI skill scripts.

The module code is the application/log routing key, not a literal table prefix.
Commercial pension ("商养") routes through ``dxcp``/``tbdxcp``. Personal pension
uses the ``per`` environment and ``tbpension`` table family.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleInfo:
    code: str
    chinese: str
    env_keyword: str
    log_path_key: str
    sql_data_attr: str
    aliases: tuple[str, ...]


MODULES = {
    "dxtrust": ModuleInfo(
        code="dxtrust",
        chinese="信托代销",
        env_keyword="trust",
        log_path_key="log_path_dxtrust",
        sql_data_attr="sql_data_dir_dxtrust",
        aliases=("信托", "信托代销", "dxtrust", "trust", "tr"),
    ),
    "dxfund": ModuleInfo(
        code="dxfund",
        chinese="基金代销",
        env_keyword="fund",
        log_path_key="log_path_dxfund",
        sql_data_attr="sql_data_dir_dxfund",
        aliases=("基金", "基金代销", "dxfund", "fund", "if"),
    ),
    "fina": ModuleInfo(
        code="fina",
        chinese="理财代销",
        env_keyword="fina",
        log_path_key="log_path_fina",
        sql_data_attr="sql_data_dir_fina",
        aliases=("理财", "理财代销", "理财销售", "lcdx", "fina", "fi"),
    ),
    "dxcp": ModuleInfo(
        code="dxcp",
        chinese="商业养老金",
        env_keyword="dxcp",
        log_path_key="log_path_dxcp",
        sql_data_attr="sql_data_dir",
        aliases=("商业养老金", "商养", "dxcp"),
    ),
    "hstc": ModuleInfo(
        code="hstc",
        chinese="汇市通",
        env_keyword="hstc",
        log_path_key="log_path_hstc",
        sql_data_attr="sql_data_dir_hstc",
        aliases=("汇市通", "hstc"),
    ),
    "insure": ModuleInfo(
        code="insure",
        chinese="保险",
        env_keyword="insure",
        log_path_key="log_path_insure",
        sql_data_attr="sql_data_dir_insure",
        aliases=("保险", "insure"),
    ),
    "per": ModuleInfo(
        code="per",
        chinese="个人养老金",
        env_keyword="per",
        log_path_key="log_path_pension",
        sql_data_attr="sql_data_dir_per",
        aliases=("个人养老金", "个人养老", "tbpension", "pension", "per"),
    ),
}


_ALIAS_TO_CODE = {
    alias.lower(): code
    for code, info in MODULES.items()
    for alias in info.aliases
}


def normalize_module(module: str) -> str:
    """Return canonical module code; unknown values are returned unchanged."""
    text = (module or "").strip()
    if not text:
        return ""
    return _ALIAS_TO_CODE.get(text.lower(), text)


def module_info(module: str) -> ModuleInfo:
    code = normalize_module(module)
    if code not in MODULES:
        raise ValueError(f"Unknown module: {module}")
    return MODULES[code]


def module_to_chinese(module: str) -> str:
    try:
        return module_info(module).chinese
    except ValueError:
        return module


def env_keyword(module: str) -> str:
    return module_info(module).env_keyword


def log_path_key(module: str) -> str:
    return module_info(module).log_path_key


def sql_data_attr(module: str) -> str:
    return module_info(module).sql_data_attr


def infer_module_from_text(text: str, default: str = "dxtrust") -> str:
    """Infer canonical module code from a menu name, level-1 name, URL, or tree path."""
    value = (text or "").strip()
    if not value:
        return default
    lower = value.lower()
    exact = normalize_module(value)
    if exact in MODULES:
        return exact
    # Order matters: "商业养老金" contains "养老金" but should be routed by dxcp.
    for code in ("dxcp", "dxfund", "dxtrust", "fina", "insure", "hstc", "per"):
        for alias in MODULES[code].aliases:
            alias_lower = alias.lower()
            if not alias:
                continue
            if alias.isascii() and len(alias) <= 2:
                continue
            if alias in value or alias_lower in lower:
                return code
    return default


def infer_module_from_keyword_mapping(text: str, mapping: str, default: str = "dxtrust") -> str:
    """Infer module using Excel's 'keyword:module' mapping string."""
    value = text or ""
    if mapping:
        for pair in mapping.split(","):
            pair = pair.strip()
            if ":" not in pair:
                continue
            keyword, module = pair.split(":", 1)
            if keyword.strip() and keyword.strip() in value:
                return normalize_module(module.strip())
    return infer_module_from_text(value, default=default)
