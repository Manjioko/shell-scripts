#!/usr/bin/env python3
"""运行期装配:配置读取、环境默认值、解释器兜底、轮次记账。

它回答的问题是"这次进程该按什么参数跑",不碰会话内容、不碰终端、不碰工具。
`cli.main` 在启动时调一次;`repl`/`session` 在需要时会读 `_auto_summary` 之类的展示串。

`_runtime_fallback()` 是这里的重点:brew 升级会删掉旧 Cellar,而正在跑的进程
`sys.executable` 仍指向旧目录 —— 所有 spawn 子进程的地方都必须经它取解释器。
"""
import os
import sys


def _seam():
    """“留在 cli 的会话入口”(标题生成)——调用时取,理由见 toolrun._seam()。"""
    from wchat_py import cli
    return cli


def _skills_dir_default():
    return os.environ.get("SKILLS_DIR_DEFAULT") or os.path.join(
        os.environ.get("HOME", ""), "." + "c" + "t", "skills")


def _load_ct_config():
    """源化 ~/.ct/config 到环境。

    与 master 的三条语义对齐(每一条都曾经不同步,踩过):
      1. **已经拿到 key 就不读文件**(master:`[[ -z "${API_KEY:-}" ]]` 才 source)——
         否则配置文件会盖掉命令行/环境里显式给的凭据,表现为"改了 env 没用";
      2. **权限不是 600/400 就拒绝加载并提示**——文件里有明文 key,同机他人可读
         等于把 key 交出去(master 的 stat 校验);
      3. 文件里的键**不覆盖**已存在的环境变量(环境变量优先级更高,便于临时试值)。
    只认 KEY=value 行,跳过注释/空行;行尾 ` # ...` 注释剥掉;值去引号。
    """
    if os.environ.get("API_KEY") or os.environ.get("WCHAT_API_KEY"):
        return
    _ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct")
    p = os.path.join(_ct, "config")
    try:
        mode = os.stat(p).st_mode & 0o777
    except OSError:
        return
    if mode not in (0o600, 0o400):
        sys.stderr.write("✗ %s 权限为 %o(应为 600),已拒绝加载: chmod 600 \"%s\"\n"
                         % (p, mode, p))
        return
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                if not k or not (k[0].isalpha() or k[0] == "_"):
                    continue
                if " #" in v:
                    v = v.split(" #", 1)[0]
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                    v = v[1:-1]
                if k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


def _detect_web_provider():
    """复刻 bash detect_web_search_provider:按 BASE_URL 与 flash 模型判断家在谁家。"""
    base = os.environ.get("BASE_URL", "")
    fm = os.environ.get("FLASH_MODEL", "") or os.environ.get("WCHAT_MODEL", "")
    if "xiaomimimo" in base or "mimo" in base or fm.startswith("mimo"):
        return "mimo"
    if any(k in base for k in ("maas.aliyuncs.com", "dashscope", "aliyuncs.com")) \
            or fm.startswith("qwen") or fm.startswith("qwq"):
        return "qwen"
    return "none"


def _apply_runtime_defaults():
    """补齐 bash 的派生默认值:窗口/压缩阈值/联网方式/超时。缺这些 -md/-u 显示全 0/空。"""
    os.environ.setdefault("CTX_WINDOW", "1000000")
    try:
        _ctx = int(os.environ.get("CTX_WINDOW") or 1000000)
    except ValueError:
        _ctx = 1000000
    os.environ.setdefault("TOKEN_THRESHOLD", str(_ctx * 3 // 4))
    # 越界判据与压缩点**必须是两个数**(master 亦然):压缩点抬到窗口 75% 时,
    # 若判据跟着抬,真越界就会被误判成"网络抖动"而反复重试。这里取 5/8。
    os.environ.setdefault("CTX_OVERFLOW_GATE", str(_ctx * 5 // 8))
    os.environ.setdefault("WEB_SEARCH_TIMEOUT", "90")
    _wp = os.environ.get("WEB_SEARCH_PROVIDER", "")
    if _wp in ("", "auto"):
        os.environ["WEB_SEARCH_PROVIDER"] = _detect_web_provider()


def _runtime_fallback():
    """`sys.executable` 若已消失(Homebrew 升级删掉旧 Cellar),换一个能用的解释器。

    真实故障:升级到新版时旧版本目录被清掉,而**正在运行**的进程 `sys.executable`
    还指着 `<旧 Cellar>/libexec/venv/bin/python`;调用方用它 spawn 子进程
    (`_run.py llm stream`、`wchat_py/mdrender.py`、repl 的 history helper)就会
    FileNotFoundError 直接崩栈,而不是给出一句能看懂的话。
    兜底顺序:环境里显式给的 → 当前 Cellar 里的 venv(按路径推断) → 系统 python3。

    ⚠ **每个候选都要过 `bootstrap._suitable()`**(存在 + 可执行 + 版本达标),不是
    "路径存在就用"。原因:这里的解释器是拿去 spawn 子进程(`_run.py llm stream`)的,
    子进程必须能 import openai/httpx —— 那几个包装在 venv 里,不在 `/usr/bin/python3`。
    旧实现只看 `os.path.exists`,于是在"brew 升级删了旧 Cellar、opt 链接也还没重建"
    的窗口里会退到系统 3.9:子进程 `ImportError: No module named openai`,
    而主进程一切正常 —— 报错位置离真因很远。
    """
    exe = sys.executable or ""
    if exe and os.path.exists(exe):
        return exe
    _cand = []
    _fixed = os.environ.get("WCHAT_PYTHON", "")
    if _fixed:
        _cand.append(_fixed)
    # <Cellar>/wchat/<ver>/libexec/wchat_py/cli.py → 同级的 venv/bin/python
    _here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _cand.append(os.path.join(_here, "venv", "bin", "python"))
    # brewed opt 链接(版本无关;`brew reinstall` 后自然指向新版本)
    _cand.append("/opt/homebrew/opt/wchat/libexec/venv/bin/python")
    _cand.append("/usr/local/opt/wchat/libexec/venv/bin/python")
    # 升级后"新 Cellar 已装好、但本进程仍指着旧版本" —— 挑一个存在的版本目录用
    import glob as _glob
    _cand.extend(sorted(_glob.glob("/opt/homebrew/Cellar/wchat/*/libexec/venv/bin/python"),
                        reverse=True))
    _cand.extend(sorted(_glob.glob("/usr/local/Cellar/wchat/*/libexec/venv/bin/python"),
                        reverse=True))
    _cand.append("/usr/bin/python3")
    try:
        from wchat_py.bootstrap import _suitable
    except Exception:
        _suitable = None
    for c in _cand:
        if not c:
            continue
        if _suitable is not None:
            if _suitable(c):
                return c
        elif os.path.exists(c):
            return c
    return exe or "python3"


def _sync_runtime(s):
    """把本轮 usage 回填活会话;首轮生成对话标题(与 bash 一致)。"""
    try:
        s.last_prompt_tokens = int(os.environ.get("WCHAT_LAST_PROMPT_TOKENS") or 0)
        s.last_cached_tokens = int(os.environ.get("WCHAT_LAST_CACHED_TOKENS") or 0)
    except ValueError:
        pass
    if not getattr(s, "chat_title", ""):
        _t = _seam()._gen_title(s.messages_json)
        if _t:
            s.chat_title = _t
            os.environ["WCHAT_CHAT_TITLE"] = _t


def _bump_auto():
    try:
        n = int(os.environ.get("WCHAT_AUTO_APPROVALS") or 0)
    except ValueError:
        n = 0
    os.environ["WCHAT_AUTO_APPROVALS"] = str(n + 1)


def _auto_summary():
    """退出时的 auto 会话小结(与 master auto_report_exit 同形)。"""
    tier = os.environ.get("AUTO_MODE") or os.environ.get("WCHAT_AUTO_MODE") or ""
    if not tier:
        return
    ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct")
    alog = os.environ.get("AUTO_AUDIT_LOG") or os.path.join(ct, "auto", "audit.log")
    try:
        n = int(os.environ.get("WCHAT_AUTO_APPROVALS") or 0)
    except ValueError:
        n = 0
    try:
        bn = int(os.environ.get("WCHAT_AUTO_BACKUPS") or 0)
    except ValueError:
        bn = 0
    try:
        import subprocess as _sp
        p = _sp.run(["git", "rev-parse", "--short", "HEAD"],
                    capture_output=True, text=True, timeout=5)
        rev = p.stdout.strip() if p.returncode == 0 else "(非 git 仓库)"
    except Exception:
        rev = "(非 git 仓库)"
    w = sys.stdout.write
    d, r = "\033[2m", "\033[0m"
    w("\n%s── auto 会话小结 ──%s\n" % (d, r))
    w("%s  自动放行 %d 次 · 备份 %d 份 · 档位 %s%s\n" % (d, n, bn, tier, r))
    w("%s  审计日志: %s%s\n" % (d, alog, r))
    w("%s  回滚: git diff %s 查看改动;git checkout %s -- . 复原%s\n" % (d, rev, rev, r))
    bdir = os.environ.get("WCHAT_AUTO_BACKUP_DIR", "")
    if bn > 0 and bdir:
        w("%s  备份目录: %s%s\n" % (d, bdir, r))
    try:
        from wchat_py import audit as _audit
        _audit.record(alog, "exit", tier=tier, verdict="finished",
                      approvals=n, backups=bn)
    except Exception:
        pass
    sys.stdout.flush()

def _trunc_int(key, default):
    try:
        return int(os.environ.get(key) or default)
    except (TypeError, ValueError):
        return default
