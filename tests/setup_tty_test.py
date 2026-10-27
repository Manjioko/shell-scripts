#!/usr/bin/env python3
"""在真 pty 里验"key 输入真的能收进去"。末行 RESULT ...。

锁的是那个真实故障:getpass 走 /dev/tty,当 stdin 与 /dev/tty 不是同一个终端时,
敲字没有反应、只有回车能过 —— 用户看到的就是"输入框被锁住"。
所以这里必须:
  1. 逐字符慢速输入后,key **真的**进了配置文件(不是空串);
  2. 输入期间 ECHO 是关的(不回显);
  3. 读完之后 ECHO **恢复**(否则后续输入/回车行为全乱);
  4. 屏幕上有"已收到 N 个字符"的反馈(证明它活着,不用靠盲猜);
  5. 整条链走完 rc=0 并能落盘 600。
"""
import os
import pty
import select
import stat
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


def run_pty(home, vals, extra_env=None):
    """真 pty 跑 `wchat -config`,逐字符喂值。返回 (屏幕文本, 退出码)。"""
    env = dict(os.environ, HOME=home, CT_HOME=os.path.join(home, ".ct"),
               WCHAT_NO_BOOTSTRAP="1", TERM="xterm-256color", COLUMNS="100",
               PYTHONPATH=REPO + ":" + os.environ.get("PYTHONPATH", ""))
    for k in ("BASE_URL", "API_KEY", "WCHAT_API_KEY", "FLASH_MODEL", "PRO_MODEL",
              "WCHAT_SETUP_NO_VERIFY", "WCHAT_SETUP_PLAIN_INPUT"):
        env.pop(k, None)
    if extra_env:
        env.update(extra_env)
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(sys.executable, [sys.executable, os.path.join(REPO, "wchat"), "-config"], env)
    out = b""

    def drain(t=0.5):
        nonlocal out
        end = time.time() + t
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if not r:
                continue
            try:
                c = os.read(fd, 65536)
            except OSError:
                return
            if not c:
                return
            out += c

    drain(1.5)
    for v in vals:
        for ch in v:                     # 逐字符:模拟真打字,而不是一次灌一整行
            os.write(fd, ch.encode())
            drain(0.02)
        os.write(fd, b"\n")
        drain(0.8)
    drain(2.0)
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        _, status = os.waitpid(pid, 0)
        rc = os.waitstatus_to_exitcode(status)
    except Exception:
        rc = None
    return out.decode("utf-8", "replace"), rc


def run_burst(home, payload):
    """一次性把整段粘贴内容灌进 pty(模拟 Cmd+V 三行)。返回 (屏幕文本, 退出码)。"""
    env = dict(os.environ, HOME=home, CT_HOME=os.path.join(home, ".ct"),
               WCHAT_NO_BOOTSTRAP="1", TERM="xterm-256color", COLUMNS="100",
               PYTHONPATH=REPO + ":" + os.environ.get("PYTHONPATH", ""),
               WCHAT_SETUP_NO_VERIFY="1")
    for k in ("BASE_URL", "API_KEY", "WCHAT_API_KEY", "FLASH_MODEL", "PRO_MODEL",
              "WCHAT_SETUP_PLAIN_INPUT"):
        env.pop(k, None)
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(sys.executable, [sys.executable, os.path.join(REPO, "wchat"), "-config"], env)
    out = b""
    time.sleep(1.5)
    os.write(fd, payload.encode())
    end = time.time() + 8
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if not r:
            continue
        try:
            c = os.read(fd, 65536)
        except OSError:
            break
        if not c:
            break
        out += c
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        _, status = os.waitpid(pid, 0)
        rc = os.waitstatus_to_exitcode(status)
    except Exception:
        rc = None
    return out.decode("utf-8", "replace"), rc


def main():
    home = tempfile.mkdtemp(prefix="tty_cfg_")
    screen, rc = run_pty(home, ["https://tty.example/v1", "sk-typed-by-hand-123", "m-tty"],
                         extra_env={"WCHAT_SETUP_NO_VERIFY": "1"})
    plain = screen.replace("\r", "")
    cfg = setup.read_config() if os.path.exists(os.path.join(home, ".ct", "config")) else {}
    # read_config 读的是当前 CT_HOME,这里手动按 home 读
    p = os.path.join(home, ".ct", "config")
    if os.path.exists(p):
        txt = open(p).read()
    else:
        txt = ""

    ck("key 真的被收进去了(不是空串)", 'sk-typed-by-hand-123' in txt, txt or "(无文件)")
    ck("逐字符输入没有丢字符", 'sk-typed-by-hand-123' in txt,
       "文件里:%r" % (txt.splitlines()[:3]))
    ck("输入期间不回显(屏幕上没有完整 key)", 'sk-typed-by-hand-123' not in plain,
       "屏幕泄露了 key")
    ck("有'已收到 N 个字符'反馈", "已收到" in plain, plain[-400:])
    ck("计数与实际长度一致", "已收到 %d 个字符" % len("sk-typed-by-hand-123") in plain,
       plain[-400:])
    ck("落盘 600",
       os.path.exists(p) and oct(stat.S_IMODE(os.stat(p).st_mode)) == "0o600",
       oct(stat.S_IMODE(os.stat(p).st_mode)) if os.path.exists(p) else "(无文件)")
    ck("rc=0", rc == 0, repr(rc))
    ck("URL 也被收了(说明前一项读取没把后一项搞坏)", "tty.example" in txt, txt)
    ck("模型名也被收了", "m-tty" in txt, txt)

    # ECHO 必须在读完之后恢复:再跑一次,若 echo 没恢复,第二遍的 BASE_URL 就不会回显
    home2 = tempfile.mkdtemp(prefix="tty_cfg2_")
    screen2, rc2 = run_pty(home2, ["https://echo.example/v1", "sk-x123456", "m2"],
                           extra_env={"WCHAT_SETUP_NO_VERIFY": "1"})
    ck("ECHO 已恢复(非密字段正常回显)", "echo.example" in screen2.replace("\r", ""),
       screen2[-300:])
    ck("第二遍也成功", rc2 == 0, repr(rc2))

    # 用户最常见的动作:**一次粘贴三行**(从文档整段复制)。
    # 旧实现(getpass 走 /dev/tty)在这一步会卡死:三行都进了 tty 缓冲,
    # URL 读走第一行,key 却去另一个设备读 → 屏幕上停在 KEY> 一动不动,
    # 而回车(缓冲里剩的行)看着像"还能往下走"。这条断言锁住它。
    home3 = tempfile.mkdtemp(prefix="tty_cfg3_")
    screen3, rc3 = run_burst(home3, "https://burst.example/v1\nsk-burst-key-987\nm-burst\n")
    p3 = os.path.join(home3, ".ct", "config")
    txt3 = open(p3).read() if os.path.exists(p3) else ""
    ck("整段粘贴三行:key 没丢", "sk-burst-key-987" in txt3, txt3 or "(无文件)")
    ck("整段粘贴三行:模型名没被 key 顶掉", 'FLASH_MODEL="m-burst"' in txt3, txt3)
    ck("整段粘贴三行:rc=0", rc3 == 0, repr(rc3))

    print("RESULT total=%d pass=%d fail=%d" % (PASSES[0] + len(FAILS), PASSES[0], len(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())