#!/usr/bin/env python3
"""首次运行引导的行为自检。末行 RESULT ...。

锁的是"用户能不能真的配好",不是"打印了什么字符串":
  1. 粘贴解析:空格分隔/多行/export/引号/反引号/JSON 片段/裸 URL/裸 key 都要认;
  2. URL 补齐:只写到 /v1 必须能补成 /chat/completions(否则一进去就 404);
  3. 落盘:~/.ct/config 权限 600、保留文件里其它键、PRO_MODEL 跟随 FLASH_MODEL;
  4. 交互粘贴一次即通:文件写出来 + 环境变量生效 + 验证通过(打桩网关真跑一次 SSE);
  5. 验证失败要给人话(401 → "key 无效"),且**不丢用户已填的内容**;
  6. 非交互不阻塞、不静默写盘,给可直接执行的整段并退出 2;
  7. 已配置好时不再打扰(回归:不能每次启动都弹)。
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

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


def fresh_home():
    d = tempfile.mkdtemp(prefix="setup_home_")
    for k in ("BASE_URL", "API_KEY", "WCHAT_API_KEY", "FLASH_MODEL", "PRO_MODEL",
              "WCHAT_SETUP", "WCHAT_NO_SETUP", "WCHAT_SETUP_NO_VERIFY"):
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


def run_wizard(lines):
    """跑一次交互引导,返回 (rc, 屏幕文本)。"""
    old_in, old_out = sys.stdin, sys.stdout
    out = FakeOut()
    sys.stdin, sys.stdout = FakeIn(lines), out
    try:
        rc = setup.run(interactive=True)
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    return rc, out.getvalue()


# ---------- 1) 粘贴解析 ----------
print("=== 1. 粘贴解析 ===")
d = setup.parse_pasted("BASE_URL=https://a.example/v1/chat/completions WCHAT_API_KEY=sk-aaa111 "
                       "FLASH_MODEL=m1")
ck("空格分隔一行三键", d.get("BASE_URL") == "https://a.example/v1/chat/completions"
   and d.get("WCHAT_API_KEY") == "sk-aaa111" and d.get("FLASH_MODEL") == "m1", repr(d))
ck("PRO_MODEL 自动跟随 FLASH_MODEL", d.get("PRO_MODEL") == "m1", repr(d.get("PRO_MODEL")))
d = setup.parse_pasted('export BASE_URL="https://b.example/v1/chat/completions"\n'
                       "export WCHAT_API_KEY='sk-bbb222'\nexport FLASH_MODEL=\"m2\"")
ck("export + 引号 + 多行", d.get("WCHAT_API_KEY") == "sk-bbb222" and d.get("FLASH_MODEL") == "m2", repr(d))
d = setup.parse_pasted('{"BASE_URL": "https://c.example/v1/chat/completions", '
                       '"WCHAT_API_KEY": "sk-ccc333", "FLASH_MODEL": "m3"}')
ck("JSON 片段也能认", d.get("WCHAT_API_KEY") == "sk-ccc333", repr(d))
d = setup.parse_pasted("sk-ddd444")
ck("只粘一个裸 key", d.get("WCHAT_API_KEY") == "sk-ddd444", repr(d))
d = setup.parse_pasted("`BASE_URL=https://e.example/v1/chat/completions`")
ck("反引号包裹", d.get("BASE_URL") == "https://e.example/v1/chat/completions", repr(d))
d = setup.parse_pasted("API_KEY=sk-fff555")
ck("API_KEY 别名映射到 WCHAT_API_KEY", d.get("WCHAT_API_KEY") == "sk-fff555", repr(d))

# ---------- 2) URL 补齐 ----------
print("=== 2. URL 补齐 ===")
ck("/v1 结尾补 /chat/completions",
   setup.normalize_base_url("https://x.example/v1") == "https://x.example/v1/chat/completions")
ck("根地址补 /chat/completions",
   setup.normalize_base_url("https://x.example") == "https://x.example/chat/completions")
ck("已是完整端点不动",
   setup.normalize_base_url("https://x.example/v1/chat/completions")
   == "https://x.example/v1/chat/completions")
ck("尾部斜杠不产生双斜杠",
   setup.normalize_base_url("https://x.example/v1/") == "https://x.example/v1/chat/completions")

# ---------- 3) 落盘 ----------
print("=== 3. 落盘 ===")
home = fresh_home()
cfg = setup.config_path()
os.makedirs(os.path.dirname(cfg), exist_ok=True)
open(cfg, "w").write('# 注释保留\nBASE_URL="https://old.example/v1/chat/completions"\nCTX_WINDOW=1234\n')
pairs = {"BASE_URL": "https://new.example/v1/chat/completions",
         "WCHAT_API_KEY": "sk-new999", "FLASH_MODEL": "m9", "PRO_MODEL": "m9"}
setup.write_config(pairs)
txt = open(cfg).read()
ck("权限 600", oct(stat.S_IMODE(os.stat(cfg).st_mode)) == "0o600",
   oct(stat.S_IMODE(os.stat(cfg).st_mode)))
ck("覆盖同名键不产生重复", txt.count("BASE_URL=") == 1, txt)
ck("保留文件里的其它键", "CTX_WINDOW=1234" in txt, txt)
ck("保留注释", "# 注释保留" in txt, txt)
ck("读回来的值正确", setup.read_config().get("WCHAT_API_KEY") == "sk-new999", repr(setup.read_config()))

# ---------- 4) 交互粘贴一次即通(打桩网关,真跑 SSE) ----------
print("=== 4. 交互一次粘贴 ===")
home = fresh_home()
sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
tmpd = tempfile.mkdtemp(prefix="setup_stub_")
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
line = ("BASE_URL=http://127.0.0.1:%d/v1/chat/completions WCHAT_API_KEY=sk-stub123 "
        "FLASH_MODEL=m-stub" % port)
try:
    rc, screen = run_wizard([line + "\n"])
finally:
    stub.terminate()
ck("粘贴一行即完成(rc=0)", rc == 0, "rc=%s screen=%r" % (rc, screen[-300:]))
ck("屏幕说配置成功", "配置成功" in screen, screen[-200:])
ck("文件真的写了 key", setup.read_config().get("WCHAT_API_KEY") == "sk-stub123",
   repr(setup.read_config()))
ck("本次进程环境也生效", os.environ.get("WCHAT_API_KEY") == "sk-stub123",
   repr(os.environ.get("WCHAT_API_KEY")))
ck("验证确实发了请求",
   sum(1 for _ in open(os.path.join(tmpd, "log.jsonl"))) >= 1)

# ---------- 5) 验证失败:人话 + 不丢内容 ----------
print("=== 5. 验证失败 ===")
# 这里直接让客户端构造抛 401:测的是"401 → 人话 → 保住已填内容"这条链,
# 不必真起一个 HTTP 服务(起了反而在测自己手写的假响应格式)。
from wchat_py import llm as _llm  # noqa: E402
_real_make = _llm._make_client


def _boom(base, key, hdr=""):
    raise Exception("Error code: 401 - {'error': {'message': 'invalid api key'}}")


_llm._make_client = _boom
home = fresh_home()
try:
    rc, screen = run_wizard([
        "BASE_URL=https://bad.example/v1/chat/completions WCHAT_API_KEY=sk-bad FLASH_MODEL=m-bad\n"])
finally:
    _llm._make_client = _real_make
ck("401 时返回 3(不静默进会话)", rc == 3, "rc=%s" % rc)
ck("401 说成 key 无效", "key 无效" in screen, screen[-260:])
ck("401 给出怎么改", "换成你自己的 key" in screen, screen[-260:])
ck("失败也保住已填内容(不让人重敲)", setup.read_config().get("WCHAT_API_KEY") == "sk-bad",
   repr(setup.read_config()))
ck("失败提示里给了重来方式", "wchat --setup" in screen, screen[-200:])

# ---------- 6) 非交互 ----------
print("=== 6. 非交互 ===")
home = fresh_home()
old_in, old_out = sys.stdin, sys.stdout
sys.stdin = FakeIn([], tty=False)
buf = io.StringIO(); sys.stdout = buf
try:
    rc = setup.run()
finally:
    sys.stdin, sys.stdout = old_in, old_out
out = buf.getvalue()
ck("非交互返回 2", rc == 2, "rc=%s" % rc)
ck("给了可直接执行的整段", "cat > ~/.ct/config" in out and "chmod 600" in out, out[:200])
ck("非交互不写盘", not os.path.exists(setup.config_path()), setup.config_path())

# ---------- 7) 已配置不打扰 ----------
print("=== 7. 已配置不打扰 ===")
home = fresh_home()
setup.apply({"BASE_URL": "https://ok.example/v1/chat/completions",
             "WCHAT_API_KEY": "sk-ok", "FLASH_MODEL": "m"})
old_out = sys.stdout
buf = io.StringIO(); sys.stdout = buf
try:
    r = setup.ensure_ready(["-f"])
    r2 = setup.ensure_ready(["--setup"])     # 显式 setup 也不该因为"已配置"而崩
finally:
    sys.stdout = old_out
ck("已配置时直接放行(不返回退出码)", r is None, repr(r))
ck("已配置时不打印引导面板", "还差一步" not in buf.getvalue(), buf.getvalue()[:120])
ck("--setup 可重复执行", r2 is None, repr(r2))

# ---------- 8) 环境变量算数,不重复追问 ----------
print("=== 8. 环境变量参与判断 ===")
home = fresh_home()
os.environ["BASE_URL"] = "https://env.example/v1/chat/completions"
os.environ["WCHAT_SETUP_NO_VERIFY"] = "1"
try:
    rc, screen = run_wizard(["WCHAT_API_KEY=sk-envonly FLASH_MODEL=m-env\n"])
finally:
    os.environ.pop("WCHAT_SETUP_NO_VERIFY", None)
ck("BASE_URL 来自环境时,只粘 key 也能完成", rc == 0, "rc=%s %r" % (rc, screen[-200:]))
ck("没有重复追问 BASE_URL", "还没认出来" not in screen, screen[-200:])
_txt = open(setup.config_path()).read()
ck("只把粘贴带来的键写进文件(BASE_URL 不固化)", "env.example" not in _txt, _txt)
ck("PRO_MODEL 仍跟着 FLASH_MODEL 落盘", 'PRO_MODEL="m-env"' in _txt, _txt)

# ---------- 9) 旧文件权限不对时被修正 ----------
print("=== 9. 权限修正 ===")
home = fresh_home()
os.makedirs(os.path.dirname(setup.config_path()), exist_ok=True)
open(setup.config_path(), "w").write('BASE_URL="https://x/v1/chat/completions"\n')
os.chmod(setup.config_path(), 0o644)
setup.write_config({"WCHAT_API_KEY": "sk-perm"})
ck("原来的 644 被改成 600",
   oct(stat.S_IMODE(os.stat(setup.config_path()).st_mode)) == "0o600",
   oct(stat.S_IMODE(os.stat(setup.config_path()).st_mode)))
ck("原有键仍在", "BASE_URL" in open(setup.config_path()).read(),
   open(setup.config_path()).read())

print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)