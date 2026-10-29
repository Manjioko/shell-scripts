#!/usr/bin/env python3
"""把 wchat 打成 .deb（Ubuntu/Debian，自带 venv + 内置 wheel）。

## 为什么要内置 wheel

Linux 上 `pip install wchat` 走 PEP 668（externally-managed-environment）会被拦，
而依赖发行版的 `python3-openai` 又对不上（我们要 openai>=2,<3，Ubuntu 24.04 里没有）。
所以 deb 里**预置 wheel**，postinst 用 `--no-index` 离线装进 `/usr/lib/wchat/venv`：
装完即可用，运行期零联网（已实测容器内无出网，见 README）。

## 布局（与 brew 的 libexec 同形，`runtime._runtime_fallback()` 天然覆盖）

    /usr/lib/wchat/wchat              入口脚本
    /usr/lib/wchat/wchat_py/          包
    /usr/lib/wchat/configs/           模板 + ctmodel.py
    /usr/lib/wchat/skills/            随包技能
    /usr/lib/wchat/wheels/            离线 wheel
    /usr/lib/wchat/venv/              postinst 建（不属于包内容，purge 时删）
    /usr/bin/wchat  /usr/bin/ctmodel  wrapper（exec 到 venv 的 python）

venv **不能**打进 deb：venv 里的 `pyvenv.cfg` 和脚本 shebang 都是绝对路径，
而且体积/字节码会随 Python 补丁版本漂。由 postinst 现场生成才可靠。

用法（在 Linux 容器里跑，需要 dpkg-deb）：
    python3 packaging/linux/make_deb.py --version 0.2.7 --wheelhouse /wheelhouse
"""
import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "packaging", "homebrew"))

# 与 brew 包**共用同一份文件清单与剔除规则**:两份实现迟早漂(而且漂了是"deb 里有、
# brew 里没有"这种最难察觉的形态)。这里 import 它，不复制。
from make_release import INCLUDE, CONFIG, CONFIG_PY, MCP_EXAMPLE, SKIP_DIRS, SKIP_NAMES, SKIP_SUFFIX  # noqa: E402

LIBDIR = "usr/lib/wchat"
BINDIR = "usr/bin"

CONTROL = """Package: wchat
Version: {ver}
Section: utils
Priority: optional
Architecture: {arch}
Depends: python3 (>= 3.9), python3-venv, ca-certificates
Recommends: git, curl, jq
Installed-Size: {size}
Maintainer: wchat maintainers <noreply@example.com>
Homepage: https://github.com/Manjioko/shell-scripts
Description: Terminal AI assistant (Python, bundled venv)
 A terminal-based AI coding assistant. This package ships the runtime and
 all Python wheels, so it works offline right after install.
 .
 Includes: wchat (main entry), ctmodel (model-profile switcher).
"""

POSTINST = r"""#!/bin/sh
# 建 venv + 离线装依赖。**必须 --no-index**:运行期不允许联网(实测容器无出网),
# 而且"装完就能用"是硬要求 —— 联网 pip 会让 apt install 在某些网络下挂死一分钟。
set -e
LIB=/usr/lib/wchat
PY=/usr/bin/python3

if [ ! -x "$LIB/venv/bin/python" ]; then
    if ! "$PY" -c 'import venv' 2>/dev/null; then
        echo "wchat: 缺少 python3-venv,请先安装该包(apt install python3-venv)" >&2
        exit 1
    fi
    "$PY" -m venv "$LIB/venv" || {
        echo "wchat: 创建 venv 失败($LIB/venv)" >&2
        exit 1
    }
fi

# 幂等:已装齐就不再跑 pip(apt 重装/升级时会再调一次 postinst)
if ! "$LIB/venv/bin/python" -c 'import openai, httpx, prompt_toolkit, rich, PIL' 2>/dev/null; then
    "$LIB/venv/bin/pip" install -q --no-index --find-links="$LIB/wheels" \
        --disable-pip-version-check --no-warn-script-location \
        -r "$LIB/requirements.txt" || {
        echo "wchat: 离线安装依赖失败(检查 $LIB/wheels 是否完整)" >&2
        exit 1
    }
fi

# 清字节码:venv 里的 .pyc 会随 Python 补丁版本失效,留着只是占地方
find "$LIB/venv" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
exit 0
"""

PRERM = r"""#!/bin/sh
set -e
# 升级/卸载前删 venv:venv 里的绝对路径与 shebang 绑死当前 Python,
# 留着会在解释器升级后变成"能执行但 import 失败"的僵尸态。
if [ "$1" = "upgrade" ] || [ "$1" = "remove" ]; then
    rm -rf /usr/lib/wchat/venv
fi
exit 0
"""

WRAPPER = """#!/bin/sh
# 入口包装:解释器由这里指定(与 brew Formula 生成的 bin/wchat 同形)。
# 不用 `#!/usr/bin/env python3`:那会用系统 python,而依赖装在 venv 里。
exec /usr/lib/wchat/venv/bin/python /usr/lib/wchat/wchat "$@"
"""

CTMODEL_WRAPPER = """#!/bin/sh
# 兼容壳:实现只有一个(wchat_py/setup.cmd_model),这里直接转过去。
exec /usr/lib/wchat/venv/bin/python /usr/lib/wchat/wchat model "$@"
"""


def _skip(path):
    """与 make_release 同一套剔除规则(字节码/编辑器垃圾/版本控制)。"""
    rel = os.path.relpath(path, ROOT)
    for part in rel.split(os.sep):
        if part in SKIP_DIRS:
            return True
        if part in SKIP_NAMES or part.endswith(SKIP_SUFFIX):
            return True
        if part.startswith(".#"):
            return True
    return False


def _walk(top):
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in sorted(filenames):
            p = os.path.join(dirpath, f)
            if not _skip(p):
                yield p


def _copy_tree(src, dst):
    """按"文件权限归一"拷贝:可执行保持 0755,其余 0644(deb 里不带开发机 umask)。"""
    os.makedirs(dst, exist_ok=True)
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel = os.path.relpath(dirpath, src)
        target = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target, exist_ok=True)
        for f in sorted(filenames):
            p = os.path.join(dirpath, f)
            if _skip(p):
                continue
            out = os.path.join(target, f)
            shutil.copy2(p, out)
            mode = 0o755 if (os.stat(p).st_mode & 0o111) else 0o644
            os.chmod(out, mode)


def _write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, mode)


def _dir_size(path):
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            try:
                total += os.stat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


def build(version, wheelhouse, out_dir, arch):
    if not os.path.isdir(wheelhouse):
        sys.exit("✗ wheelhouse 不存在:%s(先 pip download --only-binary=:all: -r requirements.txt -d 它)" % wheelhouse)
    wheels = sorted(f for f in os.listdir(wheelhouse) if f.endswith(".whl"))
    if not wheels:
        sys.exit("✗ wheelhouse 里没有 .whl:%s" % wheelhouse)

    stage = tempfile.mkdtemp(prefix="wchat-deb-")
    try:
        lib = os.path.join(stage, LIBDIR)
        binp = os.path.join(stage, BINDIR)
        os.makedirs(lib, exist_ok=True)
        os.makedirs(binp, exist_ok=True)

        # —— 与 brew 包相同的载荷 ——
        for name in INCLUDE:
            src = os.path.join(ROOT, name)
            if not os.path.exists(src):
                continue
            if os.path.isdir(src):
                _copy_tree(src, os.path.join(lib, name))
            else:
                # 注意:清单里既有目录也有**单个文件**(如 docs/brew-package.md),
                # 单个文件要自己建父目录 —— 直接 copy2 会 FileNotFoundError。
                dest = os.path.join(lib, name)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(src, dest)
                os.chmod(dest,
                         0o755 if (os.stat(src).st_mode & 0o111) else 0o644)
        for rel in (CONFIG, CONFIG_PY, MCP_EXAMPLE):
            f = os.path.join(ROOT, rel)
            if os.path.exists(f):
                dest = os.path.join(lib, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(f, dest)
                os.chmod(dest, 0o644)

        # —— 离线 wheel ——
        wh = os.path.join(lib, "wheels")
        os.makedirs(wh, exist_ok=True)
        for w in wheels:
            shutil.copy2(os.path.join(wheelhouse, w), os.path.join(wh, w))
            os.chmod(os.path.join(wh, w), 0o644)

        _write(os.path.join(binp, "wchat"), WRAPPER, 0o755)
        _write(os.path.join(binp, "ctmodel"), CTMODEL_WRAPPER, 0o755)

        # —— DEBIAN 控制区 ——
        debian = os.path.join(stage, "DEBIAN")
        os.makedirs(debian, exist_ok=True)
        _write(os.path.join(debian, "control"),
               CONTROL.format(ver=version, arch=arch,
                              size=_dir_size(stage) // 1024 or 1))
        _write(os.path.join(debian, "postinst"), POSTINST, 0o755)
        _write(os.path.join(debian, "prerm"), PRERM, 0o755)

        # 统一的属主与时间戳:同源码同版本两次构建 sha256 一致(便于核对)
        for dirpath, dirnames, filenames in os.walk(stage):
            for n in dirnames + filenames:
                p = os.path.join(dirpath, n)
                os.chown(p, 0, 0)
                os.utime(p, (1700000000, 1700000000))
        os.chown(stage, 0, 0)
        os.utime(stage, (1700000000, 1700000000))

        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "wchat_%s_%s.deb" % (version, arch))
        r = subprocess.run(["dpkg-deb", "--root-owner-group", "--build", stage, out],
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.stderr.write(r.stdout + r.stderr)
            sys.exit("✗ dpkg-deb 失败")
        return out
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--wheelhouse", default="/wheelhouse")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--arch", default=None,
                    help="默认按 dpkg 的架构(dpkg --print-architecture)")
    a = ap.parse_args()
    arch = a.arch
    if not arch:
        r = subprocess.run(["dpkg", "--print-architecture"], capture_output=True, text=True)
        arch = (r.stdout or "").strip() or "arm64"
    deb = build(a.version, a.wheelhouse, a.out_dir, arch)
    size = os.path.getsize(deb)
    print("deb:    %s" % deb)
    print("size:   %.1f MB" % (size / 1024.0 / 1024.0))
    print("sha256: %s" % sha256(deb))
    print("arch:   %s" % arch)
    print()
    print("安装(容器/真机):dpkg -i %s" % os.path.basename(deb))
    print("卸载:            dpkg -r wchat   (venv 由 prerm 删除)")


if __name__ == "__main__":
    main()