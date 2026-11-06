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

print("=== 1a. 横幅必须写出**实际生效**的限时(用户要的时间预期) ===")
# 模型能自己指定 timeout 之后,用户看不到"这条要等多久",既没法建立预期,
# 也不知道何时该 Ctrl+C 掐掉(模型幻觉出的 600 秒会让人干坐着等)。
# 关键是显示**实际生效值**,不能照抄模型传的 timeout(可能被上限夹紧)。
import wchat_py.toolrun as TRb  # noqa: E402


def _ban(args, **envkv):
    old = {k: os.environ.get(k) for k in envkv}
    os.environ.update(envkv)
    try:
        return ANSI.sub("", TRb._banner_text(
            "run_shell_command", json.dumps(args, ensure_ascii=False)))
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


_b0 = _ban({"command": "ls -la"}, CMD_TIMEOUT="120", CMD_TIMEOUT_MAX="3600")
ck("未指定 timeout:横幅写默认 120 秒", "限时120秒" in _b0, repr(_b0))
ck("未指定 timeout:命令仍在横幅里", "ls -la" in _b0, repr(_b0))
_b1 = _ban({"command": "npm test", "timeout": 600}, CMD_TIMEOUT="120", CMD_TIMEOUT_MAX="3600")
ck("模型指定 600 秒:横幅照实写", "限时600秒" in _b1, repr(_b1))
# 这条是核心:模型狮子大开口要 999999,横幅必须写夹紧后的 3600,
# 否则用户按 999999 秒建预期,而命令 3600 秒就被杀了(预期与事实相反)。
_b2 = _ban({"command": "x", "timeout": 999999}, CMD_TIMEOUT="120", CMD_TIMEOUT_MAX="3600")
ck("超上限:横幅写**夹紧后**的 3600,不写 999999",
   "限时3600秒" in _b2 and "999999" not in _b2, repr(_b2))
_b3 = _ban({"command": "x", "timeout": 0}, CMD_TIMEOUT="120", CMD_TIMEOUT_MAX="3600")
ck("timeout=0 当未指定:写默认 120 秒(不写 0 秒)", "限时120秒" in _b3, repr(_b3))
_b4 = _ban({"command": "x", "timeout": "abc"}, CMD_TIMEOUT="120", CMD_TIMEOUT_MAX="3600")
ck("非数字 timeout:退回默认 120 秒", "限时120秒" in _b4, repr(_b4))
# 后台任务不受 timeout 约束(立即返回 PID) —— 写"限时N秒"就是骗人。
_b5 = _ban({"command": "npm start", "run_in_background": True})
ck("后台任务:横幅写'后台'而不写限时",
   "后台" in _b5 and "限时" not in _b5, repr(_b5))
# 横幅变长后仍不许折行(限时前缀是新增的宽度负担),见下面 1b 的宽度断言。


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

print("=== 4. 硬墙/终审拒绝的源码级约束(auto 拒绝的输出行为见第 10 节)===")
import wchat_py.toolrun as TR  # noqa: E402

SRC = open(os.path.join(REPO, "wchat_py", "toolrun.py"), encoding="utf-8").read()
AUTODENY = SRC[SRC.index("if _r:"):SRC.index("# 非 auto:")]
ck("auto 硬墙拒绝走统一拒绝行(reject_line),不手写 ANSI",
   "reject_line" in AUTODENY and "\\033[1;31m" not in AUTODENY, AUTODENY[:400])
ck("auto 硬墙拒绝只传 mark,不传话术",
   "mark_of" in AUTODENY and "dim(_m" not in AUTODENY, AUTODENY[:400])

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

print("=== 6. shell 输出:跟随 -tf —— 默认折叠成收尾行;-tf=on 才展开多行 ===")
# 2026-11 先把多行改成"一行摘要"被用户否掉(看不到报错行);2026-12 用户又明确
# 要"默认折叠成一行、内容不展示",再进一步要求**不给第二个开关**:`-tf` 管思考文本
# 也管 shell 输出箱,`-sf` 已删除。两档都必须真跑:只测折叠会漏掉"想展开时打不开",
# 只测展开会漏掉"默认还是满屏灰字"。
with _Env(AUTO_MODE="full", WCHAT_AUTO_ASSUME_YES="1", WCHAT_THINK_FULL="0"):
    out, screen = run_shell_capture(
        "printf 'L1\\nL2\\nL3\\n'", lambda: _Env(WCHAT_THINK_FULL="0"))
vis = text_lines(screen)
ck("默认折叠:三行内容一个字都不上屏", not any("L1" in ln or "L2" in ln or "L3" in ln for ln in vis),
   repr(vis)[:200])
ck("默认折叠:只留一条收尾行", len(vis) == 1, repr(vis)[:200])
ck("默认折叠:收尾行是 `└─ 完成 · 共 3 行`(折叠档没有箱子,不收口 ─┘)",
   any(ANSI.sub("", ln).strip() == "└─ 完成 · 共 3 行" for ln in vis), repr(vis)[-2:])
ck("默认折叠:回给模型的仍是全量", all(x in out for x in ("L1", "L2", "L3")), repr(out)[:120])

with _Env(AUTO_MODE="full", WCHAT_AUTO_ASSUME_YES="1", WCHAT_THINK_FULL="1"):
    out, screen = run_shell_capture(
        "printf 'L1\\nL2\\nL3\\n'", lambda: _Env(WCHAT_THINK_FULL="1"))
vis = text_lines(screen)
ck("-tf=on:三行输出都上屏", sum(1 for ln in vis if "L" in ln) == 3, repr(vis)[:200])
ck("-tf=on:仍带 │ 边框前缀", all("│" in ln for ln in vis if "L" in ln), repr(vis)[:160])
ck("-tf=on:末行是 └─ 完成 · 共 3 行 ─┘(展开档有箱子才收口)",
   any(ANSI.sub("", ln).strip() == "└─ 完成 · 共 3 行 ─┘" for ln in vis), repr(vis)[-2:])
ck("-tf=on:回给模型的仍是全量", all(x in out for x in ("L1", "L2", "L3")), repr(out)[:120])
# 灰化必须**逐行**覆盖正文内容,不能只灰边框:
# 原来 `┃ ` 前缀是灰的、`%s` 内容是正常亮度 —— 那正是"刺眼"的来源。
for ln in [x for x in screen.split("\n") if "L1" in x or "L2" in x]:
    payload = ln.split("│", 1)[1] if "│" in ln else ln
    payload = payload.replace("\x1b[0m", "").lstrip()      # 去掉边框后面的 reset
    ck("内容本身被灰包住(%r)" % ANSI.sub("", payload).strip()[:6],
       payload.startswith(GREY), repr(payload)[:100])
with _Env(AUTO_MODE="full", WCHAT_AUTO_ASSUME_YES="1"):
    # 这条测的是"**防静默成功**":命令没输出,也必须有一行 `└─ 完成 · 共 0 行`
    #(默认折叠档不收口,所以这里只认"有且只有一条汇总行",不钉 ─┘)。
    # 不能断言"屏幕上恰好只有 1 行" —— auto 档下还会多一行
    # `auto 终审放行:assume-yes`,那是审批链的正常输出,不是本用例要管的东西。
    # 原来写成 `len(text_lines(s)) == 1`,于是:
    #   ① 在带 AUTO_MODE 的环境里跑 → 多那行 → 假红(而且它还会去真调模型);
    #   ② 换个没 openai 的解释器 → 终审失败 → 命令没跑 → 也是红。
    # 现在只认"有且只有一条汇总行"。
    _s0 = run_shell_capture("true", lambda: _Env())[1]
    _sums = [ln for ln in text_lines(_s0) if "└─" in ANSI.sub("", ln) and "共 0 行" in ANSI.sub("", ln)]
    ck("无输出的命令仍有一行汇总(防静默成功)", len(_sums) == 1, repr(_s0)[:200])

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
    with _Env(AUTO_MODE="full", WCHAT_AUTO_ASSUME_YES="1", WCHAT_THINK_FULL="1"):
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

print("=== 9. 交互确认块与 shell 工具块同形(2026-12 用户要求) ===")
# 用户原话:旧的 `⚠ 需要人工确认` + `    命令:<整条命令>` 与 `⏺ shell · …` 不是一个形状。
# 现在统一为 `   └─ <红标题>,命中: <原因>` + `   └─ 是否执行?(…): `,
# 且**不再回显命令正文**(上面那行横幅已经给过)。
import wchat_py.render as R4  # noqa: E402


class _In:
    def __init__(self, ans):
        self.ans = ans

    def isatty(self):
        return True

    def readline(self):
        return self.ans + "\n"


def _confirm_out(title, mark="", strict=True, ans="yes", ask="是否执行?",
                 default_yes=False, no_word="拒绝"):
    buf = _io.StringIO()
    oi, oo = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = _In(ans), buf
    try:
        ok = R4.confirm_block(title, mark=mark, strict=strict, ask=ask,
                              default_yes=default_yes, no_word=no_word)
    finally:
        sys.stdin, sys.stdout = oi, oo
    return ok, buf.getvalue()


_ok, _s = _confirm_out("高危命令(规则 H5)", mark="cp")
_pl = [ANSI.sub("", x) for x in text_lines(_s)]
ck("确认块 2 行:标题(含命中)+提问", len(_pl) == 2, repr(_pl))
ck("确认块每行都用统一前缀 └─", all(x.startswith(R4.STATUS_PREFIX) for x in _pl), repr(_pl))
ck("标题是红的(只染标题词)", "\x1b[1;31m高危命令(规则 H5)\x1b[0m" in _s, repr(_s)[:160])
ck("命中片段挂在标题行", "命中: cp" in _pl[0], repr(_pl[0]))
ck("确认块不再回显命令正文", "命令:" not in _s, repr(_s)[:200])
ck("严格档如实写'必须逐字 yes'(不写成 y 或 yes)", "必须逐字 yes" in _s, repr(_pl[-1]))
ck("严格档 y 不算批准", _confirm_out("t")[0] and
   not _confirm_out("t", ans="y")[0], "")

# 2026-12 用户第二次修:"原因描述明显是给 AI 模型看的,并不是给人看的"。
# 硬墙话术(含"替代:把配置写进…")**一律不上屏**,只通过返回值交给模型。
_LONG = ("写入/删除主目录下的配置文件或凭据目录被禁止(持久改变环境且不可回滚)。"
         "替代:把配置写进工作目录内的文件,或把内容打印出来交给用户自行放置(命中: cp )")
_ok, _s3 = _confirm_out("高危命令(规则 H5)", mark=R4.mark_of(_LONG))
ck("mark_of 能从话术里取出命中片段", R4.mark_of(_LONG) == "cp", R4.mark_of(_LONG))
ck("处置建议不上屏", "替代" not in _s3 and "不可回滚" not in _s3, repr(_s3)[:200])
ck("长话术只留标题+命中+提问三行", len(text_lines(_s3)) == 2, repr(_pl))

# 路径类确认:路径必须留住(没有上一行 ⏺ 横幅可参照),超长时按宽度截断。
_ok4, _s4 = _confirm_out("高危路径(不可读)", mark="/Users/jwan/.ct/mcporter.json",
                         ask="是否放行?")
ck("路径确认保留路径", "/Users/jwan/.ct/mcporter.json" in _s4, repr(_s4)[:200])
_oc2 = R4.term_cols
try:
    R4.term_cols = (lambda d=80: 80)
    _ok5, _s5 = _confirm_out("高危路径(不可读)", mark="/very/long/path/" * 8 + "f.txt",
                             ask="是否放行?")
finally:
    R4.term_cols = _oc2
ck("超长路径不折行(否则 └─ 会被顶到第 0 列)",
   all(display_width(x.rstrip("\r")) <= 80 for x in _s5.split("\n") if x.strip()),
   repr([display_width(x.rstrip("\r")) for x in _s5.split("\n") if x.strip()]))

_ok2, _s2 = _confirm_out("需要人工确认", strict=False)
ck("普通档接受 y", _confirm_out("t", strict=False, ans="y")[0], "")
ck("普通档提示写 y 或 yes", "y 或 yes" in _s2, repr(_s2)[:160])
ck("普通档没有命中时不拼空',命中:'", "命中" not in _s2, repr(_s2)[:160])


class _InKb:
    """在 readline 时抛 KeyboardInterrupt(Ctrl+C)。"""

    def isatty(self):
        return True

    def readline(self):
        raise KeyboardInterrupt()


_oi, _oo = sys.stdin, sys.stdout
sys.stdin, sys.stdout = _InKb(), _io.StringIO()
try:
    _kb = R4.confirm_block("t", strict=True)
    _kb_exc = None
except BaseException as _e:          # noqa: BLE001  —— 这里就是要抓"有没有冒泡"
    _kb, _kb_exc = None, _e
finally:
    sys.stdin, sys.stdout = _oi, _oo
ck("Ctrl+C 在确认块里当拒绝,不吐 traceback", _kb is False and _kb_exc is None,
   repr(_kb_exc))

print("=== 10. auto 硬墙拒绝的输出行为(不扒源码,直接看字节)===")
import tempfile as _tf  # noqa: E402
import wchat_py.hardwall as HW  # noqa: E402

_MSG10 = ("写入/删除主目录下的配置文件或凭据目录被禁止(持久改变环境且不可回滚)。"
          "替代:把配置写进工作目录内的文件,或把内容打印出来交给用户自行放置(命中: cp )")
_real_hd = HW.detail
_buf10 = _io.StringIO()
_o10, _i10 = sys.stdout, sys.stdin
sys.stdout, sys.stdin = _buf10, _io.StringIO("")
try:
    with _Env(AUTO_MODE="full", AUTO_AUDIT_LOG=os.path.join(_tf.mkdtemp(), "a.log")):
        HW.detail = lambda c, auto=False: ("H5", "", _MSG10)
        try:
            _ret10 = TR._run_tool("run_shell_command",
                                  _json.dumps({"command": "cp /tmp/a ~/.ct/x"}))
        finally:
            HW.detail = _real_hd
finally:
    sys.stdout, sys.stdin = _o10, _i10
_s10 = _buf10.getvalue()
_pl10 = [ANSI.sub("", x) for x in text_lines(_s10)]
ck("auto 拒绝给出规则号", any("H5" in x for x in _pl10), repr(_pl10))
ck("auto 拒绝给出命中片段", any("命中: cp" in x for x in _pl10), repr(_pl10))
ck("auto 拒绝不上屏处置话术(它给模型看)", "替代" not in _s10 and "不可回滚" not in _s10,
   repr(_s10)[:300])
ck("auto 拒绝不回显命令正文", "/tmp/a" not in _s10, repr(_s10)[:300])
ck("auto 拒绝用统一前缀且是灰的", R3.STATUS_PREFIX in _s10 and GREY in _s10,
   repr(_s10)[:200])
ck("auto 拒绝仍把话术交给模型(替代建议没丢)", "替代" in _ret10, repr(_ret10)[:200])
ck("auto 拒绝只有一行(不摊成两段)", len(_pl10) == 1, repr(_pl10))

print("=== 11. 所有 yes/no 提问共用一种语法 ===")
# 用户要求:提示文本/提示输入样式统一。以前屏幕上有五种写法:
#   `[y/N]`、`[Y/n]`、`(y/N)`、`(y 或 yes,n/回车 = 取消)`、`(必须逐字 yes,…)`。
# 现在只有三种口径,且都由 confirm_block 生成:
_okA, _sA = _confirm_out("普通确认", strict=False)
_okB, _sB = _confirm_out("严格确认", strict=True)
_okC, _sC = _confirm_out("默认是确认", strict=False, default_yes=True)
ck("普通口径写 'y 或 yes'", "y 或 yes" in _sA, repr(_sA))
ck("严格口径写 '必须逐字 yes'", "必须逐字 yes" in _sB, repr(_sB))
ck("默认是口径写 'n/回车 = 是'", "n/回车 = 是" in _sC, repr(_sC))
ck("默认是口径下回车算批准",
   _confirm_out("x", strict=False, default_yes=True, ans="")[0], "")
ck("默认是口径下打 n 不算批准",
   not _confirm_out("x", strict=False, default_yes=True, ans="n")[0], "")
for _nm, _sx in (("普通", _sA), ("严格", _sB), ("默认是", _sC)):
    ck("%s口径的提问行也用统一前缀" % _nm, R3.STATUS_PREFIX in _sx, repr(_sx))

# 源码级兜底:这几个文件里不许再出现 `[y/N]` / `[Y/n]` / `(y/N)` 这类另起炉灶的写法。
_LEGACY = re.compile(r"\[y/N\]|\[Y/n\]|\(y/N\)|是否保存该技能|退出前保存对话\?")
for _f in ("cli.py", "repl.py", "mcp_boot.py", "mcp_cmd.py", "skillinstall.py",
           "approve.py", "toolrun.py"):
    _src = open(os.path.join(REPO, "wchat_py", _f), encoding="utf-8").read()
    _hits = [x for x in _LEGACY.findall(_src)]
    ck("%s 里没有遗留的 y/N 式提问" % _f, not _hits, str(_hits))

# 提问动词必须贴切:默认的 `是否执行` 只配 shell 执行。别的动作(保存/授权/安装/
# 写入/放行)若照抄默认值,屏幕上就是"退出前保存对话 … 是否执行?",读着像问错了。
_VERBS = {
    "cli.py": ('ask="是否保存"',),
    "repl.py": ('ask="是否保存"',),
    "mcp_boot.py": ('ask="是否授权"',),
    "skillinstall.py": ('ask="是否继续"',),
    "mcp_cmd.py": ('ask="是否添加"', 'ask="是否写入"'),
    "approve.py": ('ask="是否放行"',),
}
for _f, _want in _VERBS.items():
    _src = open(os.path.join(REPO, "wchat_py", _f), encoding="utf-8").read()
    for _w in _want:
        ck("%s 的提问动词贴切(%s)" % (_f, _w), _w in _src, "")

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)