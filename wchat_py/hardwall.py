#!/usr/bin/env python3
"""hard_deny_cmd 的 Python 移植（B 阶段第 2 步，风险 1）。

第一版目标：**rule/soft parity**（与 tests/hdcmd_baseline.tsv 对齐），消息文案次之。
规则顺序照 bash（wchat.sh 4727–4937）：H1→H9、H13、H14、H10、H11、H12(auto)。
谓词全部复用已收口的 Python：hd_re（正则）、hd（mask_readonly/mask_msg/mask_search_pat 等）。
"""
import json
import os
import re

from wchat_py import hd
from wchat_py import hd_re as R

_ASCII = re.ASCII

# 系统路径前缀（与 bash hd_re_sys_redirect 的目标集对齐；**不含** var/tmp 等可写临时区）
_SYS_PATH = re.compile(
    r"^/(?:etc|usr|bin|sbin|System|Library|Applications|proc|sys|run|boot|lib|lib64|root|srv|media|mnt"
    r"|private/(?:etc|usr|bin|sbin|System|Library|var/root|var/log|var/spool/cron|var/db|"
    r"Applications|proc|sys|run|boot|lib|lib64|root|srv|media|mnt)"
    r"|var/(?:root|log|spool/cron|db))(/|$)")


def _m(name, text):
    pat = getattr(R, name)
    return re.search(pat, text, _ASCII) is not None


def _recheck(c, maskfn, names):
    """bash hd_recheck：把只读动词的引号参数脱敏后再匹；仍匹 = 命中（返回 True=照拒）。"""
    try:
        masked = maskfn(c)
    except Exception:
        return True  # 辅助缺席 = 视为仍命中（fail-closed）
    return any(_m(n, masked) for n in names)


def _redir_targets_all_clean(s):
    """bash hd_redir_targets_all_clean：每个 > 的目标都能证明是干净绝对非 dot 路径才 True。"""
    while ">" in s:
        rest = s.split(">", 1)[1]
        if rest.startswith(">"):
            rest = rest[1:]
        s = rest
        rest = rest.lstrip()
        if not rest:
            return False
        if rest.startswith('"'):
            rest = rest[1:]
            if '"' not in rest:
                return False
            tgt = rest.split('"', 1)[0]
        elif rest.startswith("'"):
            rest = rest[1:]
            if "'" not in rest:
                return False
            tgt = rest.split("'", 1)[0]
        else:
            tgt = re.split(r"[ \t;&|<>()']", rest, 1)[0]
        if not tgt:
            return False
        if not tgt.startswith("/") or "/." in tgt or "/.." in tgt or "//" in tgt or "~" in tgt:
            return False
    return True


def _has_write(c):
    """bash hd_has_write：抹掉丢弃型 sink 与 fd 复制后，残留 > 且目标不干净，或命中写动词。"""
    s = c
    while True:
        mm = re.search(R.hd_re_dev_sink, s, _ASCII)
        if not mm:
            break
        s = s.replace(mm.group(0), "", 1)
    while True:
        mm = re.search(r"[0-9]+>&[0-9-]|>&[0-9-]", s)
        if not mm:
            break
        s = s.replace(mm.group(0), "", 1)
    if ">" in s and not _redir_targets_all_clean(s):
        return True
    if _m("hd_re_write_verb", c):
        return True
    return False


def _is_cred_token(t):
    t = t.strip("'\"")
    if any(re.search(getattr(R, n), t, _ASCII)
           for n in ("hd_re_secret_dir", "hd_re_keychain", "hd_re_keyfile", "hd_re_shadow")):
        return True
    # 裸点目录(无前导字符): .aws/credentials、.config/gh/hosts、.ct/history 等
    if re.search(r"(^|/)\.(ssh|aws|gnupg|kube|docker|subversion|config|ct)(/|$)", t):
        return True
    return re.search(r"(^|/)\.(npmrc|netrc|pypirc|htpasswd|pgpass)$", t) is not None


def _creds_all_exempt(c, work_root, home):
    """移植 bash hd_creds_all_exempt 的**按位置**语义：cd 基准只影响其后的词。

    返回 True=全部凭据引用都在工作根内（豁免/放行）；False=至少一条在根外或基准不可知（照拒 H6）。
    """
    if work_root == home or work_root == "/":      # 边界被整台机器顶掉：豁免失效
        return False

    def under_root(p):
        np = os.path.normpath(p)
        return np == work_root or np.startswith(work_root.rstrip("/") + "/")

    toks = re.findall(r"[^\s;&|()]+", c or "")
    base_ok = True
    base = work_root
    found = False
    i = 0
    while i < len(toks):
        t = toks[i].strip("'\"")
        core = re.sub(r"[();,&|<>$`]", "", t)
        if core == "popd":
            base_ok = False
            i += 1
            continue
        if core in ("cd", "pushd"):
            nxt = toks[i + 1].strip("'\"") if i + 1 < len(toks) else ""
            ncore = re.sub(r"[();,&|<>$`]", "", nxt)
            if nxt in ("$PWD", "${PWD}"):
                base = work_root if base_ok else ""
                # 未 cd 前 $PWD == 工作根；若已不确定则保持不确定
            elif ncore == "" or ncore in ("&&", "||", ";", "|", "&", "<", ">", ">>") \
                    or ncore.startswith("-") or "$" in nxt or "`" in nxt:
                base_ok = False                          # 目标不可知（无参/分隔符/cd -/变量/命令替换）
            elif nxt in ("~", "$HOME", "${HOME}") or nxt.startswith("~/"):
                base_ok = False                          # 回家目录（通常不在工作根）
            elif nxt.startswith("/"):
                base_ok = under_root(nxt)
                base = nxt
            else:
                base = os.path.join(base, nxt) if base_ok else ""
                base_ok = bool(base) and under_root(base)
            i += 2
            continue
        if _is_cred_token(t):
            found = True
            if t.startswith("/") or t.startswith("~") or t.startswith("$HOME") or t.startswith("${HOME}"):
                p = t.replace("${HOME}", home).replace("$HOME", home)
                if p.startswith("~"):
                    p = os.path.expanduser(p)
                if not under_root(p):
                    return False
            elif not base_ok:
                return False
            elif re.search(r"[^A-Za-z0-9_./-]", t):   # 相对凭据词含 @/变量等非路径字符 → 证不了在根内
                return False
        i += 1
    return found


_SEP_RE = re.compile(r"&&|\|\||[;|\n]")
# 包管理器(段首词)。与 master hd_install_tail 的 state=0 名单对齐,另加 dpkg
# (master 名单里没有它,但 `dpkg -i` 就是装包,少拦一条是覆盖缺口)。
_MGR_RE = re.compile(r"^(?:brew|pip|pip[0-9.]*|python[0-9.]*|npm|pnpm|yarn|gem|cargo|go|"
                     r"uv|apt|apt-get|dnf|yum|apk|composer|dpkg)$")


def _mask_quoted(c):
    """把引号内文本按**等长**抹成空格，返回同长度字符串（索引可直接用于原串）。

    为什么要抹：`git commit -m "… brew tap …"` 里的安装词只是**叙述文本**，不是命令。
    不抹的话 H14 会把提交说明当成装包（实测：一条带 "brew tap" 的 commit 被拦，
    而且因为"不可逆"话术每次都要人逐字 yes —— 连我自己的提交都被拦了两次）。
    处理三种真实形态：多行引号（折行后仍是一段）、`\\"` 转义引号、未闭合引号（抹到底）。
    例外：`-c` / `--command` 之后的引号内容是**要执行的代码**（`bash -c "npm install x"`），
    原样保留 —— 抹掉它等于给出一条绕过路径。
    """
    out = list(c)
    i, n = 0, len(c)
    q, start = "", -1
    while i < n:
        ch = c[i]
        if not q:
            if ch in "\"'":
                q, start = ch, i
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            i += 2                      # 转义对:里面的引号不结束本段
            continue
        if ch == q:
            if not re.search(r"(?:^|\s)(?:-c|--command)\s*$", c[:start]):
                for k in range(start + 1, i):
                    out[k] = " "
            q = ""
        i += 1
    if q and start >= 0:                # 未闭合:按叙述文本抹到底
        if not re.search(r"(?:^|\s)(?:-c|--command)\s*$", c[:start]):
            for k in range(start + 1, n):
                out[k] = " "
    return "".join(out)


def _segments(c):
    """按命令分隔符切段，返回 [(start, end)]（end 不含分隔符）。"""
    spans, pos = [], 0
    for m in _SEP_RE.finditer(c):
        spans.append((pos, m.start()))
        pos = m.end()
    spans.append((pos, len(c)))
    return spans


def _install_scan(c, _depth=0):
    """"这是安装类动作"判定 + 包名收集。返回 (hit, names)。

    对齐 master `hd_install_tail` 的两条核心约束:
      ① **段首必须是包管理器**。旧实现用 `(^|[\\s;&|(])(brew install…)`,任何位置出现
         就算 —— 于是 `git commit -m "…brew tap…"`、`echo "npm install x"` 都被判装包
         (实测误拦两条,且因为"不可逆"话术每次都要人逐字 yes)。
      ② 找到管理器后,**动词可以在段内任意位置**(`npm --prefix /x --cache /y install z`
         的 flag 值也是普通词,给动词加位数上限会变成漏放);裸 `i` 只认紧跟管理器的
         第一个实参位(否则 `git commit -i` 之类会被误当装包)。
    引号内叙述文本先抹掉(见 _mask_quoted):折行会把 `\\n` 变成 `;`,让一段提交说明
    "切"出看似独立的命令段。`bash -c "npm install x"` 的引号是要执行的代码,递归判。
    """
    if not c:
        return False, []
    cm = _mask_quoted(c)
    names, found = [], False
    for start, end in _segments(cm):
        seg = cm[start:end].lstrip()
        if not seg:
            continue
        mh = re.search(r"<<(?!<)", seg)      # heredoc 之后是载荷,不是本段参数
        if mh:
            seg = seg[:mh.start()]
        if _depth < 3:
            msh = re.search(r"^(?:sudo\s+)?(?:env\s+)?(?:/[^\s]+/)?(?:sh|bash|zsh|dash|ksh)"
                            r"\s+(?:-[a-zA-Z]*c[a-zA-Z]*|--command)\s+(['\"])(.*)\1\s*$",
                            seg, re.S)
            if msh:
                h, n = _install_scan(msh.group(2), _depth + 1)
                if h:
                    found = True
                    names.extend(n)
                continue
        state, adj, mgr = 0, 0, ""
        for w in seg.split():
            if state == 0:
                if w == "sudo":
                    continue
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):   # FOO=1 npm install
                    continue
                if _MGR_RE.match(w):
                    state, adj, mgr = 1, 1, w
                else:
                    break                                      # 段首不是包管理器 → 本段不算
            elif state == 1:
                if w in ("install", "add", "tap") or (w == "i" and adj == 1) \
                        or (mgr == "dpkg" and w == "-i"):
                    state, found = 2, True
                elif not w.startswith("-"):
                    adj = 0
            else:
                if w.startswith("-") or ">" in w or "$(" in w:
                    continue
                if w.startswith(("/", "./", "../", "~")):
                    continue                                   # 路径不是包名
                names.append(w)
    return found, names


def _install_hit(c):
    """兼容旧调用点:只回答"是不是安装动作"。"""
    return _install_scan(c)[0]




def rule(cmd, auto=False, work_root=None, home=None):
    """返回 (rule, soft)。rule 非空即拒绝；soft 非空表示降级出口。`auto=True` 启用 H12 越界写判据。"""
    c = cmd or ""
    work_root = work_root or os.environ.get("WORK_ROOT") or "/tmp"
    home = home or os.environ.get("HOME") or ""
    # 与 bash hard_deny_cmd 开头一致的预处理：先剥"数据型 heredoc"正文(非代码)，
    # 再折行(续行→空格、裸换行→`;`)。fail-closed: 组件异常/无正文 → 保留原文。
    try:
        _pl = hd.noncode_payload(c)
        if _pl is not None:
            c = _pl
    except Exception:
        pass
    try:
        c = hd.fold_lines(c)
    except Exception:
        pass

    if _m("hd_re_priv", c) or _m("hd_re_su", c):
        return "H1", ""
    if (_m("hd_re_pipe_exec", c) or _m("hd_re_proc_sub", c) or _m("hd_re_dl_exec", c)) \
            and _recheck(c, hd.mask_readonly, ("hd_re_pipe_exec", "hd_re_proc_sub", "hd_re_dl_exec")):
        return "H2", ""
    if (_m("hd_re_wipe_root", c) or _m("hd_re_wipe_flag", c)) \
            and _recheck(c, hd.mask_readonly, ("hd_re_wipe_root", "hd_re_wipe_flag")):
        return "H3", ""
    if _m("hd_re_disk", c) and _recheck(c, hd.mask_readonly, ("hd_re_disk",)):
        return "H4", ""
    if (_m("hd_re_dotfile", c) or _m("hd_re_dotfile_cfg", c)) and _has_write(c):
        return "H5", ""
    if (_m("hd_re_secret_dir", c) or _m("hd_re_keychain", c) or _m("hd_re_keyfile", c) or _m("hd_re_shadow", c)) \
            and _recheck(c, _mask_msg_search, ("hd_re_secret_dir", "hd_re_keychain", "hd_re_keyfile", "hd_re_shadow")) \
            and not _creds_all_exempt(c, work_root, home):
        return "H6", ""
    if _m("hd_re_force_push", c) or _m("hd_re_history_rewrite", c):
        return "H7", ""
    if _m("hd_re_power", c):
        return "H8", ""
    if _m("hd_re_killhost", c):
        return "H9", ""
    # H13 opaque write（近似：复用 hd_has_write 之外的动词族；先给已知形态）
    if re.search(r"(defaults\s+(write|delete|read\[)|plutil\s+(-replace|-insert|-remove|-convert)|"
                 r"launchctl\s+(load|unload|bootstrap|bootout|kickstart|enable|disable)|osascript|"
                 r"sqlite3\s+[^;&|]*\b(update|delete|insert|drop|alter|create|vacuum|attach)\b|"
                 r"git\s+config\s+(--global|--system)|networksetup|htpasswd|codesign|xattr\s+(-w|-d|--delete|--set))"
                 r"(\s|$)", c):
        return "H13", ""
    # H14 安装类动作（非自动放行即拒；源替换同归 H14）。点名白名单经 WCHAT_INSTALL_ALLOW 传入。
    _ih_hit, _ih_names = _install_scan(c)
    if _ih_hit:
        # 换源(registry/index-url/tap URL)一律 H14，白名单也不放
        if re.search(r"--index-url|--extra-index-url|--registry|--find-links|"
                     r"(^|\s)-i\s+https?:|PIP_INDEX_URL=|NPM_CONFIG_REGISTRY=|YARN_REGISTRY=|"
                     r"brew\s+tap\s+https?://", c):
            return "H14", ""
        _allow = os.environ.get("WCHAT_INSTALL_ALLOW", "")
        if _allow:
            # 包名由状态机在**本命令段**内收集(`brew install a && echo b` 的 b 不是包);
            # 归一化与 master auto_install_allowed 一致:小写、去 @版本/@scope 后缀与版本约束。
            _pkgs = []
            for _w in _ih_names:
                _w = _w.strip(";,)]").lower()
                _w = _w.split("@")[0] if not _w.startswith("@") else _w
                _w = re.split(r"[<>=!~]", _w)[0]
                if _w:
                    _pkgs.append(_w)
            _al = set(x.strip().lower() for x in _allow.split() if x.strip())
            if _pkgs and all(_pk in _al for _pk in _pkgs):
                return "", ""
        return "H14", ""
    # H10 外发敏感内容
    if _m("hd_re_upload", c) and _m("hd_re_upload_arg", c) and _m("hd_re_secret_ref", c):
        return "H10", ""
    # H11 重定向/写动词落系统路径（方向感知：只看**写目标**，源在系统不算）
    if _m("hd_re_sys_redirect", c):
        return "H11", ""
    _home = os.environ.get("HOME", "")

    def _sys(p):
        p = p.strip("'\"")
        p = p.replace("${HOME}", _home).replace("$HOME", _home)
        if p.startswith("~"):
            p = os.path.expanduser(p)
        np = os.path.normpath(p)
        return _SYS_PATH.match(np) or _SYS_PATH.match(p)

    # 重定向/of= 目标（归一后判系统路径）——无条件
    for m in re.finditer(r"(?:of=|(?<![0-9&])>{1,2})[ \t]*([^\s;&|<>()]+)", c):
        if _sys(m.group(1)):
            return "H11", ""
    # 通用写动词的目标（sed -i 让位给路径规则 H12/P2，不在此判）
    if _m("hd_re_write_verb", c) and not re.search(r"\bsed\s+-i", c):
        try:
            wt = hd.write_targets(c, R.hd_re_write_verb)
        except Exception:
            wt = ""
        for w in (wt or "").splitlines():
            w = w.strip().rstrip(";,)]")
            if not w:
                continue
            if _sys(w):
                return "H11", ""
    # mv/rm 的**源**在系统路径也算破坏；cp 从系统读入不算。只扫 mv/rm **自己的操作数**
    # （到 ;&| 为止），避免把 `mv a b && cmp /etc/hosts` 里 cmp 的只读参数误算成写目标。
    c_nq = re.sub(r"'[^']*'|\"[^\"]*\"", "", c)
    for mm in re.finditer(r"(?:^|[;&|]|\s)(?:mv|rm)\s+([^;&|]+)", c_nq):
        for p in re.findall(r"[^\s;&|<>()]+", mm.group(1)):
            if _sys(p):
                return "H11", ""
    # H12 工作根外写（仅 auto 档）：逐写目标走 pathrule.hard_deny_path → P1/P2/P3
    if auto:
        from wchat_py import pathrule
        try:
            wt = hd.write_targets(c, R.hd_re_write_verb)
        except Exception:
            wt = ""
        for w in (wt or "").splitlines():
            w = w.strip().rstrip(";,)]").strip("'\"")
            if not w:
                continue
            w = w.replace("${HOME}", home).replace("$HOME", home)
            if w in ("/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/zero"):
                continue
            if w.startswith("/dev/"):
                continue
            if w == "~" or w.startswith("~/"):
                w = os.path.expanduser(w)
            elif w.startswith("../"):
                w = os.path.join(work_root, w)
            elif not w.startswith("/"):
                continue
            res = pathrule.hard_deny_path(w, "rw", "full", home, work_root, [], [], [])
            if res.get("rule"):
                return res["rule"], ""
    return "", ""


_MSGS = {}
try:
    with open(os.path.join(os.path.dirname(__file__), "hard_msgs.json"), encoding="utf-8") as _fh:
        _MSGS = json.load(_fh)
except Exception:
    _MSGS = {}


def _mask_msg_search(s):
    """等价 bash hd_recheck_msg 的脱敏链：mask_msg -> mask_search_pat。"""
    return hd.mask_search_pat(hd.mask_msg(s))


def _safe(fn, c):
    try:
        return fn(c)
    except Exception:
        return c


def _h14_hint(c):
    """复刻 bash _iht_hint：从安装动词后取"像包名"的词(纯 [A-Za-z0-9./_@~<>=+-]，跳过 -flag)，≤60。

    包名只取**本命令段**（原来一路扫到整条命令结尾，会把 `&& git log` 之类的词也当
    包名印进审批话术 —— 实测屏幕上出现过 "命中: <user>/<repo> git log"）。
    """
    _hit, _names = _install_scan(c)
    out = []
    for w in _names:
        w = w.strip(";,)]")
        if not w or w.startswith("-"):
            continue
        if re.search(r"[^A-Za-z0-9./_@~<>=+-]", w):
            continue
        if len(" ".join(out + [w])) > 60:
            break
        out.append(w)
    return " ".join(out) or "<没认出可靠包名>"


def _h14_msg(hint):
    return ("安装软件会在 WORK_ROOT 之外落盘、以你的 uid 跑第三方 postinstall 代码,且不在快照回滚范围内"
            "(模型还会记错包名)。替代:重启时加 --allow-install %s(只点名你认识的那几个),"
            "或把安装交给人工执行;不要改用别的源、也不要用 curl|sh 绕" % hint)


def _frag(rid, c):
    """best-effort 复刻 bash hd_note_hit 的 (命中: …) 片段：取决定该规则的正则首个匹配。"""
    def first(names, text):
        for n in names:
            mm = re.search(getattr(R, n), text, _ASCII)
            if mm:
                return mm.group(0)
        return ""

    if rid == "H1":
        return first(("hd_re_priv", "hd_re_su"), c)
    if rid == "H2":
        return first(("hd_re_pipe_exec", "hd_re_proc_sub", "hd_re_dl_exec"), _safe(hd.mask_readonly, c))
    if rid == "H3":
        return first(("hd_re_wipe_root", "hd_re_wipe_flag"), _safe(hd.mask_readonly, c))
    if rid == "H4":
        return first(("hd_re_disk",), _safe(hd.mask_readonly, c))
    if rid == "H5":
        # 近似 hd_has_write 的 BASH_REMATCH：先抹 dev_sink/fd，若残留 '>' 且目标不干净 → 早返(片段空)；
        # 否则片段取写动词匹配。
        s = c
        while True:
            mm = re.search(R.hd_re_dev_sink, s, _ASCII)
            if not mm:
                break
            s = s.replace(mm.group(0), "", 1)
        while True:
            mm = re.search(r"[0-9]+>&[0-9-]|>&[0-9-]", s)
            if not mm:
                break
            s = s.replace(mm.group(0), "", 1)
        if ">" in s and not _redir_targets_all_clean(s):
            return ""
        mm = re.search(getattr(R, "hd_re_write_verb"), c, _ASCII)
        return mm.group(0) if mm else ""
    if rid == "H6":
        return first(("hd_re_secret_dir", "hd_re_keychain", "hd_re_keyfile", "hd_re_shadow"),
                     _safe(hd.mask_msg, c))
    if rid == "H7":
        return first(("hd_re_force_push", "hd_re_history_rewrite"), c)
    if rid == "H8":
        return first(("hd_re_power",), c)
    if rid == "H9":
        return first(("hd_re_killhost",), c)
    if rid == "H10":
        return first(("hd_re_secret_ref",), c)
    if rid == "H11":
        mm = re.search(getattr(R, "hd_re_sys_redirect_wide"), c, _ASCII)
        if not mm:
            mm = re.search(getattr(R, "hd_re_sys_redirect"), c, _ASCII)
        if mm:
            return "第%d行 %s" % (c[:mm.start()].count("\n") + 1, mm.group(0))
        mm = re.search(r"(?:^|[ \t=&])(/(?:etc|usr|bin|sbin|System|Library|var|private)/[^\s;&|<>()]*)", c)
        if mm:
            return "第%d行 %s" % (c[:mm.start(1)].count("\n") + 1, mm.group(1))
    if rid == "H13":
        mm = re.search(
            r"(?:defaults\s+(?:write|delete|read\[)|plutil\s+(?:-replace|-insert|-remove|-convert)|"
            r"launchctl\s+(?:load|unload|bootstrap|bootout|kickstart|enable|disable)|osascript|"
            r"git\s+config\s+(?:--global|--system)|networksetup|htpasswd|codesign|xattr\s+(?:-w|-d|--delete|--set))(?:\s|$)",
            c)
        return mm.group(0) if mm else ""
    return ""


def _h12_target(c):
    try:
        wt = hd.write_targets(c, R.hd_re_write_verb)
    except Exception:
        wt = ""
    home = os.environ.get("HOME", "")
    for w in (wt or "").splitlines():
        w = w.strip().rstrip(";,)]")
        if not w:
            continue
        w = w.replace("${HOME}", home).replace("$HOME", home)
        if w.startswith("/dev/"):
            continue
        if w == "~" or w.startswith("~/"):
            w = os.path.expanduser(w)
        elif w.startswith("../"):
            w = "/tmp/" + w
        elif not w.startswith("/"):
            continue
        if not (w == "/tmp" or w.startswith("/tmp/")):
            return w
    return ""


def detail(cmd, auto=False):
    """返回 (rule, soft, msg)。msg = bash 话术(+ 动态命中片段)；未命中返回空串。"""
    c = cmd or ""
    r, s = rule(c, auto)
    if not r:
        return r, s, ""
    if r == "H14":
        mm = re.search(r"--index-url|--registry|--extra-index-url|--find-links", c)
        if mm:
            return r, s, _MSGS.get("H14REG", "") + "(命中: %s)" % mm.group(0)
        return r, s, _h14_msg(_h14_hint(c))
    if r == "P3":
        base = _MSGS.get("P3", "")
        pre = (base.split("--allow-write '")[0] + "--allow-write '") if "--allow-write '" in base else base
        tgt = _h12_target(c)
        return r, s, pre + tgt + "'" + ("(命中目标: %s)" % tgt if tgt else "")
    base = _MSGS.get(r, "")
    f = _frag(r, c)
    return r, s, (base + ("(命中: %s)" % f[:80] if f else ""))


def cmd_json(argv):
    """CLI seam：从 stdin 读命令，输出 JSON {rule,soft,msg,deny}，供 bash 硬墙前端调用。"""
    import sys
    auto = "--auto" in argv
    c = sys.stdin.read().rstrip("\n")
    r, s, m = detail(c, auto)
    sys.stdout.write(json.dumps({"rule": r, "soft": s, "msg": m, "deny": bool(r)}, ensure_ascii=False) + "\n")
    return 0


def main(argv):
    """py_run 入口：`python3 _run.py hardwall json [--auto]`（stdin=命令）。"""
    import sys
    if "json" in argv or "--json" in argv:
        return cmd_json(argv)
    for ln in sys.stdin:
        print("%s\t%s" % rule(ln.rstrip("\n")))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))