"""hd —— 硬墙判据的可迁移子集(迁移自 wchat.sh 的 hd_* 谓词链)。

⚠ 这是**安全核心**,迁移纪律比别处更严:
  · 与 bash 回退实现逐字节 parity(tests/py_channel_test.sh);
  · 还要跑完整 569 向量(tests/hardwall_py_test.sh 的 HW_PY=1 模式),两边各自对期望值;
    负对照(弄坏本模块)必须让表变红,证明 python 路径真的在执行。
  · **只迁纯字符串谓词**;读全局/带副作用的(hard_deny_cmd、hd_note_hit 等)留 bash。
  · 改动落点:本文件 + wchat.sh 里对应函数的 `if _py_on hd` 分支 + py_channel_test 的向量。

退出码(见 `_contract`):
  0  成功,结果写 stdout
  1  未知子命令

用法:
  python3 -m wchat_py.hd mask_readonly          # stdin=命令串 -> 脱敏后的等长串
  python3 -m wchat_py.hd mask_msg
  python3 -m wchat_py.hd mask_search_pat
  python3 -m wchat_py.hd fold_lines
  python3 -m wchat_py.hd split_words
  python3 -m wchat_py.hd foreign_cred_path <path> <home> <subdir...>  # -> "0"/"1"
  python3 -m wchat_py.hd h11_dest_hit <flat> <prefixes|分隔>            # 命中->路径(rc0),否则 rc1
  python3 -m wchat_py.hd strip_noncode                                  # stdin=一行 -> 剥注释
  python3 -m wchat_py.hd is_readonly_verb <cmd> # -> "0"/"1"
"""

from __future__ import annotations

import re
import sys

from . import _contract

# 只读/查询类动词白名单(与 bash 的 hd_is_readonly_verb 逐字对齐)。
_READONLY_VERBS = frozenset((
    "grep", "rg", "ag", "sed", "awk", "echo", "printf", "cat", "head", "tail",
    "sort", "uniq", "cut", "wc", "tr", "comm", "cmp", "diff", "file", "column",
    "iconv", "base64", "md5", "shasum", "jq", "yq", "less", "more", "man",
    "strings", "git",
))
# 消息类动词(只有 echo/printf 的参数永远不会被当文件读)。
_MSG_VERBS = frozenset(("echo", "printf"))
# 检索类动词(只脱**模式位**,不脱尾部操作数)。
_SEARCH_VERBS = frozenset(("grep", "egrep", "fgrep", "rg", "ag"))

# 引号内原样保留(不替换成 x)的分界符 —— 保持等长、不拼出新命中。
_IN_QUOTE_KEEP = ";|&\n<>$`"
# 引号外的分界符(句法边界)。
_SEPARATORS = ";|&\n()<>`"
# “自带取文本值”的选项:值永不被当文件读,故其取值可脱。
_OPT_WITH_TEXT = ("-e", "-g", "--include=", "--exclude=", "--exclude-dir=",
                  "--glob=", "--regexp=")


def is_readonly_verb(cmd: str) -> bool:
    return cmd.split("/")[-1] in _READONLY_VERBS


def is_msg_verb(cmd: str) -> bool:
    return cmd.split("/")[-1] in _MSG_VERBS


def is_search_verb(cmd: str) -> bool:
    return cmd.split("/")[-1] in _SEARCH_VERBS


def _mask(s: str, is_verb) -> str:
    """把**某类动词**的带引号参数脱敏成等长占位(不改形状、保留分界符、引号内遇
    `$(`/反引号立刻停脱)。逐字符状态机,与 bash 旧实现逐条对齐。"""
    out = []
    cmd = ""
    q = ""
    qm = 0
    sp = 1
    keep = 0
    n = len(s)
    for i in range(n):
        ch = s[i]
        nxt = s[i + 1] if i + 1 < n else ""
        if q:
            if ch == q:
                out.append(")" if qm else ch)
                q = ""
                qm = 0
                continue
            if qm:
                if ch == "`" or (ch == "$" and nxt == "("):
                    qm = 0
                    out.append(ch)
                    continue
                out.append(ch if ch in _IN_QUOTE_KEEP else "x")
            else:
                out.append(ch)
            continue
        if ch == "'" or ch == '"':
            q = ch
            if keep == 1 and sp == 0:
                qm = 1
                out.append("(")
            else:
                qm = 0
                out.append(ch)
        elif ch == " " or ch == "\t":
            if sp and cmd:
                keep = 1 if is_verb(cmd) else 0
                cmd = ""
                sp = 0
            out.append(ch)
        elif ch in _SEPARATORS:
            if sp and cmd:
                keep = 1 if is_verb(cmd) else 0
                cmd = ""
            keep = 0
            sp = 1
            out.append(ch)
        else:
            if sp:
                cmd += ch
            out.append(ch)
    return "".join(out)


def mask_readonly(s: str) -> str:
    return _mask(s, is_readonly_verb)


def mask_msg(s: str) -> str:
    return _mask(s, is_msg_verb)


def mask_search_pat(s: str) -> str:
    """检索类动词的**模式位**脱敏(H6/H3/H4 的二次确认口径,见 bash 侧长注释)。

    与 hd_mask_msg 的区别:只脱"日志/检索里那个模式位",尾部操作数一格不动。
      grep -n '\\.npmrc' wchat.sh   → 模式位脱,文件位 wchat.sh 干净 → 不误拦
      grep 'x' '.npmrc'            → 第二个引号参数是操作数 → 一个字不脱
      grep -f '~/.npmrc' x         → -f 的取值是模式文件、真会打开 → 照拦
    """
    if not s:
        return s
    out = []
    w = ""
    q = ""
    qm = 0
    sp = 1
    vs = 0
    patused = 0
    dd = 0
    pending = ""
    n = len(s)
    for i in range(n):
        ch = s[i]
        nxt = s[i + 1] if i + 1 < n else ""
        if q:
            if ch == q:
                q = ""
                qm = 0
                out.append(ch)
                continue
            if qm:
                if ch == "`" or (ch == "$" and nxt == "("):
                    qm = 0
                    out.append(ch)
                    continue
                out.append(ch if ch in ";|&\n<>$`" else "x")
            else:
                out.append(ch)
            continue
        if ch == "'" or ch == '"':
            q = ch
            qm = 0
            msk = 0
            if vs == 1 and dd == 0:
                if w:
                    if w in ("-e", "-g") or w.startswith(
                            ("--include=", "--exclude=", "--exclude-dir=",
                             "--glob=", "--regexp=")):
                        msk = 1
                elif pending == "pat":
                    msk = 1
                    pending = ""
                elif patused == 0:
                    msk = 1
                if msk == 1:
                    patused = 1
                    qm = 1
                    out.append("(")
                    continue
            out.append(ch)
            sp = 0
        elif ch == " " or ch == "\t":
            if w:
                if vs == 0:
                    if is_search_verb(w):
                        vs = 1
                elif w == "--" or w == "-f" or w == "--file":
                    dd = 1
                elif w.startswith("-"):
                    pending = "pat" if w in ("-e", "-g", "--include", "--exclude",
                                             "--exclude-dir", "--glob", "--regexp") else ""
                else:
                    patused = 1
                    pending = ""
                w = ""
            sp = 1
            out.append(ch)
        elif ch in _SEPARATORS:
            if w:
                if vs == 0:
                    if is_search_verb(w):
                        vs = 1
                w = ""
            vs = 0
            patused = 0
            dd = 0
            pending = ""
            sp = 1
            out.append(ch)
        else:
            if sp == 1:
                w = ch
                sp = 0
            elif w:
                w += ch
            out.append(ch)
    return "".join(out)


def fold_lines(s: str) -> str:
    """命令边界归一:判定串里换行有两种语义 —— `\\`+换行是同一条命令的续行,
    裸换行是两条命令。折法:`\\`+换行 → 空格;裸换行 → `;`(正则本就在 `;` 处停)。
    含 heredoc(`<<`)一律不折:正文里的换行是数据不是命令边界。

    与 bash 旧实现逐字节等价(parity 由 py_channel_test 钉住):
      · 无换行 → 原样;含 `<<` → 原样;
      · 逐行:`\\` 结尾 → 去掉 `\\` 补空格;否则补换行;
      · 只去掉**一个**结尾换行,再把每个换行前补 `;`(行结构保持,坐标对着原文数)。
    """
    if "\n" not in s:
        return s
    if "<<" in s:
        return s
    out = ""
    for line in s.split("\n"):
        if line.endswith("\\"):
            out += line[:-1] + " "
        else:
            out += line + "\n"
    if out.endswith("\n"):
        out = out[:-1]
    return out.replace("\n", ";\n")


def split_words(s: str) -> "dict":
    """命令串 → token 列表(供写目标提取器用)。与 bash 旧实现逐字符对齐:
      · 引号内原样进当前 token(引号本身不保留);空引号对 `''`/`""` 只置 empty_token;
      · `\\` 转义下一个字符(含空格);
      · `$(...)` / `${...}` / 反引号**整块**并进当前 token(内部按深度配对),按空格切会
        造出 `$(date` 这类碎 token,令 `)` 之后的路径顶到"命令名"位、判定错位;
      · 空格/制表符断词;空 token 不入列(只体现在 empty_token)。
    """
    words = []
    empty_token = 0
    i = 0
    n = len(s)
    cur = ""
    q = ""
    while i < n:
        ch = s[i]
        nxt = s[i + 1] if i + 1 < n else ""
        if q:
            if ch == q:
                q = ""
            else:
                cur += ch
        elif ch == "\\":
            if i + 1 < n:
                cur += s[i + 1]
                i += 1
        elif ch == '"' or ch == "'":
            if nxt == ch:
                empty_token = 1
                i += 1
            else:
                q = ch
        elif (ch == "$" and (nxt == "(" or nxt == "{")) or ch == "`":
            if ch == "`":
                close = "`"
                delim = ""
            elif nxt == "(":
                close = ")"
                delim = "("
            else:
                close = "}"
                delim = "{"
            cur += ch
            if delim:
                cur += delim
                i += 1
            dep = 1
            j = i + 1
            q2 = ""
            while j < n and dep > 0:
                c2 = s[j]
                if q2:
                    if c2 == q2:
                        q2 = ""
                elif c2 == '"' or c2 == "'":
                    q2 = c2
                elif delim:
                    if c2 == delim:
                        dep += 1
                    elif c2 == close:
                        dep -= 1
                elif c2 == "`":
                    dep -= 1
                cur += c2
                j += 1
            i = j - 1
        elif ch == " " or ch == "\t":
            if cur:
                words.append(cur)
                cur = ""
        else:
            cur += ch
        i += 1
    if cur:
        words.append(cur)
    return {"words": words, "empty_token": empty_token}


def cmd_split_words(argv) -> int:
    import json
    out = split_words(_contract.read_stdin_text())
    _contract.write_text(json.dumps(out, ensure_ascii=False) + "\n")
    return _contract.EXIT_OK


# ---- 写目标提取(迁移自 wchat.sh hd_write_targets)----
_POSIX_CLASSES = [
    ("[:space:]", r"\s"), ("[:alpha:]", "A-Za-z"), ("[:alnum:]", "A-Za-z0-9"),
    ("[:digit:]", "0-9"), ("[:upper:]", "A-Z"), ("[:lower:]", "a-z"),
    ("[:xdigit:]", "A-Fa-f0-9"),
]


def _posix(regex: str) -> str:
    """bash ERE 的 POSIX 字符类 → Python re 等价物(Python re **不**认 `[[:space:]]`)。
    只需处理嵌在括号里的 `[:xxx:]` 片段:替换后 `[[:space:];&|]` → `[\\s;&|]`。"""
    for a, b in _POSIX_CLASSES:
        regex = regex.replace(a, b)
    return regex


_WT_KEYWORDS = frozenset((
    "for", "in", "do", "done", "while", "until", "if", "then", "else", "elif",
    "fi", "case", "esac", "select", "time", "!", "sudo", "env", "export",
))
_WT_OPTS_TAKE_NEXT = frozenset((
    "-C", "-O", "-o", "--directory", "--outdir", "--output", "--target", "--file",
))
_WT_MODE_ALL = frozenset(("rm", "rmdir", "unlink", "shred", "tee", "truncate",
                          "touch", "mkdir", "dd", "ln"))
_WT_MODE_LAST = frozenset(("cp", "mv", "rsync", "scp", "install"))
_WT_MODE_INPLACE = frozenset(("sed", "awk", "gawk", "perl"))


def write_targets(cmd: str, write_verb_re: str) -> str:
    """从一条命令串里提出"有写语义的目标"(每个一行,去重保序)。见 wchat.sh hd_write_targets。

    只做提取,不做裁决(/dev/null 白名单、越界与否在 H12 循环里判)。
    """
    t = re.sub(r"\d+>&[\d-]|>&[\d-]", "", cmd)          # 抹掉 fd 复制
    t = t.replace("|&", " ")                              # |& 当普通分隔
    flat = (t.replace("&&", "\n").replace("||", "\n")
             .replace("|", "\n").replace(";", "\n"))
    try:
        wv = re.compile(_posix(write_verb_re))
    except re.error:
        wv = None
    out = []
    for seg in flat.split("\n"):
        if seg.replace(" ", "") == "":
            continue
        sp = split_words(seg)
        words = sp["words"]
        empty_token = sp["empty_token"]
        n = len(words)
        if n == 0:
            continue
        prog = ""
        reds, ops, topts = [], [], []
        i = 0
        while i < n:
            w = words[i]
            if ">" in w:                                  # 2a:重定向目标
                rhs = w.rsplit(">", 1)[1]
                if rhs == "" and i + 1 < n:
                    rhs = words[i + 1]
                    i += 1
                rhs = rhs.replace('"', "").replace("'", "").strip()
                if rhs:
                    reds.append(rhs)
                i += 1
                continue
            if "<" in w:                                  # 2b:输入重定向(读方向)跳过
                if w.endswith("<") and i + 1 < n:
                    i += 1
                i += 1
                continue
            if w.startswith("-") or "=" in w:             # 2c:选项
                if w in _WT_OPTS_TAKE_NEXT:
                    if i + 1 < n:
                        topts.append(words[i + 1])
                        i += 1
                elif w.startswith("--") and "=" in w:
                    rhs = w.split("=", 1)[1]
                    if rhs:
                        topts.append(rhs)
                elif re.match(r"^-[A-Za-z].*f$", w):
                    # 归档类合并短选项:tar 的 f 之后才是归档名;x/t 是解包/列出(归档只读)
                    if prog in ("tar", "gtar", "bsdtar"):
                        if ("x" in w) or ("t" in w):
                            if i + 1 < n:
                                i += 1
                        else:
                            if i + 1 < n:
                                topts.append(words[i + 1])
                                i += 1
                i += 1
                continue
            if w in _WT_KEYWORDS:
                i += 1
                continue
            if prog == "":
                prog = w.rsplit("/", 1)[-1]               # 段内第一个实义 token = 命令名
                i += 1
                continue
            ops.append(w)
            i += 1
        if prog == "":
            continue
        mode = "flagonly"
        if prog in _WT_MODE_ALL:
            mode = "all"
        elif prog in _WT_MODE_LAST:
            mode = "last"
        elif prog in _WT_MODE_INPLACE:
            mode = "inplace"
        if mode == "inplace":
            # sed/awk 只有独立选项簇里带 i(或 --in-place)才算真写
            if re.search(r"(^|\s)-[A-Za-z]*i[A-Za-z]*(\s|$)", seg) or \
               re.search(r"(^|\s)--in-place", seg):
                pass
            else:
                mode = "flagonly"
        if empty_token and mode == "flagonly" and wv and wv.search(seg):
            mode = "all"
        out.extend(reds)
        out.extend(topts)
        if mode == "all":
            out.extend(ops)
        elif mode == "last":
            if ops:
                out.append(ops[-1])
        elif mode == "inplace":
            for w in ops:
                if any(c in w for c in ",(){}`^\\"):
                    continue
                if "/" in w:
                    if not (w.startswith("./") or w.startswith("/")
                            or w.startswith("~") or w.startswith("$")):
                        continue
                out.append(w)
    seen = set()
    res = []
    for x in out:                                         # 去重保序(同 awk '!seen[$0]++')
        if x and x not in seen:
            seen.add(x)
            res.append(x)
    return "".join(x + "\n" for x in res)


def cmd_write_targets(argv) -> int:
    _contract.write_text(write_targets(_contract.read_stdin_text(),
                                       argv[0] if argv else ""))
    return _contract.EXIT_OK


# ---- 非执行型 heredoc 正文剥离(迁移自 wchat.sh hd_noncode_payload)----
# 口径:白名单,不是黑名单。只有接收方明确"把正文当数据"的动词才剥正文;看不懂的一律当代码。
_NCP_DATA_VERBS = (" git commit ", " git tag ", " tee ", " cat ", " echo ",
                   " printf ", " cp ", " mv ", " touch ")
_NCP_INTERP_RE = re.compile(
    r"(^|\s)(sh|bash|zsh|dash|ksh|python[0-9.]*|perl|ruby|node|php|awk|sed|eval|"
    r"expect|ssh|su|sudo|crontab|at|batch|xargs|env|systemd-run)\s")
_NCP_TAG_RE = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)")


def noncode_payload(raw: str):
    """剥掉"数据型 heredoc"的正文(正文不是要执行的代码)。找到可剥的 → 返回改写后的串;
    否则返回 None(调用方保留原文,绝不拿空串替换判据文本)。见 wchat.sh hd_noncode_payload。
    """
    out = []
    tag = ""
    drop = False
    found = False
    for line in raw.split("\n"):
        if drop:
            if line.lstrip(" \t") == tag:          # <<- 的结束符可带缩进
                drop = False
                out.append(line)
            continue
        out.append(line)
        # here-string <<< 不是 heredoc,别把它之后的行整段跳掉
        if ("<<" in line) and ("<<<" not in line):
            m = _NCP_TAG_RE.search(line)
            if m:
                tag = m.group(1)
                s = " " + line + " "
                data = any(v in s for v in _NCP_DATA_VERBS)
                # 同一行既有数据动词又有解释器/执行类动词 → 按解释器算(保守)
                if _NCP_INTERP_RE.search(line):
                    data = False
                if data:
                    drop = True
                    found = True
    if not found:
        return None
    return "".join(x + "\n" for x in out)


def cmd_noncode_payload(argv) -> int:
    out = noncode_payload(_contract.read_stdin_text())
    if out is None:
        return _contract.EXIT_FAIL
    _contract.write_text(out)
    return _contract.EXIT_OK


# ---- H11 逐行扫描器(迁移自 wchat.sh hd_h11_scan)----
def h11_scan(raw: str, sys_redirect_re: str, prefixes: str) -> "dict":
    """逐行扫 H11(重定向写系统路径 / mv·install·truncate·dd 落点)。返回 dict:

    {num, hit, line, exempt, code, code_ok, found}。`found=1` 表示命中(rc=0)。
    · 行号 1 基;命中即**停止**(code 只含到该行,与 bash 同);
    · code=整条命令剥注释后的"会执行的部分"(heredoc 正文仍在内,bash 如此);
    · 命中判定用 hd_re_sys_redirect;次判据(无重定向符号但含路径)走 h11_dest_hit。
    """
    try:
        rex = re.compile(_posix(sys_redirect_re))
    except re.error:
        rex = None
    num = 0
    heredoc = False
    tag = ""
    code = []
    exempt = ""

    def _pack(found, hit="", line=""):
        return {"num": num if found else 0, "hit": hit, "line": line,
                "exempt": exempt, "code": "".join(x + "\n" for x in code),
                "code_ok": 1, "found": 1 if found else 0}

    for line in raw.split("\n"):
        num += 1
        # 攒"会执行的文本":只有含 # 的行才付逐字符剥注释的代价,其余原样收下
        code.append(strip_noncode(line) if "#" in line else line)
        if heredoc:
            if line.lstrip(" \t") == tag:      # <<- 结束符可带缩进
                heredoc = False
            continue
        frag = ""
        m = rex.search(line) if rex else None
        if m:
            frag = m.group(0)
        elif line.count("/") >= 2:              # 廉价预筛:像含路径才付 dest_hit 的代价
            f2 = h11_dest_hit(line, prefixes)
            frag = f2 if f2 else ""
        if frag:
            stripped = strip_noncode(line)
            m2 = rex.search(stripped) if rex else None
            if m2:
                return _pack(True, m2.group(0), stripped)
            # 目标版命中必须在**剥完注释**的那份文本里还在(否则是 # 之后的叙述)
            if frag in stripped:
                return _pack(True, frag, stripped)
            exempt = "第%d行的 %s 剥掉注释后不再命中(写在 # 之后,不会执行)" % (num, frag)
        if ("<<" in line) and ("<<<" not in line):
            mt = _NCP_TAG_RE.search(line)
            if mt:
                tag = mt.group(1)
                heredoc = True
    return _pack(False)


def cmd_h11_scan(argv) -> int:
    import json
    raw = _contract.read_stdin_text()
    re_ = argv[0] if len(argv) > 0 else ""
    prefixes = argv[1] if len(argv) > 1 else ""
    r = h11_scan(raw, re_, prefixes)
    _contract.write_text(json.dumps(r, ensure_ascii=False))
    return _contract.EXIT_OK if r["found"] else _contract.EXIT_FAIL


def foreign_cred_path(p: str, home: str, subdirs) -> bool:
    """他人家目录下的凭据目录?(命令级 H6 与路径级 P1 共用的一份判据)

    只判"别人的家":自己家目录下的路径交给精确名单。用 **basename 比对**而不是
    `$HOME/` 前缀 —— p 是归一后路径,macOS 上 /home 是 firmlink(归一成
    /System/Volumes/Data/home),前缀比会失配。名单匹配限定在"家目录直属的下一段"。
    HOME 未设时保守返回 False(不因此加严,免得把自己的一切当他人)。
    """
    if not home:
        return False
    me = home.split("/")[-1]
    found = False
    rest = ""
    for pre in ("/Users/", "/home/"):
        if found:
            break
        idx = p.find(pre)                 # case *"$pre"* → 子串任意位置
        if idx < 0:
            continue
        rest = p[idx + len(pre):]         # ${p#*"$pre"} 最短匹配 = 第一个 pre 之后
        name = rest.split("/", 1)[0]      # ${rest%%/*}
        if (not name) or name == rest:    # 没有"用户名/…"这一层
            return False
        if name == me:                    # 是自己 → 不加严
            return False
        rest = rest[rest.find("/") + 1:]  # 剥掉用户名,只留直属层级往后
        found = True
    if not found:
        return False
    for s in subdirs:
        if rest == s or rest.startswith(s + "/"):
            return True
    return False


def cmd_foreign_cred_path(argv) -> int:
    p = argv[0] if len(argv) > 0 else ""
    home = argv[1] if len(argv) > 1 else ""
    subdirs = list(argv[2:])
    _contract.write_text("0" if foreign_cred_path(p, home, subdirs) else "1")
    return _contract.EXIT_OK


def _h11_norm(p: str) -> str:
    """字面归一化 `a/../b → b`(不跟随符号链接)。

    复刻 bash 的 `${p/\\/[^\\/]*\\/..\\//\\/}` —— 那是**glob 模式**不是正则:
    `/` + 一个非斜杠字符 + `*`(任意,含 `/`,贪心) + `/../`,左端最长匹配。等价正则
    `/[^/].*/\\.\\./`。实测锚点:`/a/../../b → /b`、`//../x` 不变、`a/../b` 不变。
    """
    g = 0
    while "/../" in p and g < 32:
        new = re.sub(r"/[^/].*/\.\./", "/", p, count=1)
        if new == p:
            break
        p = new
        g += 1
    if p.endswith("/.."):
        p = p[:-3]
    return p


def _h11_prot(p: str, prefixes: str) -> bool:
    """绝对路径是否落在受保护前缀下(名单以 `|` 分隔,由 bash 传入,单一出处)。"""
    if not p.startswith("/"):
        return False
    for x in prefixes.split("|"):
        if p.startswith("/" + x + "/"):
            return True
    return False


def h11_dest_hit(flat: str, prefixes: str):
    """从一行里提取"能证明写进受保护路径"的操作数;命中返回该路径,否则 None。

    动词按段首识别,段边界 `;$|&()` 与换行(`>` 不算边界——它是目标前导)。取哪侧操作数:
    mv/truncate 任意绝对操作数;cp/install/rsync/ln 只取末位;`> x`/`of=` 无条件算写入目标。
    """
    flat = re.sub(r"[;$|&()]", "\n", flat)
    for seg in flat.split("\n"):
        if seg.count("/") < 2:
            continue
        W = seg.split()
        verb = ""
        redir = 0
        n = len(W)
        for i in range(n):
            w = W[i]
            if w.endswith('"'):
                w = w[:-1]
            if w.startswith('"'):
                w = w[1:]
            if w.endswith("'"):
                w = w[:-1]
            if w.startswith("'"):
                w = w[1:]
            if not w:
                continue
            if w in (">", ">>", ">&", ">|"):
                redir = 1
                continue
            if redir:
                redir = 0
                p = _h11_norm(w)
                if _h11_prot(p, prefixes):
                    return p
                continue
            if w.startswith("of="):
                p = _h11_norm(w[3:])
                if _h11_prot(p, prefixes):
                    return p
            if w.startswith("-"):
                continue
            if not verb:
                base = w.split("/")[-1]
                if base in ("cp", "mv", "install", "rsync", "ln", "truncate", "dd"):
                    verb = base
                elif base in ("sudo", "doas", "command", "env", "time", "nohup",
                              "exec", "nice", "ionice", "timeout", "xargs"):
                    pass
                else:
                    verb = "NO"
                continue
            if verb == "NO":
                continue
            if not w.startswith("/"):
                continue
            p = _h11_norm(w)
            if verb in ("mv", "truncate"):
                if _h11_prot(p, prefixes):
                    return p
            elif i == n - 1 and _h11_prot(p, prefixes):
                return p
    return None


def cmd_h11_dest_hit(argv) -> int:
    flat = argv[0] if len(argv) > 0 else ""
    prefixes = argv[1] if len(argv) > 1 else ""
    hit = h11_dest_hit(flat, prefixes)
    if hit is None:
        return _contract.EXIT_FAIL          # 1 = 未命中(业务性结果,不是环境失败)
    _contract.write_text(hit)
    return _contract.EXIT_OK


def strip_noncode(s: str) -> str:
    """剥掉行尾注释(供 H11 判定用)。逐字符,与 bash 旧实现逐条对齐:
      · 引号内(含 awk 的重定向)原样保留——那是真会落盘的写,剥掉等于开后门;
      · `\\` 转义下一个字符(整对保留);
      · `#` 前是行首/空白才算注释起点(否则 a#b、URL 片段被吃掉一半);
      · 注释起点的**其余部分一律丢弃**(这就是"剥")。
    ⚠ bash 里那句 `_prev="${_s:_i:1}"`(转义分支内)会被循环末尾的 `_prev="$_ch"`
      覆盖,所以 prev 语义 = 当前字符。这里按**覆盖后**的行为复刻。
    """
    out = []
    prev = ""
    i = 0
    n = len(s)
    inq = ""
    while i < n:
        ch = s[i]
        if inq:
            out.append(ch)
            if ch == inq:
                inq = ""
        elif ch == '"' or ch == "'":
            inq = ch
            out.append(ch)
        elif ch == "\\":
            out.append(ch)
            i += 1
            if i < n:
                out.append(s[i])
        elif ch == "#":
            if prev == "" or prev == " " or prev == "\t":
                return "".join(out)
            out.append(ch)
        else:
            out.append(ch)
        prev = ch
        i += 1
    return "".join(out)


def cmd_strip_noncode(argv) -> int:
    return _stdin_cmd(strip_noncode)


def _stdin_cmd(fn) -> int:
    _contract.write_text(fn(_contract.read_stdin_text()))
    return _contract.EXIT_OK


def cmd_fold_lines(argv) -> int:
    return _stdin_cmd(fold_lines)


def cmd_mask_readonly(argv) -> int:
    return _stdin_cmd(mask_readonly)


def cmd_mask_msg(argv) -> int:
    return _stdin_cmd(mask_msg)


def cmd_mask_search_pat(argv) -> int:
    return _stdin_cmd(mask_search_pat)


def cmd_is_readonly_verb(argv) -> int:
    _contract.write_text("0" if is_readonly_verb(argv[0] if argv else "") else "1")
    return _contract.EXIT_OK


_COMMANDS = {
    "mask_readonly": cmd_mask_readonly,
    "mask_msg": cmd_mask_msg,
    "mask_search_pat": cmd_mask_search_pat,
    "fold_lines": cmd_fold_lines,
    "split_words": cmd_split_words,
    "write_targets": cmd_write_targets,
    "noncode_payload": cmd_noncode_payload,
    "h11_scan": cmd_h11_scan,
    "foreign_cred_path": cmd_foreign_cred_path,
    "h11_dest_hit": cmd_h11_dest_hit,
    "strip_noncode": cmd_strip_noncode,
    "is_readonly_verb": cmd_is_readonly_verb,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write(
            "hd: 未知子命令 %r(可用: %s)\n"
            % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS)))
        )
        return _contract.EXIT_FAIL
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))