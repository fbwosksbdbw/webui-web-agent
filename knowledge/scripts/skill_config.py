# -*- coding: utf-8 -*-
"""
统一配置入口 — 所有脚本从这里读取配置，无硬编码默认值。

用法:
    from skill_config import get_config
    config = get_config("path/to/config.json")
    print(config.cdp_url)
    print(config.customer_id)
"""
import json
import os
import sys
from typing import Optional

# SkillConfig 通过 config.json 中的 project_dir 定位项目根目录，
# 不再靠 skill 文件自身位置猜测。project_dir 由 config_reader.py init 从 Excel 写入。
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from module_registry import env_keyword, log_path_key, normalize_module


def resolve_env(module: str, project_dir: str = None) -> str:
    """Search Config.env_dbs for matching module keyword. Returns env name like 'ifm60-trust'."""
    if project_dir and project_dir not in sys.path:
        sys.path.insert(0, project_dir)
    from conf.config import Config

    module = normalize_module(module)
    keyword = env_keyword(module)

    for env_name, cfg in Config.env_dbs.items():
        for db_name in cfg.get('databases', {}):
            if db_name != 'ui_bc' and keyword in db_name.lower():
                return env_name

    raise ValueError(f"No env matching module={module} in Config.env_dbs")


class SkillConfig:
    """统一配置类，从 config.json 读取所有配置。"""

    def __init__(self, config_json_path: str):
        if not os.path.exists(config_json_path):
            raise FileNotFoundError(f"config.json 不存在: {config_json_path}")
        with open(config_json_path, 'r', encoding='utf-8') as f:
            self._data = json.load(f)

        self._env = self._data.get('env', {})
        self._menu = self._data.get('menu', {})
        self._presets = self._data.get('presets', {})
        self._preset_fields = self._data.get('preset_fields', {})
        self._computed = self._data.get('computed', {})

        # login_url 从 conf/config.py 读取
        self._login_url = None

    def _require(self, key: str, section: str = 'env') -> str:
        """获取配置值，缺失则报错"""
        data = getattr(self, f'_{section}', {})
        val = data.get(key)
        if val is None or val == '':
            raise ValueError(f"config.json 的 {section} 缺少必填项: {key}")
        return str(val)

    # ========== 环境配置 ==========

    @property
    def cdp_url(self) -> str:
        """CDP 调试地址（如 http://localhost:9222）"""
        return self._require('cdp_url')

    @property
    def chrome_path(self) -> str:
        """Chrome 可执行文件路径"""
        return self._require('chrome_path')

    @property
    def node_path(self) -> str:
        """Node.js 可执行文件路径（留空则自动搜索 PATH）"""
        return self._env.get('node_path', '')

    @property
    def login_url(self) -> str:
        """登录地址（从 {project_dir}/conf/config.py 的 Config.envs[env_key] 读取）"""
        if self._login_url is None:
            env_key = self._require('env_key')
            try:
                project_root = self.project_dir
                if project_root not in sys.path:
                    sys.path.insert(0, project_root)
                from conf.config import Config
                env_cfg = Config.envs.get(env_key, {})
                self._login_url = env_cfg.get('login_url', '')
            except Exception:
                self._login_url = ''
            if not self._login_url:
                raise ValueError(f"conf/config.py 中 {env_key} 缺少 login_url")
        return self._login_url

    @property
    def branch_user(self) -> str:
        """分行登录用户名"""
        return self._require('branch_user')

    @property
    def branch_password(self) -> str:
        """分行登录密码"""
        return self._require('branch_password')

    @property
    def env_key(self) -> str:
        """环境标识（如 ifm60-trust）"""
        return self._require('env_key')

    @property
    def ssh_host(self) -> str:
        """SSH 服务器地址"""
        return self._require('app_server_host')

    @property
    def ssh_port(self) -> int:
        """SSH 端口"""
        return int(self._require('app_server_port'))

    @property
    def ssh_user(self) -> str:
        """SSH 用户名"""
        return self._require('app_server_user')

    @property
    def ssh_password(self) -> str:
        """SSH 密码"""
        return self._require('app_server_password')

    @property
    def project_dir(self) -> str:
        """项目根目录"""
        val = self._require('project_dir')
        if val.startswith('<') or '请配置' in val:
            raise ValueError(
                f"project_dir 未配置（当前值: {val}），"
                f"请在 config.json 的 env.project_dir 中填入您的项目根目录"
            )
        return val

    @property
    def log_path(self) -> str:
        """日志目录路径（根据 module 自动选择）"""
        module = normalize_module(self._computed.get('module', ''))
        key = log_path_key(module or 'dxtrust')
        return self._require(key)

    @property
    def log_file(self) -> str:
        """日志文件名"""
        return self._require('log_file')

    @property
    def log_file_stdout(self) -> str:
        """标准输出日志文件名"""
        return self._require('log_file_stdout')

    # ========== 菜单配置 ==========

    @property
    def menu_name(self) -> str:
        """菜单名称"""
        return self._require('菜单名称', 'menu')

    @property
    def level1(self) -> str:
        """导航一级菜单"""
        return self._require('导航一级菜单', 'menu')

    @property
    def level2(self) -> str:
        """导航二级菜单"""
        return self._require('导航二级菜单', 'menu')

    @property
    def menu_url(self) -> str:
        """菜单 URL hash"""
        return self._computed.get('menu_url', '')

    @property
    def test_case_dir(self) -> str:
        """用例目录（相对路径）"""
        return self._computed.get('test_case_dir', '')

    # ========== 预埋数据 ==========

    @property
    def customer_id(self) -> str:
        """客户标识"""
        return self._require('customer_id', 'presets')

    @property
    def customer_id_type(self) -> str:
        """客户标识类型选项"""
        return self._presets.get('customer_id_type', self._presets.get('account_type', ''))

    @property
    def ta_code(self) -> str:
        """主 TA 代码"""
        return self._require('ta_code_1', 'presets')

    @property
    def ta_option(self) -> str:
        """主 TA 下拉选项"""
        return self._require('ta_option_1', 'presets')

    @property
    def ta_code_2(self) -> str:
        """副 TA 代码"""
        return self._presets.get('ta_code_2', '')

    @property
    def ta_option_2(self) -> str:
        """副 TA 下拉选项"""
        return self._presets.get('ta_option_2', '')

    @property
    def prd_code(self) -> str:
        """产品代码"""
        return self._require('prd_code', 'presets')

    @property
    def prd_option(self) -> str:
        """产品下拉选项"""
        return self._require('prd_option', 'presets')

    @property
    def branch_no(self) -> str:
        """分行机构编号"""
        return self._require('branch_no', 'presets')

    @property
    def branch_name(self) -> str:
        """分行机构名称"""
        return self._require('branch_name', 'presets')

    @property
    def hq_branch_no(self) -> str:
        """总行机构编号"""
        return self._require('hq_branch_no', 'presets')

    @property
    def hq_branch_name(self) -> str:
        """总行机构名称"""
        return self._require('hq_branch_name', 'presets')

    @property
    def query_date_start(self) -> str:
        """查询起始日期"""
        return self._require('query_date_start', 'presets')

    @property
    def query_date_end(self) -> str:
        """查询结束日期"""
        return self._require('query_date_end', 'presets')

    def get_preset(self, key: str, default: str = '') -> str:
        """获取任意预埋数据"""
        return self._presets.get(key, default)

    @property
    def preset_fields(self) -> dict:
        """预埋字段名映射 {key: {name, value}}，供匹配探勘字段使用"""
        return self._preset_fields

    def get_env(self, key: str, default: str = '') -> str:
        """获取任意环境配置"""
        return self._env.get(key, default)

    # ========== SSH 配置字典 ==========

    @property
    def ssh_config(self) -> dict:
        """SSH 配置字典（供 cdp_executor 等使用）"""
        return {
            'host': self.ssh_host,
            'port': self.ssh_port,
            'username': self.ssh_user,
            'password': self.ssh_password,
            'log_path': self.log_path,
            'log_file': self.log_file,
            'customer_id': self.customer_id,
        }


# 全局实例缓存
_config_instance: Optional[SkillConfig] = None
_config_path: Optional[str] = None


def get_config(config_json_path: str = None) -> SkillConfig:
    """获取配置实例（单例模式，同一路径只加载一次）"""
    global _config_instance, _config_path

    if config_json_path is None:
        # 默认查找：从调用者向上查找 config.json
        caller_frame = sys._getframe(1)
        caller_file = getattr(caller_frame, 'f_filename', None) or caller_frame.f_code.co_filename
        caller_dir = os.path.dirname(os.path.abspath(caller_file))
        config_json_path = os.path.join(caller_dir, 'config.json')
        if not os.path.exists(config_json_path):
            # 尝试上级目录
            config_json_path = os.path.join(os.path.dirname(caller_dir), 'config.json')

    if _config_instance is not None and _config_path == config_json_path:
        return _config_instance

    _config_instance = SkillConfig(config_json_path)
    _config_path = config_json_path
    return _config_instance


def reset_config():
    """重置配置实例（测试用）"""
    global _config_instance, _config_path
    _config_instance = None
    _config_path = None
