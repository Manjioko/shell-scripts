#!/usr/bin/env python3
"""wchat 依赖安装脚本(对应原 setup.sh)。

检测平台(macOS/Linux/WSL2),安装缺失组件;已装的跳过。
用法: python3 setup.py
"""
import os
import platform
import shutil
import subprocess
import sys


def step(s):
    print("\033[2m│\033[0m %s" % s)


def ok(s):
    print("\033[2m│\033[0m ✅ %s" % s)


def warn(s):
    print("\033[2m│\033[0m ⚠️  %s" % s)


def fail(s):
    print("\033[2m│\033[0m ❌ %s" % s)


def have(x):
    return shutil.which(x) is not None


def run(argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True).returncode
    except OSError:
        return 127


def detect_os():
    if platform.system() == "Darwin":
        return "macos"
    if os.environ.get("WSL_DISTRO_NAME"):
        return "wsl"
    try:
        with open("/proc/version", encoding="utf-8", errors="replace") as f:
            if "microsoft" in f.read().lower():
                return "wsl"
    except OSError:
        pass
    return "linux"


def pip_install(*pkgs):
    for extra in ([], ["--user"], ["--user", "--break-system-packages"]):
        if run([sys.executable, "-m", "pip", "install"] + extra + list(pkgs)) == 0:
            return True
    return False


def install_bin(names, os_name):
    if os_name == "macos":
        return run(["brew", "install"] + names) == 0
    if have("apt-get"):
        return run(["sudo", "apt-get", "install", "-y"] + names) == 0
    if have("dnf"):
        return run(["sudo", "dnf", "install", "-y"] + names) == 0
    return False


def main():
    rc = 0
    o = detect_os()
    step("平台: %s · python3: %s" % (o, sys.executable))
    if sys.version_info < (3, 9):
        fail("需要 Python 3.9+,当前 %s" % sys.version.split()[0]); return 1

    # Python 组件(输入框 / markdown / 图片缩放)
    if run([sys.executable, "-c", "import prompt_toolkit, rich, PIL"]) == 0:
        ok("prompt_toolkit + rich + pillow 已就绪")
    else:
        step("pip install --user prompt_toolkit rich pillow")
        if pip_install("prompt_toolkit", "rich", "pillow"):
            ok("Python 组件安装完成")
        else:
            fail("安装失败:请手动执行 pip3 install --user prompt_toolkit rich pillow"); rc = 1

    # 外部命令:git 用于回滚点/版本信息;npx 仅 MCP 需要(可选)
    for cmd, why in (("git", "回滚点/版本信息"), ("npx", "MCP(可选)")):
        if have(cmd):
            ok("%s 已就绪(%s)" % (cmd, why))
        else:
            warn("未找到 %s(%s,缺它只影响对应功能)" % (cmd, why))

    # 旧外部渲染/处理工具:Python 版已不依赖,仅在跑 tests/legacy 时需要
    for cmd in ("jq", "curl", "w3m", "ffmpeg"):
        if have(cmd):
            ok("%s 存在(仅测试/兼容用,产品不再依赖)" % cmd)
        else:
            warn("%s 未安装(产品不需要;仅 tests/ 的旧基线可能用到)" % cmd)

    if rc == 0:
        ok("全部就绪。运行: ./wchat -h")
    return rc


if __name__ == "__main__":
    sys.exit(main())