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

    # python 组件(输入框 + markdown)
    if run([sys.executable, "-c", "import prompt_toolkit, rich"]) == 0:
        ok("prompt_toolkit + rich 已就绪")
    else:
        step("pip install --user prompt_toolkit rich")
        if pip_install("prompt_toolkit", "rich"):
            ok("prompt_toolkit + rich 安装完成")
        else:
            fail("安装失败:请手动执行 pip3 install --user prompt_toolkit rich"); rc = 1

    # 外置命令
    need = {"jq": "jq", "curl": "curl"}
    rec = {"w3m": "w3m", "ffmpeg": "ffmpeg"}
    for cmd, pkg in need.items():
        if have(cmd):
            ok("%s 已就绪" % cmd)
        else:
            step("安装 %s" % pkg)
            if install_bin([pkg], o):
                ok("%s 安装完成" % cmd)
            else:
                fail("缺 %s,请手动安装" % cmd); rc = 1
    for cmd, pkg in rec.items():
        if have(cmd):
            ok("%s 已就绪(可选)" % cmd)
        else:
            warn("可选组件 %s 未安装(网页解析/图片转述会降级)" % cmd)

    if rc == 0:
        ok("全部就绪。运行: ./wchat -h")
    return rc


if __name__ == "__main__":
    sys.exit(main())

