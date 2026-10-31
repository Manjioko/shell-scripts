#!/usr/bin/env python3
"""运行时自举(bootstrap)的闸门。末行 RESULT ...。

这一层是"首次安装必经之路",但在本次修改前**几乎零覆盖**(只有 brew_dist 里一条
"NO_BOOTSTRAP 不落盘")。下面每条都对应一个实测出来的真缺陷:

1. `.ready` 存在但 `venv/bin/python` 是断链(base python 被升级/删除 —— brew 升级
   python 后的真实形状)→ 旧实现 `_ready()` 返回 True、`_re_exec()` 内部 isfile 失败
   **返回 False 却被忽略**,于是每次启动都静默什么都不做,**永远不会自愈**。
2. `_with_lock` 的 `except Exception: return fn()` 把"锁机制不可用"与"fn 自己失败"
   混为一谈 → pip 中途失败会在**无锁**状态下再跑一次(实测 fn 被调用 2 次)。
3. `_deps_ok` 只判"模块找得到",不判版本区间 → 装着 openai 1.x 也判"齐全",
   于是不建 venv,跑起来撞 2.x 的 API 差异,报错位置离真因很远。
4. pip 失败只打一行"⚠ 依赖安装失败"就继续用系统 Python → 后面在很远的地方
   ImportError。首启安装失败必须**停在原地**并给可复制的命令。

全部离线:不跑真 pip、不联网(子进程一律打桩)。
"""
import os
import shutil
import stat
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from wchat_py import bootstrap as B  # noqa: E402

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


class Sandbox(object):
    """临时 CT_HOME/WCHAT_VENV,并在退出时还原所有被替换的函数与环境。"""

    def __init__(self):
        self.d = tempfile.mkdtemp(prefix="boot_", dir=REPO)
        self.venv = os.path.join(self.d, "venv")
        self.saved = {}

    def __enter__(self):
        self.saved_env = {k: os.environ.get(k) for k in
                          ("CT_HOME", "WCHAT_VENV", "WCHAT_NO_BOOTSTRAP",
                           "WCHAT_BOOTSTRAPPED", "WCHAT_BOOTSTRAP_FORCE")}
        os.environ["CT_HOME"] = os.path.join(self.d, "ct")
        os.environ["WCHAT_VENV"] = self.venv
        for k in ("WCHAT_NO_BOOTSTRAP", "WCHAT_BOOTSTRAPPED", "WCHAT_BOOTSTRAP_FORCE"):
            os.environ.pop(k, None)
        os.makedirs(self.venv, exist_ok=True)
        # ensure_runtime 在"当前进程已在某个 venv 里"时会直接 return(那是正确行为:
        # 依赖就在这个 venv 里)。但本闸门要测的是**重建/自愈那条路**,必须让它以为
        # 自己在系统解释器上跑 —— 否则测试在 venv 里跑(Linux 打包形态 /work/.venv
        # 就是这样)会三条断言全红,而产品行为完全正确。实测:Ubuntu 容器 28/31。
        self.saved_prefix = (sys.prefix, sys.base_prefix)
        sys.prefix = sys.base_prefix
        return self

    def __exit__(self, *a):
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for name, fn in self.saved.items():
            setattr(B, name, fn)
        if getattr(self, "saved_prefix", None):
            sys.prefix, sys.base_prefix = self.saved_prefix
        shutil.rmtree(self.d, ignore_errors=True)

    def stub(self, name, fn):
        if name not in self.saved:
            self.saved[name] = getattr(B, name, None)
        setattr(B, name, fn)

    def make_python(self, ok=True):
        """在 venv/bin 里造一个 python(ok=False 时是指向不存在目标的符号链接)。"""
        b = os.path.join(self.venv, "bin")
        os.makedirs(b, exist_ok=True)
        p = os.path.join(b, "python")
        if os.path.lexists(p):
            os.unlink(p)
        if ok:
            with open(p, "w") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(p, 0o755)
        else:
            os.symlink("/nonexistent/python9.9", p)
        return p


# ---------------- 1) 断链 venv 必须被判成"不健康" ----------------
print("=== 1. 断链检测 ===")
with Sandbox() as sb:
    sb.make_python(ok=False)
    with open(os.path.join(sb.venv, ".ready"), "w") as f:
        f.write(B._fingerprint())
    ck("venv python 断链时 _venv_ok 为 False", B._venv_ok(sb.venv) is False)
    ck("断链时 _ready 为 False(不能只看 marker)", B._ready(sb.venv) is False)

with Sandbox() as sb:
    sb.make_python(ok=True)
    with open(os.path.join(sb.venv, ".ready"), "w") as f:
        f.write(B._fingerprint())
    ck("健康 venv:_venv_ok 为 True", B._venv_ok(sb.venv) is True)
    ck("健康 venv:_ready 为 True", B._ready(sb.venv) is True)

    with open(os.path.join(sb.venv, ".ready"), "w") as f:
        f.write("old-fingerprint")
    ck("marker 过期的 _ready 为 False", B._ready(sb.venv) is False)

# ---------------- 2) 断链要自愈:重建而不是静默放弃 ----------------
print("=== 2. 断链自愈 ===")
with Sandbox() as sb:
    sb.make_python(ok=False)
    with open(os.path.join(sb.venv, ".ready"), "w") as f:
        f.write(B._fingerprint())
    calls = {"install": 0, "reexec": 0}

    def fake_install(venv):
        calls["install"] += 1
        sb.make_python(ok=True)
        with open(os.path.join(venv, ".ready"), "w") as f:
            f.write(B._fingerprint())
        return True

    sb.stub("_install", fake_install)
    sb.stub("_re_exec", lambda v: (calls.__setitem__("reexec", calls["reexec"] + 1) or True))
    sb.stub("_deps_ok", lambda: False)
    B.ensure_runtime()
    ck("断链 -> 触发重建", calls["install"] == 1, "install=%d" % calls["install"])
    ck("断链 -> 重建后 re-exec", calls["reexec"] >= 1, "reexec=%d" % calls["reexec"])
    os.environ.pop("WCHAT_BOOTSTRAPPED", None)

# 健康 venv:不要白重建
with Sandbox() as sb:
    sb.make_python(ok=True)
    with open(os.path.join(sb.venv, ".ready"), "w") as f:
        f.write(B._fingerprint())
    calls = {"install": 0, "reexec": 0}
    sb.stub("_install", lambda v: (calls.__setitem__("install", calls["install"] + 1) or True))
    sb.stub("_re_exec", lambda v: (calls.__setitem__("reexec", calls["reexec"] + 1) or True))
    sb.stub("_deps_ok", lambda: False)
    B.ensure_runtime()
    ck("健康 venv 不重建", calls["install"] == 0, "install=%d" % calls["install"])
    ck("健康 venv 直接 re-exec", calls["reexec"] == 1, "reexec=%d" % calls["reexec"])
    os.environ.pop("WCHAT_BOOTSTRAPPED", None)

# ---------------- 3) 锁:fn 失败不许无锁重跑 ----------------
print("=== 3. 锁语义 ===")
with Sandbox() as sb:
    n = {"c": 0}

    def boom():
        n["c"] += 1
        raise RuntimeError("模拟 pip 中途失败")

    try:
        B._with_lock(boom)
        ck("fn 异常要向上抛", False, "异常被吞掉了")
    except RuntimeError:
        ck("fn 异常要向上抛", True)
    ck("fn 只被调用一次(不许无锁重跑)", n["c"] == 1, "调用 %d 次" % n["c"])

with Sandbox() as sb:
    n = {"c": 0}

    def once():
        n["c"] += 1
        return "ok"

    # 模拟"锁机制不可用"(fcntl 导入失败):必须退化成只跑一次,而不是跑两次
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "fcntl":
            raise ImportError("模拟无 fcntl")
        return real_import(name, *a, **k)

    builtins.__import__ = fake_import
    try:
        r = B._with_lock(once)
    finally:
        builtins.__import__ = real_import
    ck("锁不可用时仍返回结果", r == "ok", repr(r))
    ck("锁不可用时只跑一次", n["c"] == 1, "调用 %d 次" % n["c"])

# ---------------- 4) 版本判据 ----------------
print("=== 4. 版本区间 ===")
ck("_ver_ok: 2.48 满足 >=2,<3", B._ver_ok("2.48.0", ">=2,<3") is True)
ck("_ver_ok: 2.48 不满足 <2", B._ver_ok("2.48.0", "<2") is False)
ck("_ver_ok: 1.30 不满足 >=2,<3", B._ver_ok("1.30.0", ">=2,<3") is False)
ck("_ver_ok: 空区间恒真", B._ver_ok("0.1", "") is True)
ck("_ver_ok: 3.0 满足 >=3", B._ver_ok("3.0.0", ">=3") is True)
ck("_ver_ok: 3.0 不满足 >=3.1", B._ver_ok("3.0.0", ">=3.1") is False)
ck("_ver_ok: == 精确匹配", B._ver_ok("2.0", "==2.0") is True)

# _deps_ok 必须看版本,不能只看 find_spec
with Sandbox() as sb:
    def fake_ver(name):
        return {"openai": "1.30.0", "prompt_toolkit": "3.0.52",
                "rich": "15.0.0", "httpx": "0.28.1"}.get(name, "0.0")

    sb.stub("_installed_version", fake_ver)
    ck("装着 openai 1.x 时 _deps_ok 为 False(要建 venv)", B._deps_ok() is False)
    ck("缺的正是 openai", B._missing_deps() == ["openai"], repr(B._missing_deps()))

# extras 语法(httpx[socks])必须仍被解析成"必需的 httpx"。
# 不认 extras 会让整行被跳过 → httpx 从必需依赖里消失 → 缺它也不建 venv,
# 表现为"装了 wchat 但 httpx 缺席也报齐全"(比不写还糟)。
_names = [n for n, _s in B._requirements()]
ck("requirements 里的 httpx[socks] 解析成 httpx", "httpx" in _names, repr(_names))
ck("extras 不带进模块名", not any("[" in n for n in _names), repr(_names))
ck("httpx 被列为必需依赖", "httpx" in B._required(), repr(B._required()))

# ---------------- 5) 安装失败必须停住,不许静默降级 ----------------
print("=== 5. 安装失败 ===")
import subprocess as _sp  # noqa: E402


def _fake_run(rc, seen=None):
    def run(argv, **kw):
        if seen is not None:
            seen["timeout"] = kw.get("timeout")
            seen["argv"] = list(argv)

        class R:
            returncode = rc
        return R()
    return run


# 5a) venv 本身 OK,只有 pip 失败 —— 这是最常见的真实形态(断网/镜像挂了)
with Sandbox() as sb:
    sb.make_python(ok=True)
    seen = {}
    real_run = _sp.run
    _sp.run = _fake_run(1, seen)
    try:
        try:
            B._install(sb.venv)
            ck("pip 失败要抛错(不许静默继续)", False, "_install 正常返回了")
        except SystemExit as e:
            ck("pip 失败要抛错(不许静默继续)", e.code not in (0, None), "code=%r" % (e.code,))
    finally:
        _sp.run = real_run
    ck("pip 调用带 timeout(断网不会卡死)", seen.get("timeout") not in (None, 0),
       "timeout=%r" % (seen.get("timeout"),))
    ck("失败时不写 .ready(否则下次被当成装好了)",
       not os.path.isfile(os.path.join(sb.venv, ".ready")))

# 5b) pip 超时要给出明确原因(不是栈)
with Sandbox() as sb:
    sb.make_python(ok=True)
    real_run = _sp.run

    def timeout_run(argv, **kw):
        raise _sp.TimeoutExpired(argv, kw.get("timeout") or 1)
    _sp.run = timeout_run
    try:
        try:
            B._install(sb.venv)
            ck("pip 超时要抛错", False, "_install 正常返回了")
        except SystemExit as e:
            ck("pip 超时要抛错", e.code not in (0, None), "code=%r" % (e.code,))
    finally:
        _sp.run = real_run
    ck("超时也不写 .ready", not os.path.isfile(os.path.join(sb.venv, ".ready")))

# 5c) 建 venv 本身失败(基础解释器不可用)
with Sandbox() as sb:
    sb.make_python(ok=False)
    real_run = _sp.run
    _sp.run = _fake_run(1)
    try:
        try:
            B._install(sb.venv)
            ck("建 venv 失败要抛错", False, "_install 正常返回了")
        except SystemExit as e:
            ck("建 venv 失败要抛错", e.code not in (0, None), "code=%r" % (e.code,))
    finally:
        _sp.run = real_run

# ---------------- 6) 兼容与逃生口 ----------------
print("=== 6. 逃生口与解释器回退 ===")
with Sandbox() as sb:
    sb.make_python(ok=False)
    os.environ["WCHAT_NO_BOOTSTRAP"] = "1"
    calls = {"install": 0}
    sb.stub("_install", lambda v: (calls.__setitem__("install", calls["install"] + 1) or True))
    sb.stub("_deps_ok", lambda: False)
    B.ensure_runtime()
    ck("NO_BOOTSTRAP=1 不建 venv(既有语义保留)", calls["install"] == 0,
       "install=%d" % calls["install"])

ck("_base_python 给出的路径存在且可执行",
   bool(B._base_python()) and os.path.isfile(B._base_python()),
   repr(B._base_python()))
# sys.executable 指向不存在的文件时要能换一个(brew 升级后的真实形状)
with Sandbox() as sb:
    real_exe = sys.executable
    sys.executable = "/nonexistent/python9.9"
    try:
        picked = B._base_python()
    finally:
        sys.executable = real_exe
    ck("sys.executable 失效时会另找一个", os.path.isfile(picked), repr(picked))

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)