#!/usr/bin/env python3
"""运行时自举:系统 Python 依赖齐全就直用;否则创建/复用 venv 并 re-exec。

设计(每条都对应一次真实事故,改动前先读下面的"为什么"):

  · 常态零安装:用 importlib.util.find_spec 探测(不 import,避免启动变慢);
  · **版本也要判**:只看"模块找得到"会把 openai 1.x 当成"齐全",于是不建 venv,
    跑起来撞 2.x 的 API 差异,报错位置离真因十万八千里。版本区间取自
    requirements.txt(单一真源),不在这里抄第二份。
  · venv 放 ~/.ct/venv(按用户共享),多 tty 共用;首次创建用 fcntl.flock 串行化;
  · `.ready` 记 requirements 哈希 + Python 版本;**并且校验 venv 里的 python 真的
    能执行** —— 只信 marker 的话,base python 被 brew 升级删除后,每次启动都会
    "以为装好了 → re-exec 失败 → 静默什么都不做",永远不会自愈。
  · 坏掉的 venv 一律删除重建(在锁内),不试图修。
  · 安装失败**停下并给可复制的命令**,不静默退回系统 Python —— 回退只会在很远的地方
    变成 ImportError。
  · WCHAT_NO_BOOTSTRAP=1 完全跳过(测试/CI 用);
  · Pillow 视为可选:缺它只影响图片缩放(会退回 sips),不因此建 venv。
"""
import hashlib
import importlib.metadata
import importlib.util
import os
import re
import shutil
import subprocess
import sys

# requirements.txt 是唯一的依赖真源;这里不再抄一份名字列表。
_OPTIONAL = ("pillow",)          # 可选:缺了不建 venv(图片缩放会退回 sips)
_MARK = ".ready"
_SPEC_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*([<>=!~].*)?$")


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _venv_dir():
    return os.environ.get("WCHAT_VENV") or os.path.join(
        os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME") or "", ".ct"),
        "venv")


def _requirements():
    """requirements.txt -> [(名字, 版本区间)]。读不到就返回空表(交由调用方降级)。"""
    path = os.path.join(_repo_root(), "requirements.txt")
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.split("#", 1)[0].strip()
                if not line or line.startswith("-"):
                    continue
                m = _SPEC_RE.match(line)
                if m:
                    out.append((m.group(1), (m.group(2) or "").strip()))
    except OSError:
        pass
    return out


def _required():
    return [n for n, _s in _requirements() if n.lower() not in _OPTIONAL]


def _parse_ver(v):
    """'2.48.0' -> (2,48,0)。非数字段落截断,够用于 >=/</== 这类简单区间。"""
    parts = []
    for seg in str(v or "").split("."):
        m = re.match(r"^(\d+)", seg)
        if not m:
            break
        parts.append(int(m.group(1)))
    return tuple(parts) or (0,)


def _ver_ok(version, spec):
    """判断 version 是否满足 spec(支持逗号分隔的 >=,<=,>,<,==)。空 spec 恒真。

    只用简单比较,不实现完整 PEP 440 —— 依赖表里只有这几种形态,而多写一个
    半吊子的版本解析器比不写更危险。
    """
    ver = _parse_ver(version)
    for piece in (spec or "").split(","):
        piece = piece.strip()
        if not piece:
            continue
        m = re.match(r"^(>=|<=|==|>|<)\s*(.+)$", piece)
        if not m:
            continue                 # 不认识的算子(如 ~=)按不约束处理,不误拒
        op, want = m.group(1), _parse_ver(m.group(2))
        n = max(len(ver), len(want))
        a = ver + (0,) * (n - len(ver))
        b = want + (0,) * (n - len(want))
        if op == ">=" and not a >= b:
            return False
        if op == "<=" and not a <= b:
            return False
        if op == ">" and not a > b:
            return False
        if op == "<" and not a < b:
            return False
        if op == "==" and not a == b:
            return False
    return True


def _installed_version(name):
    """已安装版本;没装/查不到返回空串(不抛)。"""
    for cand in (name, name.replace("_", "-"), name.replace("-", "_")):
        try:
            return importlib.metadata.version(cand)
        except Exception:
            continue
    return ""


def _missing_deps():
    """返回缺失或版本不符的依赖名列表(必需项)。"""
    bad = []
    for name, spec in _requirements():
        if name.lower() in _OPTIONAL:
            continue
        try:
            if importlib.util.find_spec(name) is None:
                bad.append(name)
                continue
        except Exception:
            bad.append(name)
            continue
        v = _installed_version(name)
        if v and not _ver_ok(v, spec):
            bad.append(name)
    return bad


def _deps_ok():
    try:
        return not _missing_deps()
    except Exception:
        return False


def _fingerprint():
    req = os.path.join(_repo_root(), "requirements.txt")
    try:
        with open(req, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        h = "noreq"
    return "%s-py%d.%d" % (h, sys.version_info.major, sys.version_info.minor)


def _venv_python(venv):
    return os.path.join(venv, "bin", "python")


def _venv_ok(venv):
    """venv 里的 python **真的能执行**吗。

    必须是 isfile + X_OK:brew 升级 python 后留下的符号链接是"存在但指向不存在",
    `os.path.exists` / `os.path.isfile` 对断链都返回 False —— 这正是要判的。
    """
    py = _venv_python(venv)
    return bool(py) and os.path.isfile(py) and os.access(py, os.X_OK)


def _ready(venv):
    """marker 匹配 **且** venv 真的可用。只满足一半就重装,不许"以为装好了"。"""
    try:
        with open(os.path.join(venv, _MARK), encoding="utf-8") as f:
            if f.read().strip() != _fingerprint():
                return False
    except OSError:
        return False
    return _venv_ok(venv)


def _base_python():
    """造 venv 用的基础解释器。

    sys.executable 可能指向已被删除的文件(brew 升级删旧 Cellar 后的真实形状),
    这时 `python -m venv` 必然失败,所以要另找一个能用的。
    """
    exe = sys.executable
    if exe and os.path.isfile(exe) and os.access(exe, os.X_OK):
        return exe
    for cand in (shutil.which("python3"), shutil.which("python"),
                 "/usr/bin/python3", "/opt/homebrew/bin/python3"):
        if cand and os.path.isfile(cand):
            return cand
    return exe or "python3"


def _log(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _fail(msg, venv=None):
    """首启安装失败:停下来说清楚,不要静默退回系统 Python。

    "继续跑"看起来更友好,实际是把一个**立刻可修**的问题推迟成一个
    在很远的地方才爆的 ImportError —— 那时用户已经不知道是自己环境的问题。
    """
    _log("❌ %s" % msg)
    if venv:
        req = os.path.join(_repo_root(), "requirements.txt")
        _log("   手动装一次试试:")
        _log("     %s -m pip install -r %s" % (_venv_python(venv), req))
        _log("   仍然不行:删掉重来 `rm -rf %s` 后重启 wchat" % venv)
    raise SystemExit(1)


def _re_exec(venv):
    py = _venv_python(venv)
    if not _venv_ok(venv):
        return False
    os.environ["WCHAT_BOOTSTRAPPED"] = "1"
    argv = [py, os.path.abspath(sys.argv[0])] + sys.argv[1:]
    try:
        os.execv(py, argv)
    except OSError as e:
        _log("⚠ venv 启动失败(%s),回退系统 Python" % e)
        return False


def _rm_venv(venv):
    """坏 venv 直接删掉重建。不试图修:半个损坏的 venv 比没有更难诊断。"""
    try:
        shutil.rmtree(venv, ignore_errors=True)
    except Exception:
        pass


def _pip_timeout():
    try:
        return int(os.environ.get("WCHAT_PIP_TIMEOUT") or 600)
    except ValueError:
        return 600


def _install(venv):
    """创建 venv 并安装依赖;调用方须已持有锁。成功返回 True,失败抛 SystemExit。"""
    py = _venv_python(venv)
    if not _venv_ok(venv):
        base = _base_python()
        _log("📦 首次运行:创建虚拟环境 %s …" % venv)
        try:
            r = subprocess.run([base, "-m", "venv", venv], timeout=_pip_timeout())
        except (OSError, subprocess.TimeoutExpired) as e:
            _fail("创建 venv 失败(%s)" % e, None)
            return False
        if r.returncode != 0 or not _venv_ok(venv):
            _fail("创建 venv 失败(基础解释器 %s 可能不可用)" % base, None)
            return False
    req = os.path.join(_repo_root(), "requirements.txt")
    _log("📦 安装依赖(仅首次)…")
    try:
        r = subprocess.run([py, "-m", "pip", "install", "-q", "-r", req],
                           timeout=_pip_timeout())
    except subprocess.TimeoutExpired:
        _fail("安装依赖超时(%ss)" % _pip_timeout(), venv)
        return False
    except OSError as e:
        _fail("跑不了 pip(%s)" % e, venv)
        return False
    if r.returncode != 0:
        _fail("依赖安装失败(pip rc=%s)" % r.returncode, venv)
        return False
    try:
        with open(os.path.join(venv, _MARK), "w", encoding="utf-8") as f:
            f.write(_fingerprint())
    except OSError:
        pass
    return True


def _with_lock(fn):
    """拿锁执行 fn。**fn 自己的异常不许被当成"锁不可用"而重跑。**

    旧实现写的是 `except Exception: return fn()`,于是 pip 中途失败会在**无锁**
    状态下再跑一次(实测 fn 被调用 2 次);两个进程同时首启时第二次就绕过了串行化。
    这里把"锁机制不可用"和"fn 失败"彻底分开:前者退化成不加锁跑一次,后者原样抛出。
    """
    lock = _venv_dir() + ".lock"
    lf = None
    fcntl_mod = None
    try:
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        import fcntl as fcntl_mod          # 非 POSIX 平台没有它
        lf = open(lock, "w")
        fcntl_mod.flock(lf, fcntl_mod.LOCK_EX)
    except Exception:
        # 锁机制不可用(无 fcntl / 目录不可写):退化为 + 只跑一次
        if lf is not None:
            try:
                lf.close()
            except Exception:
                pass
        lf = None
    try:
        return fn()
    finally:
        if lf is not None:
            try:
                fcntl_mod.flock(lf, fcntl_mod.LOCK_UN)
            except Exception:
                pass
            try:
                lf.close()
            except Exception:
                pass


def ensure_runtime():
    if os.environ.get("WCHAT_NO_BOOTSTRAP") == "1":
        return
    if os.environ.get("WCHAT_BOOTSTRAPPED") == "1":
        return
    # 已在 venv 中 / 系统依赖齐全 -> 直跑
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return
    if _deps_ok() and os.environ.get("WCHAT_BOOTSTRAP_FORCE") != "1":
        return
    venv = _venv_dir()
    # marker 说好、但 venv 起不来 -> **不要 return**,落到下面重建。
    if _ready(venv) and _re_exec(venv):
        return

    def _do():
        if _ready(venv) and _re_exec(venv):     # 等锁期间别人已装好
            return
        if os.path.isdir(venv) and not _venv_ok(venv):
            _log("♻️ 虚拟环境已失效(多半是 Python 被升级),重建 %s" % venv)
            _rm_venv(venv)
        if _install(venv):
            _re_exec(venv)

    _with_lock(_do)


if __name__ == "__main__":
    ensure_runtime()

