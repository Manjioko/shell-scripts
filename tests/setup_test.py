#!/usr/bin/env python3
"""配置检测与填写的自检。末行 RESULT ...。

分两块,对应两个职责:
  · `diagnose()`  —— wchat 启动时用:只判断、只解释、退出码 2,不进循环
  · `configure()` —— `wchat -config` 用:逐项问/一次给完 → 校验 → 600 落盘 → 验连通

断言全部是行为:
  1. 缺什么就说缺什么,并且**不进循环**、退出码 2、末尾写明原因;
  2. 每项**独立一问**,值里带空格/斜杠/等号都不出错(不让用户做排版);
  3. key **回显**(用户要能确认自己粘对了);但汇总行/日志里一律打码;
  4. 校验能指到具体错处(缺项/URL 形态/key 含空格/两键粘一起/模型名带空格/引号不配对);
  5. 校验不通过**一个字节都不写**;
  6. 写盘 600、保留其它键、旧值回车保留、只改一项不重填三项;
  7. 强制验连通:通了才 rc=0;不通 rc=2 且**已填的值不丢**;
  8. `--check` 只看不写;`--no-verify` 跳过连通;无 tty 时给可复制的一行命令;
  9. 已配置时完全不出现(不能每次启动都弹)。
"""
import io
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from wchat_py import setup  # noqa: E402

FAILS = []
PASSES = [0]


def ck(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def fresh(drop_env=True):
    d = tempfile.mkdtemp(prefix="cfg_")
    if drop_env:
        for k in ("BASE_URL", "API_KEY", "WCHAT_API_KEY", "FLASH_MODEL", "PRO_MODEL",
                  "WCHAT_SETUP_NO_VERIFY", "WCHAT_NO_SETUP", "API_KEY_HEADER"):
            os.environ.pop(k, None)
    os.environ["HOME"] = d
    os.environ["CT_HOME"] = os.path.join(d, ".ct")
    return d


class FakeIn(object):
    def __init__(self, lines, tty=True):
        self._it = iter(lines)
        self._tty = tty

    def isatty(self):
        return self._tty

    def readline(self):
        try:
            return next(self._it)
        except StopIteration:
            return ""

    def fileno(self):
        raise io.UnsupportedOperation("fake")


class FakeOut(io.StringIO):
    def isatty(self):
        return True


def cap(fn, *a, **kw):
    """跑 fn,返回 (rc, 屏幕文本)。stdin/stdout 都换掉(避免真读键盘)。"""
    old_in, old_out = sys.stdin, sys.stdout
    out = FakeOut()
    sys.stdin, sys.stdout = FakeIn(kw.pop("lines", []), kw.pop("tty", True)), out
    try:
        rc = fn(*a, **kw)
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    return rc, out.getvalue()


# ---------- 1) diagnose:缺项 → 说清 + 退出码 2 + 不进循环 ----------
print("=== 1. 缺配置时只说不动 ===")
fresh()
ok, issues = setup.diagnose()
ck("diagnose 判为未就绪", ok is False)
ck("三条都点名", len(issues) == 3, repr(issues))
ck("原因标签是'缺少必要配置'", issues[0][0] == setup.R_MISSING, repr(issues[0][0]))
ck("说明里带字段名", all(any(k in m for m in (x[1] for x in issues))
                       for k in ("BASE_URL", "WCHAT_API_KEY", "FLASH_MODEL")), repr(issues))
rc, screen = cap(setup.ensure_ready, ["-f"])
ck("ensure_ready 返回 2(不进循环)", rc == 2, repr(rc))
ck("屏幕上写明退出码原因", "退出码 2：缺少必要配置" in screen, screen[-200:])
ck("屏幕上给了下一步命令", "wchat -config" in screen, screen[-260:])
ck("没有任何东西被写盘", not os.path.exists(setup.config_path()))

# ---------- 2) diagnose:配置齐了 → 放行 ----------
print("=== 2. 配置齐了就放行 ===")
fresh()
setup.apply({"BASE_URL": "https://x.example/v1/chat/completions",
             "WCHAT_API_KEY": "sk-ok123456", "FLASH_MODEL": "m1"})
ok2, iss2 = setup.diagnose()
ck("diagnose 就绪", ok2 is True and iss2 == [], repr((ok2, iss2)))
rc, screen = cap(setup.ensure_ready, ["-f"])
ck("已配置时 ensure_ready 返回 None(继续跑)", rc is None, repr(rc))
ck("已配置时屏幕上没有提示", screen.strip() == "", repr(screen))

# ---------- 3) diagnose:权限 644 → 拒载并给 chmod ----------
print("=== 3. 权限不对 ===")
d = fresh()
os.makedirs(os.path.dirname(setup.config_path()), exist_ok=True)
open(setup.config_path(), "w").write('BASE_URL="https://x/v1/chat/completions"\n')
os.chmod(setup.config_path(), 0o644)
ok3, iss3 = setup.diagnose()
ck("判为未就绪", ok3 is False, repr(ok3))
ck("原因是权限", iss3[0][0] == setup.R_PERM, repr(iss3[0][0]))
ck("说明里给出 chmod 600", "chmod 600" in iss3[0][1], repr(iss3[0][1]))

# ---------- 4) validate:每类坏输入都要指到点上 ----------
print("=== 4. 校验能指到点上 ===")


def bad(name, pairs, must):
    iss = setup.validate(pairs)
    joined = " | ".join(m for _k, m in iss)
    ck(name, any(must in m for _k, m in iss), joined)


good = {"BASE_URL": "https://x.example/v1/chat/completions",
        "WCHAT_API_KEY": "sk-abc123456789", "FLASH_MODEL": "deepseek-chat"}
ck("全对时无问题", setup.validate(good) == [], repr(setup.validate(good)))
bad("缺 key", {**good, "WCHAT_API_KEY": ""}, "还缺 WCHAT_API_KEY")
bad("缺 URL", {**good, "BASE_URL": ""}, "还缺 BASE_URL")
bad("缺模型", {**good, "FLASH_MODEL": ""}, "还缺 FLASH_MODEL")
bad("URL 不是 http", {**good, "BASE_URL": "api.deepseek.com"}, "http:// 或 https://")
bad("URL 没域名", {**good, "BASE_URL": "https:///v1"}, "没有域名")
bad("key 带空格", {**good, "WCHAT_API_KEY": "sk-aa bb"}, "不能有空格")
bad("两键粘一起", {**good, "WCHAT_API_KEY": "sk-aaFLASH_MODEL=m1"}, "混进了另一个设置项")
bad("引号不配对", {**good, "WCHAT_API_KEY": 'sk-aa"'}, "引号没配对")
bad("模型名带空格", {**good, "FLASH_MODEL": "deep seek"}, "不该含空格或中文")
bad("模型名带中文", {**good, "FLASH_MODEL": "深度求索"}, "不该含空格或中文")

# ---------- 5) normalize_base_url ----------
print("=== 5. 地址补齐 ===")
ck("/v1 补 /chat/completions",
   setup.normalize_base_url("https://x.example/v1") == "https://x.example/v1/chat/completions")
ck("根地址补 /chat/completions",
   setup.normalize_base_url("https://x.example") == "https://x.example/chat/completions")
ck("已是完整端点不动",
   setup.normalize_base_url("https://x.example/v1/chat/completions")
   == "https://x.example/v1/chat/completions")
ck("尾斜杠不产生双斜杠",
   setup.normalize_base_url("https://x.example/v1/")
   == "https://x.example/v1/chat/completions")

# ---------- 6) 交互逐项填写:一项一问,值里带空格也行 ----------
print("=== 6. 逐项填写 ===")
fresh()
sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
tmpd = tempfile.mkdtemp(prefix="cfg_stub_")
rp = os.path.join(tmpd, "r.json")
open(rp, "w").write('[{"type":"text","text":"OK"}]')
env = dict(os.environ, STUB_LOG=os.path.join(tmpd, "log.jsonl"), STUB_REPLY=rp,
           STUB_AUTH_DENY_IF="", STUB_FINAL_DENY_IF="")
stub = subprocess.Popen([sys.executable, os.path.join(REPO, "tests/stub_gateway.py"), str(port)],
                        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", port), 0.2).close(); break
    except OSError:
        time.sleep(0.1)
try:
    # 输入顺序:组名 → BASE_URL → WCHAT_API_KEY → FLASH_MODEL → PRO_MODEL(空=跟随)
    rc, screen = cap(setup.configure, [],
                     lines=["deepseek", "http://127.0.0.1:%d/v1" % port,
                            "sk-demo123456789", "demo-chat", ""])
finally:
    stub.terminate()
ck("逐项填完 rc=0", rc == 0, "rc=%s %r" % (rc, screen[-300:]))
ck("屏幕说配置成功", "配置成功" in screen, screen[-200:])
ck("写了 key", setup.read_config().get("WCHAT_API_KEY") == "sk-demo123456789",
   repr(setup.read_config()))
ck("URL 被补齐成完整端点",
   setup.read_config().get("BASE_URL", "").endswith("/chat/completions"),
   setup.read_config().get("BASE_URL"))
ck("PRO_MODEL 跟着 FLASH_MODEL",
   setup.read_config().get("PRO_MODEL") == "demo-chat", repr(setup.read_config()))
ck("权限 600", oct(stat.S_IMODE(os.stat(setup.config_path()).st_mode)) == "0o600",
   oct(stat.S_IMODE(os.stat(setup.config_path()).st_mode)))
ck("key 有打码确认(收到 N 字符 + 首尾)",
   "已收到 %d 个字符" % len("sk-demo123456789") in screen and "sk-de…789" in screen,
   screen[-400:])
ck("每项都单独问过", all(f in screen for f in setup.REQUIRED), screen[:400])
# ---- 写盘格式必须与 ctmodel 一致(行尾 # @model <组名>) ----
_txt = open(setup.config_path()).read()
ck("每行都带 @model 标记", _txt.count("# @model ") >= 4, _txt)
ck("组名是用户填的 deepseek", "# @model deepseek" in _txt, _txt)
ck("活动组没有被注释", '\nBASE_URL=' in "\n" + _txt, _txt)
ck("active_group() 认得这组", setup.active_group() == "deepseek", repr(setup.active_group()))
ck("read_groups() 能读出来", "deepseek" in setup.read_groups(), repr(list(setup.read_groups())))

# ---------- 7) 校验不过:一个字节都不写 ----------
print("=== 7. 校验不过不写盘 ===")
fresh()
rc, screen = cap(setup.configure, [],
                 lines=["ds", "api.deepseek.com", "sk-aa bb", "deep seek"])
ck("rc=2", rc == 2, repr(rc))
ck("没有写任何文件", not os.path.exists(setup.config_path()), setup.config_path())
ck("三条问题都指出来了",
   sum(1 for m in ("http:// 或 https://", "不能有空格", "不该含空格") if m in screen) == 3,
   screen[-400:])
ck("提示可以重跑", "重跑 wchat -config" in screen, screen[-200:])

# ---------- 8) 只改一项:回车保留其余 ----------
print("=== 8. 只改一项 ===")
fresh()
setup.write_config({"BASE_URL": "https://keep.example/v1/chat/completions",
                    "WCHAT_API_KEY": "sk-old111111", "FLASH_MODEL": "old-model",
                    "PRO_MODEL": "old-model", "CTX_WINDOW": "1234"})
os.environ["WCHAT_SETUP_NO_VERIFY"] = "1"
try:
    rc, screen = cap(setup.configure, [], lines=["", "", "sk-new222222", "", ""])
finally:
    os.environ.pop("WCHAT_SETUP_NO_VERIFY", None)
cfg = setup.read_config()
ck("只改了 key", cfg.get("WCHAT_API_KEY") == "sk-new222222", repr(cfg))
ck("跳过验证时 rc=0(WCHAT_SETUP_NO_VERIFY)", rc == 0, repr(rc))
ck("URL 保留原值", cfg.get("BASE_URL") == "https://keep.example/v1/chat/completions", repr(cfg))
ck("模型保留原值", cfg.get("FLASH_MODEL") == "old-model", repr(cfg))
ck("其它键没被动", cfg.get("CTX_WINDOW") == "1234", repr(cfg))
ck("屏幕上显示了已有值(打码)", "old-model" in screen, screen[:400])

# ---------- 9) 一次给完 / --check / --no-verify ----------
print("=== 9. 命令行形态 ===")
fresh()
rc, screen = cap(setup.configure, ["--check", "--no-verify",
                                   "BASE_URL=https://x.example/v1 WCHAT_API_KEY=sk-abc123456789 "
                                   "FLASH_MODEL=m1"], tty=False)
ck("--check --no-verify rc=0(格式对)", rc == 0, "rc=%s %r" % (rc, screen[-200:]))
ck("--check 不写盘", not os.path.exists(setup.config_path()))
ck("--check 打印脱敏 key", "sk-ab…789" in screen, screen[:400])
# 强制验证是默认:域名不通也必须报出来(而不是"格式对就放行")
rc, screen = cap(setup.configure, ["--check",
                                   "BASE_URL=https://no-such-host.invalid/v1 "
                                   "WCHAT_API_KEY=sk-abc123456789 FLASH_MODEL=m1"], tty=False)
ck("--check 默认验连通(不通 → rc=2)", rc == 2, "rc=%s %r" % (rc, screen[-200:]))
ck("--check 不通时说清是连通问题", "连不上" in screen, screen[-260:])

fresh()
rc, screen = cap(setup.configure, ["--no-verify",
                                   "BASE_URL=https://x.example/v1 WCHAT_API_KEY=sk-abc123456789 "
                                   "FLASH_MODEL=m1"], tty=False)
ck("--no-verify 直接落盘 rc=0", rc == 0, "rc=%s %r" % (rc, screen[-200:]))
ck("--no-verify 确实写了文件", os.path.exists(setup.config_path()))
ck("--no-verify 明说跳过了验证", "跳过连通性验证" in screen, screen[-260:])

fresh()
rc, screen = cap(setup.configure, ["BASE_URL=not-a-url WCHAT_API_KEY=sk-x FLASH_MODEL=y"],
                 tty=False)
ck("一次给完但格式错 → rc=2", rc == 2, repr(rc))
ck("格式错不写盘", not os.path.exists(setup.config_path()))
ck("格式错时提示可以不带参数逐项填", "不带参数跑" in screen, screen[-260:])

# ---------- 10) 无 tty 不带参数:给可复制的一行 ----------
print("=== 10. 无 tty ===")
fresh()
rc, screen = cap(setup.configure, [], lines=[], tty=False)
ck("rc=2", rc == 2, repr(rc))
ck("给出一次性命令", "wchat -config " in screen and "BASE_URL=" in screen, screen[:400])
ck("不写盘", not os.path.exists(setup.config_path()))

# ---------- 11) 连通性失败:值不丢 ----------
print("=== 11. 连通性失败 ===")
fresh()
from wchat_py import llm as _llm  # noqa: E402
_real = _llm._make_client


def _boom(base, key, hdr=""):
    raise Exception("Error code: 401 - {'error': {'message': 'invalid api key'}}")


_llm._make_client = _boom
try:
    rc, screen = cap(setup.configure, ["BASE_URL=https://bad.example/v1 "
                                       "WCHAT_API_KEY=sk-badkey WCHAT_SETUP_X=1 "
                                       "FLASH_MODEL=m-bad"], tty=False)
finally:
    _llm._make_client = _real
ck("401 → rc=2", rc == 2, repr(rc))
ck("说成 key 无效", "key 无效" in screen, screen[-300:])
ck("给出怎么改", "换成你自己的 key" in screen, screen[-300:])
ck("已填的值保住了", setup.read_config().get("WCHAT_API_KEY") == "sk-badkey",
   repr(setup.read_config()))
ck("告诉用户不用重敲", "不用重敲" in screen, screen[-260:])

# ---------- 12) 出错信息里不打印完整 key ----------
print("=== 12. 不漏 key ===")
fresh()
_secret = "sk-VERYSECRET1234567890"
rc, screen = cap(setup.configure,
                 ["BASE_URL=https://x.example/v1 WCHAT_API_KEY=%s FLASH_MODEL=m1" % _secret,
                  "--no-verify"], tty=False)
ck("一次性给完时汇总行打码(不漏 key)", "sk-VE…890" in screen or _secret not in screen,
   screen[-400:])
fresh()
rc, screen = cap(setup.configure,
                 ["BASE_URL=https://x.example/v1 WCHAT_API_KEY=%s FLASH_MODEL=m1" % _secret,
                  "--check", "--no-verify"], tty=False)
ck("--check 输出的是打码形式", _secret not in screen and "sk-VE…890" in screen,
   screen[:400])

# ---------- 13) help / 未知选项 ----------
print("=== 13. 帮助与坏选项 ===")
fresh()
rc, screen = cap(setup.configure, ["-h"], tty=False)
ck("-h rc=0", rc == 0, repr(rc))
ck("-h 讲清两种写法", "逐项填写" in screen and "一次给完" in screen, screen[:400])
rc, screen = cap(setup.configure, ["--bogus"], tty=False)
ck("未知选项 rc=1", rc == 1, repr(rc))
ck("未知选项给用法", "用法:" in screen, screen[:200])

# ---------- 14) WCHAT_NO_SETUP 完全跳过 ----------
print("=== 14. WCHAT_NO_SETUP ===")
fresh()
os.environ["WCHAT_NO_SETUP"] = "1"
try:
    ck("跳过检测(返回 None)", setup.ensure_ready(["-f"]) is None)
finally:
    os.environ.pop("WCHAT_NO_SETUP", None)

# ---------- 15) 与真实 ctmodel 脚本互通(格式兼容不是靠"长得像") ----------
print("=== 15. ctmodel 互通 ===")
d = fresh()
setup.write_group("deepseek", {"BASE_URL": "https://api.deepseek.com/chat/completions",
                              "WCHAT_API_KEY": "sk-ds111",
                              "FLASH_MODEL": "deepseek-flash",
                              "PRO_MODEL": "deepseek-flash"})
setup.write_group("qwen", {"BASE_URL": "https://qwen.example/v1/chat/completions",
                           "WCHAT_API_KEY": "sk-qw222",
                           "FLASH_MODEL": "qwen3.8-flash",
                           "PRO_MODEL": "qwen3.8-flash"})
_ctm = os.path.join(REPO, "configs", "ctmodel.py")
_env = dict(os.environ, CT_CONFIG_FILE=setup.config_path())


def _ctmodel(*args):
    r = subprocess.run([sys.executable, _ctm] + list(args), env=_env,
                       capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


rc, out = _ctmodel("-l")
ck("ctmodel -l 能列出两组", rc == 0 and "deepseek" in out and "qwen" in out, out[:200])
rc, out = _ctmodel("check")
ck("ctmodel check 通过", rc == 0 and "校验通过" in out, out[:200])
rc, out = _ctmodel("deepseek")
ck("ctmodel deepseek 切换成功", rc == 0, out[:200])
ck("切完 deepseek 变成活动组", setup.active_group() == "deepseek", repr(setup.active_group()))
ck("切完读到的 key 是 deepseek 的",
   setup.read_config().get("WCHAT_API_KEY") == "sk-ds111", repr(setup.read_config()))
rc, out = _ctmodel("qwen")
ck("ctmodel qwen 也能切回来", rc == 0 and setup.active_group() == "qwen", out[:200])

# 反过来:ctmodel 切过之后的文件,本模块还能继续改同一组
setup.write_group("qwen", {"WCHAT_API_KEY": "sk-qw333"})
ck("ctmodel 切过之后仍能改同一组",
   setup.read_config().get("WCHAT_API_KEY") == "sk-qw333", repr(setup.read_config()))
ck("另一组没被带坏(deepseek 的 key 没变)",
   setup.read_groups().get("deepseek", {}).get("WCHAT_API_KEY") == "sk-ds111",
   repr(setup.read_groups().get("deepseek")))

print("RESULT total=%d pass=%d fail=%d" % (PASSES[0] + len(FAILS), PASSES[0], len(FAILS)))
sys.exit(0 if not FAILS else 1)