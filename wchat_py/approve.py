#!/usr/bin/env python3
"""授权账本与路径裁决:会话内的"人已经批过什么"该记在哪、怎么判。

三套账本(都由本模块独占,别在别处另起一份):
  · `_GRANTED_PATHS`  人工放行过的**路径**(带墙判据过滤,mode 是 r/rw);
  · `_ALLOW_RW`       启动参数 `--allow-write` 的白名单;
  · `_APPROVED_CMDS`  本会话人工批过的**命令**(折叠空白后比对)。

为什么要独立成模块:这三样是"安全状态",它们被 `cli`(审批链)、`repl`(Ctrl-F 选文件
后授权)、`toolrun`(执行前查)三方共享。历史上它们散在 `cli` 的模块全局里,谁都可能
顺手 add 一条 —— 授权面因此缺少单一出处。搬到这里之后,**写入口只有 `_grant_path`**。
"""
import os
import sys


_GRANTED_PATHS = set()


_ALLOW_RW = []

# 本会话内"人已经批过的命令"(折叠空白后比对)。
# 为什么需要:master 在人工确认后会把影响的外部路径记账(shell_note_grants),
# 于是同一类操作不再反复问人;迁移时只搬了判定没搬记账,现象是**同一条命令**
# 每执行一次就弹一次确认(实测同一晚被问 4 次)。这里补上"命令级"的记账,
# 与路径级(_GRANTED_PATHS)互补。
_APPROVED_CMDS = set()


def _cmd_key(cmd):
    """命令的会话内身份:折叠空白。**不做**语义归一(不改 argv/不忽略参数),
    改一个字符就是另一条命令 —— 缓存必须保守。"""
    import re as _re
    return _re.sub(r"\s+", " ", (cmd or "").strip())


def _grant_path(p, mode):
    """把一条已获人工放行的路径记进会话名单(带墙判据过滤,对齐 master shell_note_grants)。

    三条约束(每条都来自实测):
      · 只记绝对路径;`/`、`$HOME`、`/dev/*` 这类过宽目标不记;
      · 墙判据能拦的**不记**(记了也白记,而且会让名单看起来比实际宽);
      · 已覆盖的不重复堆条目。
    """
    from wchat_py import pathrule as _pr
    home = os.path.realpath(os.environ.get("HOME") or "")
    root = os.path.realpath(os.environ.get("WORK_ROOT") or os.getcwd())
    p = (p or "").strip().strip("'\"")
    p = p.replace("${HOME}", home).replace("$HOME", home)
    if p.startswith("~"):
        p = os.path.expanduser(p)
    if not p.startswith("/"):
        return
    p = os.path.realpath(p)
    if p in ("/", home) or p.startswith("/dev/"):
        return
    if root and (p == root or p.startswith(root + "/")):
        return
    try:
        _res = _pr.hard_deny_path(p, "rw" if mode == "rw" else "r", "full",
                                  home, root, [], [], [])
        if (_res or {}).get("rule"):
            return
    except Exception:
        return          # 墙判据不可用就不记账(fail-closed:绝不把没验过的路径发出去)
    for _m, _b in _GRANTED_PATHS:
        if _m == mode or (_m == "rw" and mode == "r"):
            if p == _b or p.startswith(_b + "/"):
                return
    _GRANTED_PATHS.add((mode, p))


def _note_path_grants(cmd, mode="rw"):
    """人工放行一条 shell 命令后,把它碰到的**外部**路径记账(对齐 master shell_note_grants)。

    只看两类目标:命令里出现的绝对路径词(读)、以及写动词的写目标(读写)。
    相对路径/URL/选项/命令替换一律跳过 —— 猜错方向等于自己给自己发授权。
    """
    try:
        from wchat_py import hd as _hd, hd_re as _R
        words = _hd.split_words(cmd or "").get("words") or []
    except Exception:
        words, _hd, _R = [], None, None
    for w in words:
        w = (w or "").strip()
        if not w or w.startswith("-") or "=" in w:
            continue
        if "://" in w or "$(" in w or "`" in w:
            continue
        if w.startswith("/") or w.startswith("~") or w.startswith("$HOME") or w.startswith("${HOME}"):
            _grant_path(w, "r")
    if _hd is not None:
        try:
            wt = _hd.write_targets(cmd or "", _R.hd_re_write_verb)
        except Exception:
            wt = ""
        for line in (wt or "").splitlines():
            _grant_path(line, "rw")


def _path_guard(path, mode):
    """工作目录外访问闸(对应 bash path_access):返回 None=放行, 字符串=拒绝原因。"""
    if not path:
        return None
    p = os.path.realpath(os.path.expanduser(path))
    root = os.path.realpath(os.environ.get("WORK_ROOT") or os.getcwd())
    home = os.path.realpath(os.environ.get("HOME") or "")
    try:
        import tempfile as _tf
        tmp = os.path.realpath(_tf.gettempdir())
    except Exception:
        tmp = "/tmp"
    _tmps = {tmp, "/tmp", "/private/tmp"}
    _envtmp = os.environ.get("TMPDIR", "")
    if _envtmp:
        _tmps.add(os.path.realpath(_envtmp))

    def under(x, r):
        r = r.rstrip("/")
        return bool(r) and (x == r or x.startswith(r + "/"))

    if under(p, root) or any(under(p, t) for t in _tmps if t):
        return None
    _creds = (".ct/config", ".ct/history", ".ct/mcporter.json", ".ssh", ".aws", ".kube", ".docker",
              ".netrc", ".npmrc", ".mcporter")
    is_cred = home and any(under(p, os.path.join(home, x)) for x in _creds)
    auto = os.environ.get("AUTO_MODE") in ("full", "sandbox")
    _act = "写" if mode == "rw" else "读"

    def _deny_status(rule):
        from wchat_py import audit as _au
        _au.record(os.environ.get("AUTO_AUDIT_LOG", ""), "path", rule=rule,
                   decision="auto_deny", path=path)
        # 拒绝只给理由(规则号 + 说明),不回显路径正文;非正文统一灰化。
        from wchat_py.render import dim as _dim
        sys.stdout.write(" " + _dim("⛔ 越界%s被硬墙拒绝(%s)" % (_act, rule)) + "\n")
        sys.stdout.flush()

    if is_cred:
        if auto:
            _deny_status("P1")
            return "错误:%s被拒绝[硬墙 P1]:凭据/会话数据路径不可访问。这不是用户拒绝,是代码级红线,换方案继续,不要改写路径重试同一目标。" % _act
        from wchat_py.render import dim as _dim
        sys.stdout.write("\033[2K \033[1;31m⛔ 高危路径\033[0m " + _dim(path) + "\n\n")
        sys.stdout.write(" \033[1;31m确需放行?必须输入 yes(n/回车 = 拒绝): \033[0m")
        sys.stdout.flush()
        try:
            ans = (sys.stdin.readline() or "").strip()
        except KeyboardInterrupt:
            ans = ""
        except Exception:
            ans = ""
        sys.stdout.write("\n"); sys.stdout.flush()
        return None if ans == "yes" else "用户拒绝访问: %s" % path
    if auto:
        if mode == "r":
            return None
        if os.environ.get("AUTO_MODE") == "full":
            for _a in _ALLOW_RW:
                if under(p, _a):
                    return None
        _deny_status("P2")
        return "错误:%s被拒绝[硬墙 P2]:工作目录外写入。这不是用户拒绝,是代码级红线,换方案继续,不要改写路径重试同一目标。" % _act
    for _m, _base in _GRANTED_PATHS:
        if _m == mode or (_m == "rw" and mode == "r"):
            if under(p, _base):
                return None
    from wchat_py.render import dim as _dim
    sys.stdout.write("\033[2K \033[1;31m⚠ AI 请求访问工作目录外文件\033[0m " + _dim(path) + "\n\n")
    sys.stdout.write(" \033[1;31m是否放行?(y 或 yes,n/回车 = 拒绝): \033[0m")
    sys.stdout.flush()
    try:
        ans = (sys.stdin.readline() or "").strip().lower()
    except KeyboardInterrupt:
        ans = ""
    except Exception:
        ans = ""
    sys.stdout.write("\n"); sys.stdout.flush()
    if ans in ("y", "yes"):
        # 记账要在**拿到许可之后**,而且记的是实际被问的那条路径(不是 key:
        # 旧实现这里写的是未定义变量 `key`,NameError 一抛,授权永远记不上,
        # 于是同一个目录每次访问都要再问人 —— 这就是"多次授权"的来源之一)。
        _grant_path(p, "rw" if mode == "rw" else "r")
        return None
    from wchat_py.render import dim as _dim
    sys.stdout.write(" " + _dim("⛔ 用户拒绝%s: %s" % (_act, path)) + "\n"); sys.stdout.flush()
    return "用户拒绝访问工作目录外文件: %s" % path
