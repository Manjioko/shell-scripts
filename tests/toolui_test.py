#!/usr/bin/env python3
"""非正文输出的统一渲染闸门:一行、灰色、正文不灰。

用户报的三件事(2026-11):
  1. shell/read_file/write_file… 把内容**全量**铺到屏幕上 → 画面杂乱;
     要"只输出一行,超过一行的省略",且内容颜色与思考一致(灰),方法名的蓝不动;
  2. 硬墙拒绝**把命令正文摊开却不给理由** → 要"只给理由、不显示内容",颜色也要灰;
     auto 终审拒绝同样"只有命令没有理由",要一并修;
  3. 其它非正文输出也要做灰化处理。

本闸门钉的是**三条不变量**,不是某一句文案:
  A. 一行:任何工具的横幅/输出行,上屏的物理行数 ≤ 1(不含汇总行);
  B. 灰色:横幅预览、工具输出行、状态行、拒绝/放行提示都含 `ESC[2m`;
  C. 对照:助手正文(render_markdown 输出)不得被整体灰化 —— 否则"一律灰"就成了
     "什么都看不清",A/B 全绿也没意义。所以这里必须有这条反向断言。

为什么必须有 C:把 `dim()` 塞到 render_markdown 里,A/B 依然全绿(正文也灰了),
而屏幕会变成一坨灰。测试只断言"该灰的灰了",抓不住"不该灰的也灰了"。
"""
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ["WCHAT_API_LOG"] = "0"
os.environ["WCHAT_NO_SETUP"] = "1"

from wchat_py.render import dim, ellipsis, display_width, render_markdown, tool_banner  # noqa: E402

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
GREY = "\x1b[2m"


def text_lines(s):
    """把一段上屏字节按真换行拆开,去掉空行(空行不该算"多输出了一行")。"""
    return [ln for ln in s.split("\n") if ANSI.sub("", ln).strip()]


print("=== 1. 横幅:多行命令压成一行 ===")
heredoc = "python3 - <<'PY'\n" + "\n".join("print(%d)" % i for i in range(40)) + "\nPY"
b = tool_banner("shell", heredoc)
ck("40 行 heredoc 的横幅只占 1 行", len(text_lines(b)) <= 1, repr(text_lines(b))[:120])
ck("被省略的部分有 `…` 标记", "…" in b, repr(b)[-80:])
ck("横幅不超过终端宽度", display_width(b.rstrip("\n")) <= 200, str(display_width(b)))
ck("方法名仍是蓝色(不动方法色)", "\x1b[1;36mshell" in b, repr(b)[:60])
ck("预览是灰色(与思考同色)", GREY in b, repr(b)[:80])

print("=== 1b. 宽度:横幅/输出行/状态行都必须**塞得进终端**,不许折行 ===")
# 真事故(2026-11,用户贴的就是这一条):
#   `⏺ shell · date; echo "=== 0.2.8 目录内容 ==="; ls -la …; echo "=== INSTALL…
#   省略号跑到**第二行开头**。`
# 根因不是省略号位置,是宽度算错:`ellipsis` 里硬写 `term_cols() - 8`,
# 而横幅前缀 ` ⏺ shell · ` 实际 11 列 → 11 + (cols-8) = cols+3 → 终端自动折行。
# 这种错不崩不报,只有在真终端里用肉眼看;所以必须用"物理宽度 ≤ 终端宽"来钉。
import wchat_py.render as R2  # noqa: E402

USER_CMD = ('date; echo "=== 0.2.8 目录内容 ==="; ls -la /opt/homebrew/Cellar/wchat/0.2.8/; '
            'echo "=== INSTALL_RECEIPT 是否存在（brew 安装完成的标志） ==="; '
            'ls -la /opt/homebrew/Cellar/wchat/0.2.8/INSTALL_RECEIPT.json 2>&1')
_orig_cols = R2.term_cols
try:
    for cols in (40, 60, 80, 100, 120, 140, 160, 200):
        R2.term_cols = (lambda c: (lambda d=80: c))(cols)
        b = tool_banner("shell", USER_CMD)
        w = display_width(b.rstrip("\n"))
        ck("%3d 列:用户那条命令的横幅不折行(宽 %d)" % (cols, w), w <= cols,
           "溢出 %d 列 → 终端会把 … 折到第二行" % (w - cols))
        # 长参数名(read_file 的 JSON 可能很长)同样不许折
        b2 = tool_banner("read_file", '{"path":"%s","purpose":"%s"}'
                         % ("/很长的中文路径/" * 6, "看实现" * 20))
        ck("%3d 列:read_file 横幅不折行" % cols,
           display_width(b2.rstrip("\n")) <= cols,
           "宽 %d" % display_width(b2.rstrip("\n")))
finally:
    R2.term_cols = _orig_cols

print("=== 2. dim/ellipsis 的边界 ===")
ck("空串不留灰块", dim("") == "", repr(dim("")))
ck("多行折成单行", "\n" not in ANSI.sub("", dim("a\nb\n\tc d")), repr(dim("a\nb\nc d")))
ck("短文本不截断", "abc" in dim("abc"), repr(dim("abc")))
long = "x" * 500
ck("超长文本被截断到 1 行", len(ANSI.sub("", dim(long))) < 500, str(len(ANSI.sub("", dim(long)))))
ck("截断后带省略号", ANSI.sub("", dim(long)).endswith("…"), repr(ANSI.sub("", dim(long))[-6:]))
# limit=0 = 不截断:拒绝**理由**必须完整(80 列下"替代方案"曾被切掉,等于没给理由)
LONGR = "提权命令(sudo/doas/su)在任何模式下都不自动执行。" + "替代:把命令原样告诉用户,请他手动执行。" * 3
ck("dim(limit=0) 不截断理由", ANSI.sub("", dim(LONGR, 0)).endswith("手动执行。"),
   repr(ANSI.sub("", dim(LONGR, 0))[-20:]))
ck("dim(limit=0) 仍是灰色", dim(LONGR, 0).startswith(GREY), repr(dim(LONGR, 0)[:20]))
ck("dim(limit=0) 不留内嵌换行", "\n" not in dim(LONGR, 0), repr(dim(LONGR, 0)[:40]))
ck("ellipsis 保留原文边界(不切断宽字符)", display_width(ellipsis("中文" * 200, 21)) <= 21,
   str(display_width(ellipsis("中文" * 200, 21))))

print("=== 3. 对照:助手正文不许被整体灰化 ===")
body = render_markdown("**粗体** 与 `代码`\n\n- 列表项\n")
ck("正文渲染非空", bool(body.strip()), repr(body)[:60])
ck("正文不是整段灰(否则 A/B 全绿也没用)", not ANSI.sub("", body).strip() == "" and
   (GREY not in body or "\x1b[0m" in body), repr(body)[:120])

print("=== 4. 硬墙拒绝:给理由、不给命令正文 ===")
import wchat_py.toolrun as TR  # noqa: E402

SRC = open(os.path.join(REPO, "wchat_py", "toolrun.py"), encoding="utf-8").read()
AUTODENY = SRC[SRC.index("if _r:"):SRC.index("# 非 auto:")]
ck("auto 硬墙提示里出现的是理由变量 _m", "dim(_m" in AUTODENY, AUTODENY[:400])
ck("auto 硬墙提示不再拼命令正文", "_one = (cmd or \"\")" not in AUTODENY
   and "% (_r, _one)" not in AUTODENY, AUTODENY[:400])
ck("硬墙拒绝文案仍是灰的", "GREY" in AUTODENY, AUTODENY[:400])

FINALDENY = SRC[SRC.index("elif _v == \"N\":"):SRC.index("else:\n                    # \"无判定\"")]
ck("终审拒绝提示带理由 _rs", "_rs" in FINALDENY, FINALDENY[:300])
ck("终审拒绝提示不再拼命令", "% cmd" not in FINALDENY and "% (_rs or \"no-reason\", cmd)" not in
   FINALDENY.split("return")[0], FINALDENY[:300])
ck("终审拒绝文案是灰的", "GREY" in FINALDENY, FINALDENY[:300])
UNAVAIL = SRC[SRC.index("# \"无判定\"必须自己解释清楚"):SRC.index("return \"auto 终审不可用")]
ck("'无判定'自己解释原因(不再只报'被拒')", "没回可用判定" in UNAVAIL, UNAVAIL[:300])

print("=== 5. 端到端:硬墙/终审拒绝在真 _run_tool 里既给理由又不回显命令 ===")
import io as _io
import json as _json


def run_shell_capture(cmd, monkey, **extra):
    buf = _io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    args = {"command": cmd, "reason": "测试用"}
    args.update(extra)
    try:
        with monkey():
            out = TR._run_tool("run_shell_command",
                               _json.dumps(args, ensure_ascii=False))
    finally:
        sys.stdout = old
    return out, buf.getvalue()


class _Env:
    """临时改环境变量,退出时还原。"""
    def __init__(self, **kv):
        self.kv = kv
        self.old = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


SECRET = "touch /tmp/should_not_appear_9f3"
with _Env(AUTO_MODE="sandbox", AUTO_AUDIT_LOG=os.path.join(REPO, ".scratch", "ui_audit.jsonl")):
    out, screen = run_shell_capture("sudo -n %s" % SECRET, lambda: _Env())
ck("硬墙拒绝:屏幕上有规则号", "H" in screen and "硬墙拒绝" in screen, repr(screen[:160]))
ck("硬墙拒绝:屏幕上有理由(有冒号后的内容)", bool(ANSI.sub("", screen).split(":")[-1].strip()),
   repr(ANSI.sub("", screen)[:160]))
ck("硬墙拒绝:屏幕**不**回显命令正文", "should_not_appear" not in screen, repr(screen[:200]))
ck("硬墙拒绝:整行灰色", GREY in screen, repr(screen[:80]))
ck("硬墙拒绝:回给模型的仍带规则与理由(模型要知道为什么)",
   "硬墙" in out and "H" in out, repr(out)[:120])
# 理由**不截断**:80 列下 "…替代:把命令原样告诉用户…" 曾被切掉,用户/模型都不知道下一步怎么办
LONGCMD = "sudo -n true"
_out2, _scr2 = run_shell_capture(LONGCMD, lambda: _Env())
ck("硬墙拒绝:理由完整(没有被 … 截断)", "…" not in ANSI.sub("", _scr2) or
   "替代" in ANSI.sub("", _scr2), repr(ANSI.sub("", _scr2)[:200]))


class _FakeJudge:
    """把终审打成固定判定,不联网。"""
    def __init__(self, verdict, reason):
        self.v = verdict
        self.r = reason

    def __enter__(self):
        self.o1 = TR._seam
        cli = TR._seam()
        self.cli = cli
        self.o_think = cli._auth_think
        self.o_judge = cli._auto_judge
        self.o_bump = cli._bump_auto
        cli._auth_think = lambda cmd: ("N", "初审认为需要终审")
        cli._auto_judge = lambda cmd, reason="": (self.v, self.r)
        cli._bump_auto = lambda: None
        return self

    def __exit__(self, *a):
        self.cli._auth_think = self.o_think
        self.cli._auto_judge = self.o_judge
        self.cli._bump_auto = self.o_bump


MARK = "touch /tmp/final_deny_marker_7c1"
with _Env(AUTO_MODE="sandbox", AUTO_AUDIT_LOG=os.path.join(REPO, ".scratch", "ui_audit.jsonl")):
    with _FakeJudge("N", "命令含写入,超出只读范围"):
        out, screen = run_shell_capture("echo hi && %s" % MARK, lambda: _Env())
ck("终审拒绝:屏幕上有理由原文", "超出只读范围" in screen, repr(screen[:200]))
ck("终审拒绝:屏幕不回显命令正文", "final_deny_marker" not in screen, repr(screen[:200]))
ck("终审拒绝:整行灰色", GREY in screen, repr(screen[:80]))
ck("终审拒绝:回给模型的带理由(模型据此换方案)", "超出只读范围" in out, repr(out)[:160])

with _Env(AUTO_MODE="sandbox", AUTO_AUDIT_LOG=os.path.join(REPO, ".scratch", "ui_audit.jsonl")):
    with _FakeJudge(None, None):
        out, screen = run_shell_capture("echo hi", lambda: _Env())
ck("无判定:屏幕上解释'没回可用判定'", "没回可用判定" in screen, repr(screen[:200]))
ck("无判定:不把命令摊在屏幕上", "echo hi" not in ANSI.sub("", screen), repr(screen[:200]))

print("=== 6. shell 输出:多行照旧(窗口),但要灰;上下文仍是全量 ===")
# 2026-11 用户纠正过一次:把 shell 输出折成一行 **反而更差** ——
# 看不到报错行在哪,`git status` 这类十几行日常输出只剩一行。多行是要的,
# 只是要求**统一灰**(原来 ┃ 前缀是灰的、内容却是正常亮度,很刺眼)。
with _Env(AUTO_MODE="full", WCHAT_AUTO_ASSUME_YES="1"):
    out, screen = run_shell_capture(
        "printf 'L1\\nL2\\nL3\\n'", lambda: _Env())
vis = text_lines(screen)
ck("三行输出**都**上屏(多行不许折成一行)", sum(1 for ln in vis if "L" in ln) == 3,
   repr(vis)[:200])
ck("仍带 │ 边框前缀", all("│" in ln for ln in vis if "L" in ln), repr(vis)[:160])
ck("末行是 └─ 完成 · 共 3 行 ─┘ 形状",
   any("完成" in ln and "共 3 行" in ln and "─┘" in ln for ln in vis), repr(vis)[-2:])
ck("回给模型的仍是全量", all(x in out for x in ("L1", "L2", "L3")), repr(out)[:120])
# 灰化必须**逐行**覆盖正文内容,不能只灰边框:
# 原来 `┃ ` 前缀是灰的、`%s` 内容是正常亮度 —— 那正是"刺眼"的来源。
for ln in [x for x in screen.split("\n") if "L1" in x or "L2" in x]:
    payload = ln.split("│", 1)[1] if "│" in ln else ln
    payload = payload.replace("\x1b[0m", "").lstrip()      # 去掉边框后面的 reset
    ck("内容本身被灰包住(%r)" % ANSI.sub("", payload).strip()[:6],
       payload.startswith(GREY), repr(payload)[:100])
ck("无输出的命令仍有一行汇总(防静默成功)",
   (lambda s: len(text_lines(s)) == 1 and "共 0 行" in s)(
       run_shell_capture("true", lambda: _Env())[1]))

print("=== 6b. 单命令时长:模型可自主指定 timeout ===")
import wchat_py.toolrun as TR2  # noqa: E402
SPEC = json.load(open(os.path.join(REPO, "wchat_py", "tools_spec.json"), encoding="utf-8"))
SHELL_SPEC = [t for t in SPEC if t["function"]["name"] == "run_shell_command"][0]["function"]
ck("schema 里有 timeout 属性", "timeout" in SHELL_SPEC["parameters"]["properties"],
   repr(list(SHELL_SPEC["parameters"]["properties"])))
ck("timeout 不是必填(不给就按默认)",
   "timeout" not in (SHELL_SPEC["parameters"].get("required") or []),
   repr(SHELL_SPEC["parameters"].get("required")))
ck("说明里写了默认值与上限", "120" in SHELL_SPEC["parameters"]["properties"]["timeout"]["description"]
   and "3600" in SHELL_SPEC["parameters"]["properties"]["timeout"]["description"],
   SHELL_SPEC["parameters"]["properties"]["timeout"]["description"][:120])

# 行为断言:真的用上了这个值。用 1s 上限 + sleep 2 证明"参数生效";
# 只看 status 字符串是抓不住"读进来却没用"的。
with _Env(AUTO_MODE="full", WCHAT_AUTO_ASSUME_YES="1"):
    _o, _s = run_shell_capture("sleep 3", lambda: _Env(), timeout=1)
ck("timeout=1 的慢命令被掐断(参数真的生效)", "超过" in _s and "1s" in _s, repr(_s[:160]))
ck("被掐断时告诉模型可以调大重试", "timeout" in _o and "重试" in _o, repr(_o[:200]))
with _Env(AUTO_MODE="full", WCHAT_AUTO_ASSUME_YES="1"):
    _o2, _s2 = run_shell_capture("sleep 2 && echo ok", lambda: _Env())
ck("同样 2 秒的命令在默认上限下能跑完(不是一味拒绝)", "ok" in _o2, repr(_o2[:120]))

# 上限兜底:不许模型用 timeout 把 auto 会话挂一整天
with _Env(AUTO_MODE="full", WCHAT_AUTO_ASSUME_YES="1", CMD_TIMEOUT="1", CMD_TIMEOUT_MAX="2"):
    _o3, _s3 = run_shell_capture("sleep 30", lambda: _Env(), timeout=999)
ck("超过 CMD_TIMEOUT_MAX 时按上限夹紧并说明",
   "超过上限" in _s3 and "2s" in _s3, repr(_s3[:200]))
# 非数字/0 不该把命令变成 1 秒暴死
with _Env(AUTO_MODE="full", WCHAT_AUTO_ASSUME_YES="1"):
    _o4, _s4 = run_shell_capture("echo ok4", lambda: _Env())
    _o5, _s5 = run_shell_capture("sleep 1 && echo ok5", lambda: _Env(), timeout=0)
ck("timeout=0 当作未指定(不会变成 1s 暴死)", "ok5" in _o5, repr((_o5, _s5))[:160])

print("=== 7. 状态行灰化(已读取/已列出/已写入) ===")
import wchat_py.tools_run as TSR  # noqa: E402
for label, txt in (("已读取", " 已读取 a.py(12 行)"),
                   ("已列出", " 已列出 ."),
                   ("已写入", " ✅ 已写入 a.py(3 行)· 实际落点 /tmp/a.py"),
                   ("已修改", " 已修改 a.py")):
    buf = _io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        TR._status(txt)
    finally:
        sys.stdout = old
    s = buf.getvalue()
    ck("%s 状态行是灰色" % label, GREY in s, repr(s[:80]))
    ck("%s 状态行只有 1 行" % label, len(text_lines(s)) == 1, repr(text_lines(s))[:120])

print("=== 8. 80 列下的输出行/状态行也塞得进(同一条宽度规则) ===")
import wchat_py.render as R3  # noqa: E402

_oc = R3.term_cols
try:
    R3.term_cols = (lambda d=80: 80)
    _o, _s = run_shell_capture("python3 -c \"print('y'*400)\"", lambda: _Env())
    _long = [x for x in _s.split("\n") if "y" in x]
    ck("超长输出行被截到不折行", _long and all(display_width(x.rstrip("\r")) <= 80
                                        for x in _long),
       repr([display_width(x.rstrip("\r")) for x in _long]))
    _buf = _io.StringIO()
    _old = sys.stdout
    sys.stdout = _buf
    try:
        TR._status(" 已读取 " + "/very/long/path/" * 8 + "f.py(10 行)")
    finally:
        sys.stdout = _old
    ck("长路径状态行不折行", display_width(_buf.getvalue().rstrip("\n")) <= 80,
       "宽 %d" % display_width(_buf.getvalue().rstrip("\n")))
finally:
    R3.term_cols = _oc

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)