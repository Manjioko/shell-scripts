"""authparse —— 审批器回复解析(迁移自 wchat.sh 的 `auth_parse_verdict`)。

提示词(auth_sys_tail 的补充口径)强制判 N 时回「N编号:原因」——整行**没有空格**。
旧解析写的是"剥掉第一个空格分隔的 token",规范格式下首个 token 就是整行,理由被
连字吃干恒为空。三条口径(与旧实现逐条对齐,test_hardwall.sh T19 钉住):
  ① 首字符定 Y/N,其余一律 N(fail-closed);
  ② 编号必须保住(它是提示词 N 列表的序号,人靠它知道撞了哪一条);
  ③ 模型违规没写 N 前缀时,整行当理由留下 —— 丢一段看不懂的文本,也比编一句
     确定的假理由好。

退出码(见 `_contract`):
  0  成功,单行 JSON 在 stdout:`{"verdict":"Y|N","code":"...","reason":"..."}`
  1  未知子命令

用法:
  python3 -m wchat_py.authparse verdict <审批器原始回复>   # -> 单行 JSON
"""

from __future__ import annotations

import json
import re
import sys

from . import _contract

# POSIX [:space:] 在常见 locale 下就是这几个 ASCII 字节 —— 不要用 Python 的 \s
# (它会顺带吃 unicode 空格,与 bash [[:space:]] 不等价)。
_WS = r"[ \t\n\r\f\v]"
_RE_NUM = re.compile(r"^[Nn]" + _WS + r"*([0-9]+)" + _WS + r"*[:：\-]?(.*)$")
_RE_NC = re.compile(r"^[Nn]" + _WS + r"*[:：\-]?(.*)$")
_RE_LEAD_WS = re.compile(r"^" + _WS + r"+")

REASON_MAX = 80


def parse_verdict(raw: str) -> "dict":
    """把审批器一行回复解析成 (verdict, code, reason)。与 bash 逐字符对齐。"""
    line = (raw or "").split("\n", 1)[0].replace("\r", "")   # head -n1 + tr -d '\r'
    if line[:1] in ("Y", "y"):
        return {"verdict": "Y", "code": "", "reason": ""}
    code = ""
    m = _RE_NUM.match(line)
    if m:
        code = m.group(1)
        rest = m.group(2)
    else:
        m2 = _RE_NC.match(line)
        rest = m2.group(1) if m2 else line
    rest = _RE_LEAD_WS.sub("", rest)
    return {"verdict": "N", "code": code, "reason": rest[:REASON_MAX]}


def cmd_verdict(argv) -> int:
    raw = argv[0] if argv else ""
    _contract.write_text(json.dumps(parse_verdict(raw), ensure_ascii=False) + "\n")
    return _contract.EXIT_OK


_COMMANDS = {
    "verdict": cmd_verdict,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write(
            "authparse: 未知子命令 %r(可用: %s)\n"
            % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS)))
        )
        return _contract.EXIT_FAIL
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

