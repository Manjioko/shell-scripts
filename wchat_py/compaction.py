"""compaction —— 上下文自动压缩的**纯布局**逻辑(迁移自 wchat.sh)。

只放不碰网络、不碰 UI 的纯函数:
  · `due`          —— 要不要压缩(唯一口径,三处触发点共用);
  · `apply`        —— 压缩后的历史布局(摘要有没有保住,可离线断言)。
"摘要怎么拿"(发请求)与 `summary_pin_count`(与 `$_JQ_CONTENT_TEXT` 片段强耦合、
压缩探针有结构锁)留 bash。

退出码(见 `_contract`):
  0  `due`:要压 / `apply`:成功
  1  `due`:不压(业务性结果,不回退)
  2  `apply`:环境异常(调用方回退 bash)

用法:
  python3 -m wchat_py.compaction due <last_prompt_tokens> <threshold>     # rc0=压 rc1=不压
  python3 -m wchat_py.compaction apply <pin> <summary> <cut> <prefix>     # stdin=messages JSON
"""

from __future__ import annotations

import json
import re
import sys

from . import _contract

_UINT = re.compile(r"^[0-9]+$")


def _u(raw, default=0) -> int:
    s = str(raw)
    return int(s) if _UINT.match(s) else default


def due(t, g) -> bool:
    """要不要压缩。`g > 0` 必须显式判:光写 `t > g`,阈值被裁空/置 0 时任何 t>0 都成立,
    半装载环境会把每一轮都变成一次压缩(白烧请求 + 每次多一层有损摘要)。严格 `>` 不是 `>=`。
    """
    tt = _u(t, 0)
    gg = _u(g, 0)
    return gg > 0 and tt > gg


# 上下文越界的错误签名(与 master HD_OVF_SIG 逐字一致)。
# 命中 = 服务端明确说"超长",可以放心压缩后重发;不命中才看字节粗算兜底。
OVF_SIG = re.compile(
    r"(context (length|window|tokens)|maximum context|too many tokens"
    r"|token (limit|count).{0,20}(exceed|large)|prompt is too long"
    r"|input.{0,10}token.{0,20}exceed|reduce your (prompt|input)"
    r"|上下文(长度|窗口|过(长|大))|超出.{0,8}(长度|窗口|上限)|长度超限)",
    re.I)


def overflow_kind(err_text, est_tokens=0, gate=0) -> str:
    """这次失败是不是"上下文越界"。返回 'sig'（接口自己说的，最可信）/ 'est'（字节粗算兜底）/
    'none'。

    为什么分成两种：字节粗算(bytes*2/5)**两个方向都会错** —— 对英文/代码偏高，
    又完全漏算每次请求都要发的 tools schema 与技能清单。所以优先信服务端的错误文本，
    粗算只做兜底。master 拆出 CTX_OVERFLOW_GATE 就是为了不必把粗算口径跟着压缩点一起抬。
    """
    if err_text and OVF_SIG.search(str(err_text)):
        return "sig"
    if _u(est_tokens, 0) > _u(gate, 0) > 0:
        return "est"
    return "none"


def apply(messages_json: str, pin, summary: str, cut, prefix: str) -> str:
    """压缩后的历史布局:`[system] + [锁定旧摘要] + [新摘要?] + [tail]`。

    pin 钳到 `cut-1`(锁定块不许越过切点,否则同一条消息被数两遍);`summary` 为空 = 压缩
    失败,只做硬截断,但**已锁定的旧摘要必须原样留下**(旧实现连摘要一起丢 = 凭空删一层历史)。
    """
    try:
        msgs = json.loads(messages_json)
    except Exception:
        msgs = []
    pin = _u(pin, 0)
    cut = _u(cut, 0)
    pin_max = cut - 1 if cut > 1 else 0
    if pin > pin_max:
        pin = pin_max
    if summary:
        s = summary[:-1] if summary.endswith("\n") else summary   # jq rtrimstr("\n"):去一个尾换行
        new = (msgs[0:1] + msgs[1:1 + pin]
               + [{"role": "user", "content": prefix + "\n" + s}]
               + msgs[cut:])
    else:
        new = msgs[0:1] + msgs[1:1 + pin] + msgs[cut:]
    return json.dumps(new, ensure_ascii=False)


def cmd_due(argv) -> int:
    t = argv[0] if len(argv) > 0 else "0"
    g = argv[1] if len(argv) > 1 else "0"
    return _contract.EXIT_OK if due(t, g) else _contract.EXIT_FAIL


def cmd_apply(argv) -> int:
    pin = argv[0] if len(argv) > 0 else "0"
    summary = argv[1] if len(argv) > 1 else ""
    cut = argv[2] if len(argv) > 2 else "0"
    prefix = argv[3] if len(argv) > 3 else ""
    msgs = _contract.read_stdin_text()
    _contract.write_text(apply(msgs, pin, summary, cut, prefix))
    return _contract.EXIT_OK


_COMMANDS = {
    "due": cmd_due,
    "apply": cmd_apply,
}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write(
            "compaction: 未知子命令 %r(可用: %s)\n"
            % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS)))
        )
        return _contract.EXIT_FAIL
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))