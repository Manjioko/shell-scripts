#!/usr/bin/env python3
"""安装完整性/解释器有效性自检。末行 RESULT ...。

锁两件事(都对应线上真实故障):
  1. 发布包必须含**全部** wchat_py/*.py —— 0.2.1 的 tarball 漏了 msg.py,
     装出来后 /compact 只得到一句 "cannot import name 'msg'"(看不出缺文件)。
  2. 解释器路径失效时必须**换一个能用的**并且不抛栈 —— brew 升级删掉旧 Cellar 后,
     正在运行的进程 sys.executable 仍指着旧目录,spawn 子进程直接 FileNotFoundError
     崩栈(用户看到一长串 traceback)。
"""
import os
import subprocess
import sys
import tarfile
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "packaging", "homebrew"))
from wchat_py import cli  # noqa: E402
from wchat_py import compact_run   # noqa: E402

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


# ---- 1) 打包必须含全部模块 ----
import make_release as mr  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pkgaudit_")
tar = mr.build_tarball("0.0.0-audit", tmp)
with tarfile.open(tar) as tf:
    names = tf.getnames()
top = "wchat-0.0.0-audit/"
in_tar = {n[len(top):] for n in names}
repo_py = set()
for d, dirs, files in os.walk(os.path.join(REPO, "wchat_py")):
    dirs[:] = [x for x in dirs if x != "__pycache__"]
    for fn in files:
        if fn.endswith(".py"):
            repo_py.add(os.path.relpath(os.path.join(d, fn), REPO))
missing = sorted(repo_py - in_tar)
ck("发布包含仓库里每一个 wchat_py/*.py", not missing, "缺: " + ", ".join(missing))
ck("发布包含 msg.py(0.2.1 漏过这个)", "wchat_py/msg.py" in in_tar)
ck("发布包含 version.py", "wchat_py/version.py" in in_tar)

# 每个模块都真的能 import(缺文件/坏 import 在这一步就红,而不是等用户点 /compact)
code = ("import importlib,sys;"
        "[importlib.import_module('wchat_py.'+m) for m in sys.argv[1:]];print('ok')")
mods = sorted(os.path.basename(x)[:-3] for x in repo_py
              if os.path.dirname(x) == "wchat_py" and os.path.basename(x) != "__init__.py")
r = subprocess.run([sys.executable, "-c", code] + mods, cwd=REPO,
                   capture_output=True, text=True)
ck("每个 wchat_py 模块都能 import", r.returncode == 0 and "ok" in r.stdout,
   (r.stderr or "").strip().splitlines()[-1] if r.stderr else "")

# ---- 2) 解释器失效时的兜底 ----
_real = sys.executable
try:
    sys.executable = "/nonexistent/cellar/wchat/9.9.9/libexec/venv/bin/python"
    got = cli._runtime_fallback()
finally:
    sys.executable = _real
ck("解释器不存在时返回一个**存在**的替代", bool(got) and os.path.exists(got), repr(got))
ck("替代解释器不是那个坏路径", "9.9.9" not in got, repr(got))

# 子进程起不来时必须给一句人话,而不是抛栈
_old = sys.executable
try:
    sys.executable = "/nonexistent/python"
    # _call_llm 内部会用 _runtime_fallback,所以它应当**能**跑起来或给可读错误,
    # 绝不能抛 OSError 到调用方。
    try:
        out = cli._call_llm([{"role": "user", "content": "hi"}], "")
        ck("_call_llm 不向调用方抛 OSError", True, "")
        ck("_call_llm 返回三元组", isinstance(out, tuple) and len(out) == 3, repr(out)[:80])
    except OSError as e:
        ck("_call_llm 不向调用方抛 OSError", False, repr(e))
        ck("_call_llm 返回三元组", False, "抛异常了")
finally:
    sys.executable = _old

# ---- 3) 缺 msg 模块时,压缩失败要说"缺文件"而不是只报 import 错 ----
# 用"把 wchat_py.msg 从 sys.modules 里拿掉并塞一个占位/去掉成功入口"太脆,
# 直接在临时目录造一个缺 msg.py 的包副本更贴近真实(安装包不完整就是这个形状)。
import shutil  # noqa: E402
_iso = tempfile.mkdtemp(prefix="pkgiso_")
shutil.copytree(os.path.join(REPO, "wchat_py"), os.path.join(_iso, "wchat_py"),
                ignore=shutil.ignore_patterns("__pycache__"))
os.remove(os.path.join(_iso, "wchat_py", "msg.py"))
_probe = (
    "import io, sys\n"
    "from contextlib import redirect_stdout\n"
    "sys.path.insert(0, %r)\n"
    "from wchat_py import compact_run\n"
    "class S: messages_json = '[]'; msg_count = 0\n"
    "buf = io.StringIO()\n"
    "with redirect_stdout(buf):\n"
    "    res = compact_run.compact(S(), auto=False)\n"
    "print(repr(res)); print(buf.getvalue())\n" % _iso)
_r = subprocess.run([sys.executable, "-c", _probe], capture_output=True, text=True)
_out = (_r.stdout or "") + (_r.stderr or "")
ck("缺 msg.py 时压缩不抛异常", _r.returncode == 0, (_r.stderr or "")[-160:])
ck("缺 msg.py 时返回未改动", "(False, 0, 0, 0)" in (_r.stdout or ""), repr(_r.stdout)[:80])
ck("提示里写明缺 msg.py", "msg.py" in _out, repr(_out[-160:]))
ck("提示里给出修复命令 brew reinstall", "brew reinstall" in _out, repr(_out[-160:]))
shutil.rmtree(_iso, ignore_errors=True)

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)