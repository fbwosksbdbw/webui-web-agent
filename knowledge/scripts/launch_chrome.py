import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import time

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
import tempfile
import subprocess
import urllib.request


def kill_chrome():
    """关闭所有 Chrome 进程"""
    print("正在关闭 Chrome...")
    if sys.platform == 'win32':
        try:
            subprocess.run(['taskkill', '/F', '/IM', 'chrome.exe'],
                           capture_output=True, timeout=5)
        except (subprocess.TimeoutExpired, Exception):
            try:
                subprocess.run(['powershell', '-Command',
                    'Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue'],
                    capture_output=True, timeout=15)
            except Exception:
                pass
    else:
        subprocess.run(['pkill', '-f', 'chrome'],
                       capture_output=True, timeout=10)
    time.sleep(2)


def _resolve_chrome_exe(chrome_path: str) -> str:
    """解析 Chrome 可执行文件路径。支持填 exe 文件或目录。"""
    if os.path.isfile(chrome_path):
        return chrome_path
    if os.path.isdir(chrome_path):
        for name in (["chrome.exe", "chrome"] if sys.platform == 'win32' else ["google-chrome", "chromium", "chrome"]):
            full = os.path.join(chrome_path, name)
            if os.path.isfile(full):
                return full
    return chrome_path


def launch_chrome(port=None, chrome_path=None, config_path=None):
    """启动带 CDP 的 Chrome（使用独立临时 profile，避免复用已有 session）"""
    from skill_config import get_config
    cfg = get_config(config_path)

    if chrome_path is None:
        chrome_path = cfg.chrome_path
    if port is None:
        # 从 cdp_url 提取端口
        cdp_url = cfg.cdp_url
        port = int(cdp_url.split(':')[-1].strip('/'))

    chrome_exe = _resolve_chrome_exe(chrome_path)
    if not os.path.isfile(chrome_exe):
        raise FileNotFoundError(f"Chrome 路径不存在: {chrome_path}")

    # 必须使用独立 user-data-dir，否则 Chrome 复用已有 profile 不会开启 CDP 端口
    tmp_dir = os.path.join(tempfile.gettempdir(), 'chrome_cdp_profile')
    os.makedirs(tmp_dir, exist_ok=True)

    cmd = [
        chrome_exe,
        f'--remote-debugging-port={port}',
        f'--user-data-dir={tmp_dir}',
    ]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Chrome 已启动 (port={port}, profile={tmp_dir})")


def check_cdp(port=None, timeout=15, config_path=None):
    """等待 CDP 端口就绪"""
    if port is None:
        from skill_config import get_config
        cdp_url = get_config(config_path).cdp_url
        port = int(cdp_url.split(':')[-1].strip('/'))
    url = f'http://localhost:{port}/json/version'
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url, timeout=3)
            print(f"CDP 就绪 (http://localhost:{port})")
            return True
        except Exception:
            time.sleep(1)
    print(f"CDP 启动超时 ({timeout}s)")
    return False


def main():
    # 解析 --config 参数
    config_path = None
    for i, arg in enumerate(sys.argv):
        if arg == '--config' and i + 1 < len(sys.argv):
            config_path = sys.argv[i + 1]
            break

    force = '--force' in sys.argv or '-f' in sys.argv
    auto = '--auto' in sys.argv

    if not force and not auto:
        from skill_config import get_config
        cfg = get_config(config_path)
        cdp_port = cfg.cdp_url.split(':')[-1].strip('/')
        print("=" * 50)
        print(f"即将关闭所有 Chrome 窗口并重新启动（带 CDP 端口 {cdp_port}）")
        print("请确保已保存 Chrome 中的所有工作！")
        print("=" * 50)
        resp = input("继续？(y/N): ").strip().lower()
        if resp not in ('y', 'yes'):
            print("已取消")
            return

    kill_chrome()
    launch_chrome(config_path=config_path)

    if check_cdp(config_path=config_path):
        if auto:
            print("Chrome 已就绪（--auto 模式）")
        else:
            print("\nChrome 已就绪，请在浏览器中登录并导航到目标菜单。")
    else:
        print("\n启动失败，请手动执行：")
        from skill_config import get_config
        cfg = get_config(config_path)
        print(f'"{cfg.chrome_path}" --remote-debugging-port={cfg.cdp_url.split(":")[-1].strip("/")}')



if __name__ == '__main__':
    main()
