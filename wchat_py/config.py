"""config —— W12「启动期配置」的 Python 侧读取/校验(见 docs/migration-to-python.md §八)。

放的是**启动后不变**的配置(正则/名单/根路径)。**单一出处仍在 bash**(顶层赋值):
bash 侧 `py_cfg_dump` 把这份配置导出成 JSON,Python 侧读它。这样"读全局的大块"
(`hard_deny_path` 等)将来只需拿到这份配置对象即可变纯判定。

退出码(见 `_contract`):
  0  成功
  1  校验失败(缺键/多键/类型不符)—— 调用方**不回退**
  2  未知子命令

用法:
  python3 -m wchat_py.config keys scalar|array   # 打印键名,一行一个
  python3 -m wchat_py.config validate            # stdin=配置 JSON -> 校验(rc 0/1)
  python3 -m wchat_py.config get <key>           # stdin=配置 JSON -> 打印该键的值
"""

from __future__ import annotations

import json
import sys

from . import _contract

# 标量配置(字符串)。hz_re_* 是硬墙正则,单一出处见 wchat.sh 顶层。
SCALAR_KEYS = [
    "hd_re_dev_sink", "hd_re_disk", "hd_re_dl_exec", "hd_re_dotfile",
    "hd_re_dotfile_cfg", "hd_re_force_push", "hd_re_history_rewrite",
    "hd_re_keychain", "hd_re_keyfile", "hd_re_killhost", "hd_re_pipe_exec",
    "hd_re_power", "hd_re_priv", "hd_re_proc_sub", "hd_re_secret_dir",
    "hd_re_secret_ref", "hd_re_shadow", "hd_re_su", "hd_re_sys_redirect",
    "hd_re_sys_redirect_wide", "hd_re_upload", "hd_re_upload_arg",
    "hd_re_wipe_flag", "hd_re_wipe_root", "hd_re_write_verb",
    "hd_h11_sys_prefixes",
    "WORK_ROOT", "HOME", "CT_HOME", "HISTORY_DIR",
    "AUTO_DIR", "AUTO_AUDIT_LOG", "AUTO_BACKUP_DIR",
]
# 数组配置(字符串列表)
ARRAY_KEYS = ["hd_cred_subdirs", "auto_tmp_prefixes"]

ALL_KEYS = SCALAR_KEYS + ARRAY_KEYS


def loads(text: str) -> "dict":
    return json.loads(text)


def load_file(path: str) -> "dict":
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8", "replace"))


def validate(cfg) -> "list":
    """返回错误信息列表(空列表 = 通过)。"""
    errs = []
    if not isinstance(cfg, dict):
        return ["配置不是 JSON 对象"]
    got = set(cfg.keys())
    want = set(ALL_KEYS)
    for k in sorted(want - got):
        errs.append("缺键: %s" % k)
    for k in sorted(got - want):
        errs.append("多键(未登记在 schema): %s" % k)
    for k in SCALAR_KEYS:
        if k in cfg and not isinstance(cfg[k], str):
            errs.append("键 %s 应为字符串,实际 %s" % (k, type(cfg[k]).__name__))
    for k in ARRAY_KEYS:
        if k in cfg and not (isinstance(cfg[k], list)
                             and all(isinstance(x, str) for x in cfg[k])):
            errs.append("键 %s 应为字符串数组" % k)
    return errs


def cmd_keys(argv) -> int:
    kind = argv[0] if argv else "scalar"
    keys = SCALAR_KEYS if kind == "scalar" else (ARRAY_KEYS if kind == "array" else None)
    if keys is None:
        sys.stderr.write("config: 未知类型 %r(可用: scalar/array)\n" % kind)
        return _contract.EXIT_ENV
    _contract.write_text("".join(k + "\n" for k in keys))
    return _contract.EXIT_OK


def cmd_validate(argv) -> int:
    try:
        cfg = loads(_contract.read_stdin_text())
    except Exception as e:
        sys.stderr.write("config: JSON 解析失败: %s\n" % e)
        return _contract.EXIT_FAIL
    errs = validate(cfg)
    if errs:
        sys.stderr.write("config: 校验失败:\n  " + "\n  ".join(errs) + "\n")
        return _contract.EXIT_FAIL
    return _contract.EXIT_OK


def cmd_get(argv) -> int:
    key = argv[0] if argv else ""
    try:
        cfg = loads(_contract.read_stdin_text())
    except Exception as e:
        sys.stderr.write("config: JSON 解析失败: %s\n" % e)
        return _contract.EXIT_FAIL
    if key not in cfg:
        sys.stderr.write("config: 无此键 %r\n" % key)
        return _contract.EXIT_FAIL
    v = cfg[key]
    _contract.write_text(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    return _contract.EXIT_OK


_COMMANDS = {
    "keys": cmd_keys,
    "validate": cmd_validate,
    "get": cmd_get,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write(
            "config: 未知子命令 %r(可用: %s)\n"
            % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS)))
        )
        return _contract.EXIT_ENV
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

