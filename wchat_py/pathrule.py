"""pathrule —— 路径级硬墙裁决(迁移自 wchat.sh hard_deny_path)。

**纯判定**:给定"已归一路径 + 档位 + 会话白名单 + 一份小配置"就能算出 {rule,msg},
无 IO、无副作用(不审计/不弹确认/不落备份)。这正是 §八 说的"读全局的大块"的纯化形态:
配置(`HOME`/`WORK_ROOT`/凭据子目录/临时前缀)与**会话态**(`auto_mode`/`auto_allow_rw`)
都由调用方传入。

退出码(见 `_contract`):0=命中(rule 非空) / 1=未命中 / 2=未知子命令。

用法:
  python3 -m wchat_py.pathrule hard_deny_path <mode> <auto_mode> <home> <work_root> \
            <subdirs_nl> <tmp_prefixes_nl> <allow_rw_nl>      # stdin=path
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import sys

from . import _contract
from . import authz
from . import hd
from . import pathutil

# P2 系统路径名单(两平台并集;见 bash 原注释)。每条按"精确 或 前缀+/"匹配。
_SYS_PREFIXES = [
    "/", "/etc", "/usr", "/bin", "/sbin", "/System", "/Library", "/private",
    "/Applications", "/Volumes", "/opt", "/var/root", "/dev", "/proc", "/sys",
    "/run", "/boot", "/lib", "/lib64", "/srv", "/media", "/mnt", "/var", "/root",
]
# $HOME 下"凭据/会话数据"目录(P1),按 home/<x> 前缀匹配
_HOME_CRED_SUBS = [
    ".ssh", ".aws", ".gnupg", ".config/gh", ".config/gcloud", ".config/azure",
    ".azure", ".docker", ".kube", ".subversion", ".ct/history", ".ct/config",
    # MCP 清单:里面有 env/headers 的**占位与真实值**两种可能,按凭据级对待。
    # 清单本身不搬走模型就看不到 server 名单 —— 名单由程序启动时注入(L0),不经文件读取。
    ".ct/mcporter.json",
]
# 工作目录外的凭据**文件名**(P1)
_CRED_FILENAMES = frozenset((
    ".npmrc", ".netrc", "_netrc", ".pypirc", ".git-credentials", ".htpasswd",
    ".pgpass", "spnego-machine", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
))
_CRED_EXT = (".pem", ".key", ".p12", ".pfx")
_TMP_LITERALS = ["/tmp", "/private/tmp", "/var/tmp"]

_MSG_LINK = "这个路径是符号链接,真实落点在工作目录外:路径判定看链接名、读写跟随链接真身,目录内豁免对它不成立(否则一条链接就能穿过整份凭据/dotfile 名单)。auto 档直接拒,非 auto 档须用户本人逐字输入 yes。替代:让用户把真实文件放进工作目录,或直接给出边界外的真实路径(那条会按它自己的规则重新判定)。换链接、换路径写法都不改变判定"
_MSG_P1_HOMECRED = "凭据/会话数据路径不可访问(~/.ct/history 存对话正文、~/.ct/config 含接口凭据、~/.mcporter 是 MCP 的 vault 与 daemon 密钥):它会被读进上下文并随请求发给服务端。判定与档位无关,但出口有两条:auto 档直接拒(不提问),非 auto 档会向用户弹严格确认(必须逐字输入 yes),放不放行由用户决定。换写法、换路径表达或重试都不改变判定,也不要替用户作答;与 shell 的 H6 同判。替代:请用户自己贴出所需的值"
_MSG_P1_FOREIGN = "另一个用户家目录下的凭据/会话目录(如 /home/<user>/.ssh、/Users/<user>/.aws)不可访问:不是本会话的工作目录,也不是你本人的配置。内容会进上下文并随请求发给服务端。判定与档位无关:auto 档直接拒,非 auto 档须用户本人逐字输入 yes。--allow-write 与换路径表达都放不开这条(P1 在任何名单判定之前),与 shell 的 H6 同判。替代:请用户自己贴出所需的值,或由用户在自己的终端里操作"
_MSG_P1_FILE = "工作目录外的凭据文件不可访问(按文件名判定:.npmrc/.netrc/.pypirc/.pgpass/.git-credentials/私钥等):内容会进上下文并随请求发给服务端。auto 档直接拒,非 auto 档会向用户弹严格确认(必须逐字输入 yes),放不放行由用户决定;换位置、换写法都不改变判定,与 shell 的 H6 同判。替代:请用户自己贴出所需的值;要查 registry 用 npm config get registry"
_MSG_P1_KEY = "工作目录外的私钥/证书文件(.pem/.key/.p12/.pfx/id_rsa 等)不可访问,内容与凭据文件同判:auto 档直接拒,非 auto 档须用户本人逐字输入 yes 才放行。替代:请用户自己提供所需的值"
_MSG_P2_SYS = "写入系统路径被禁止(持久改变环境,且不在 checkpoint 覆盖范围)。替代:写到工作目录内或 /tmp。注意 --allow-write 放不开这条(P2 在越界名单判定之前就已拒绝,名单只对'工作目录外的普通路径'生效且仅 full 档),确需改系统文件请用户自己执行"
_MSG_P2_HOME = "写入主目录顶层或隐藏配置目录(~/.xxx)被禁止。替代:写到工作目录内或 /tmp,把要落的内容打印出来交给用户自行放置"


def _norm(p, home, work_root):
    r = pathutil.norm_path(p, home, work_root)
    return r if r else p


def _in_root(np, home, work_root):
    return pathutil.in_root_exempt(np, work_root, home)


def link_escapes_root(np, home, work_root):
    """末段是已存在符号链接且真身落在工作根外(解析不出来也算逃逸)。见 bash。"""
    if not os.path.islink(np):
        return False
    tgt = np
    i = 0
    while os.path.islink(tgt) and i < 8:
        try:
            tgt = os.readlink(tgt)
        except OSError:
            return True
        if not tgt:
            return True
        if not tgt.startswith("/"):
            tgt = (posixpath.dirname(tgt) or ".") + "/" + tgt
        i += 1
    npn = pathutil.norm_path(tgt, home, work_root)
    if not npn:
        return True
    return not _in_root(npn, home, work_root)


def hard_deny_path(path, mode, auto_mode, home, work_root, subdirs, tmp_prefixes, allow_rw):
    """返回 {"rule": str, "msg": str};rule 非空 = 命中硬墙。"""
    rule = ""
    msg = ""

    def note(f):
        nonlocal msg
        if not f or "(命中:" in msg:
            return
        msg = msg + "(命中: " + f[:80] + ")"

    np = _norm(path, home, work_root)

    # LINK:目录内豁免不成立(一条链接就能穿过整份名单)。必须排在最前。
    if _in_root(np, home, work_root) and link_escapes_root(np, home, work_root):
        rule, msg = "LINK", _MSG_LINK
        note(np)
        return {"rule": rule, "msg": msg}

    # P1:$HOME 下凭据/会话目录
    for s in _HOME_CRED_SUBS:
        if np.startswith(home + "/" + s):
            rule, msg = "P1", _MSG_P1_HOMECRED
            note(np)
            return {"rule": rule, "msg": msg}
    for s in (home + "/.mcporter", home + "/.local/share/mcporter"):
        if np == s or np.startswith(s + "/"):
            rule, msg = "P1", _MSG_P1_HOMECRED
            note(np)
            return {"rule": rule, "msg": msg}
    # P1:他人家目录下的凭据目录
    if hd.foreign_cred_path(np, home, subdirs):
        rule, msg = "P1", _MSG_P1_FOREIGN
        note(np)
        return {"rule": rule, "msg": msg}
    # P1:工作目录外的凭据**文件名**
    if not _in_root(np, home, work_root):
        base = np.rsplit("/", 1)[-1]
        if base in _CRED_FILENAMES:
            rule, msg = "P1", _MSG_P1_FILE
            note(np)
            return {"rule": rule, "msg": msg}
        if np.endswith(_CRED_EXT) or base in ("id_rsa", "id_ed25519", "id_ecdsa"):
            rule, msg = "P1", _MSG_P1_KEY
            note(np)
            return {"rule": rule, "msg": msg}

    # P2/P3(仅写侧)
    if mode == "rw":
        if _in_root(np, home, work_root):
            return {"rule": "", "msg": ""}
        for tp in _TMP_LITERALS + list(tmp_prefixes):
            if np == tp or np.startswith(tp + "/"):
                return {"rule": "", "msg": ""}
        if np in ("/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/zero"):
            return {"rule": "", "msg": ""}
        for sp in _SYS_PREFIXES:
            if np == sp or np.startswith(sp + "/"):
                rule, msg = "P2", _MSG_P2_SYS
                note(np)
                return {"rule": rule, "msg": msg}
        if np == home or re.match(re.escape(home) + r"/\.[A-Za-z]", np):
            rule, msg = "P2", _MSG_P2_HOME
            note(np)
            return {"rule": rule, "msg": msg}
        if auto_mode:
            if not (np == work_root or np.startswith(work_root + "/")):
                if auto_mode != "full" or not authz.auto_allow_hit(np, "rw", [], allow_rw, home, work_root):
                    rule = "P3"
                    if auto_mode == "full":
                        msg = ("越界写入被拒绝:该路径不在用户的 --allow-write 名单里。替代:"
                               "写到工作目录内或 /tmp,或把内容打印出来交给用户放置"
                               + (";确需写它请用户重启时加 --allow-write '%s'" % path if path else ""))
                    else:
                        msg = ("越界写入被拒绝:auto=%s 档不放开工作目录外的写(--allow-write 只在 "
                               "-auto=full 生效)。替代:写到工作目录内或 /tmp,或把内容打印出来交给用户自行放置"
                               % auto_mode)
                    return {"rule": rule, "msg": msg}
    return {"rule": "", "msg": ""}


def _nl_list(s):
    return [x for x in s.split("\n") if x]


def cmd_hard_deny_path(argv) -> int:
    a = list(argv) + [""] * 7
    path = _contract.read_stdin_text()
    r = hard_deny_path(path, a[0], a[1], a[2], a[3],
                       _nl_list(a[4]), _nl_list(a[5]), _nl_list(a[6]))
    _contract.write_text(json.dumps(r, ensure_ascii=False))
    return _contract.EXIT_OK if r["rule"] else _contract.EXIT_FAIL


_COMMANDS = {
    "hard_deny_path": cmd_hard_deny_path,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write("pathrule: 未知子命令 %r\n" % (argv[0] if argv else ""))
        return _contract.EXIT_ENV
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))