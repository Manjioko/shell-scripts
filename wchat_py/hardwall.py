#!/usr/bin/env python3
"""hard_deny_cmd 的 Python 移植（B 阶段第 2 步，风险 1）。

第一版目标：**rule/soft parity**（与 tests/hdcmd_baseline.tsv 对齐），消息文案次之。
规则顺序照 bash（wchat.sh 4727–4937）：H1→H9、H13、H14、H10、H11、H12(auto)。
谓词全部复用已收口的 Python：hd_re（正则）、hd（mask_readonly/mask_msg/mask_search_pat 等）。
"""
import re

from wchat_py import hd
from wchat_py import hd_re as R

_ASCII = re.ASCII


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


def _creds_all_exempt(c):
    """保守版：整条命令的凭据引用都必须是**相对本工作根**的路径才豁免。

    简化判据：出现家目录/绝对系统路径的凭据引用即不豁免（照拒）；纯相对路径（如项目级 .pypirc）豁免。
    """
    c = c or ""
    if re.search(r"(^|[\s;&|<>'\"])(~|/Users/|/home/|\$HOME|/root)", c):
        return False
    for m in re.finditer(r"(^|[\s;&|<>'\"])(/[^\s;&|<>'\"]*)", c):
        p = m.group(2)
        if not (p == "/tmp" or p.startswith("/tmp/")):
            return False
    return True


def _install_hit(c):
    """近似 hd_install_tail 的"这是安装类动作"判定（非 auto 即拒；源替换也归 H14）。"""
    return re.search(
        r"(^|[\s;&|(])(pip[0-9.]*\s+install|python[0-9.]*\s+-m\s+pip\s+install|"
        r"npm\s+(install|i|add)|yarn\s+(add|install)|pnpm\s+(add|install)|"
        r"brew\s+install|gem\s+install|cargo\s+install|go\s+install|"
        r"apt-get\s+install|apt\s+install|dpkg\s+-i|pipx\s+install)", c)


def rule(cmd):
    """返回 (rule, soft)。rule 非空即拒绝；soft 非空表示降级出口。"""
    c = cmd or ""

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
            and _recheck(c, hd.mask_msg, ("hd_re_secret_dir", "hd_re_keychain", "hd_re_keyfile", "hd_re_shadow")) \
            and not _creds_all_exempt(c):
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
                 r"git\s+config\s+(--global|--system)|networksetup|htpasswd|codesign|xattr\s+(-w|-d|--delete|--set))"
                 r"(\s|$)", c):
        return "H13", ""
    # H14 安装类动作（非自动放行即拒；源替换同归 H14）
    if _install_hit(c):
        return "H14", ""
    # H10 外发敏感内容
    if _m("hd_re_upload", c) and _m("hd_re_upload_arg", c) and _m("hd_re_secret_ref", c):
        return "H10", ""
    # H11 重定向/写动词落系统路径
    if _m("hd_re_sys_redirect", c) or (
            _m("hd_re_write_verb", c)
            and re.search(r"(^|[ \t=&])/(etc|usr|bin|sbin|System|Library|var|private)/", c)):
        return "H11", ""
    return "", ""


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "-":
        for ln in sys.stdin:
            print("%s\t%s" % rule(ln.rstrip("\n")))