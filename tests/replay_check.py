#!/usr/bin/env python3
"""B 阶段回放**比对器**：同 argv 重跑一遍，与录制基线逐字节比对。

这是 B 阶段"删掉 bash 之后有没有退化"的唯一硬判据，所以它自己必须能被判读：
  · 差异时打印**首个不同字节的偏移 + 两侧上下文**（而不是只报"不一致"）；
  · `--self-test` 故意篡改基线，验证它**确实会红**（防恒绿）。

用法：
    python3 tests/replay_check.py            # 跑全部 tests/replay/*.json
    python3 tests/replay_check.py help       # 只跑某一段
    python3 tests/replay_check.py --self-test  # 自检：篡改基线必须变红
"""
import base64
import glob
import json
import os
import re
import pty
import select
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
RDIR = os.path.join(HERE, "replay")
DOTS = "." + "c" + "t"


def run(argv, feed, limit, env_extra):
    env = dict(os.environ)
    env.update(env_extra or {})
    env["TERM"] = env.get("TERM", "xterm-256color")
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir("/tmp")
        os.execvpe(argv[0], argv, env)
        os._exit(127)
    buf = bytearray()
    feeds = list(feed or [])
    start = last = time.time()
    while time.time() - start < limit:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            buf.extend(data)
            last = time.time()
        if feeds and time.time() - last > 2.5:
            os.write(fd, (feeds.pop(0) + "\n").encode())
            last = time.time()
        if not feeds and time.time() - last > 4:
            break
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    time.sleep(0.2)
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass
    return bytes(buf)


def diff_at(a, b):
    n = min(len(a), len(b))
    i = next((k for k in range(n) if a[k] != b[k]), None)
    if i is None:
        if len(a) == len(b):
            return None
        i = n
    return i, a[max(0, i - 30):i + 30], b[max(0, i - 30):i + 30]


def _free_port():
    import socket
    sk = socket.socket(); sk.bind(("127.0.0.1", 0)); p = sk.getsockname()[1]; sk.close(); return p


def _sandbox_env(d):
    """重建录制期的环境：桩 + HOME/TMPDIR + 配置目录 + PYTHONPATH。

    三个坑(录制时踩过, 回放同样会踩):
      1. 沙箱 HOME 让 user-site 失效 ⇒ openai import 不到 ⇒ 补 PYTHONPATH=真实 user-site
      2. BASE_URL 端口每次不同 ⇒ 起新桩并把端点指过去
      3. 需要 `-f -st` 才会真发请求(argv 已在基线里)
    """
    if not d.get("sandbox"):
        return (d.get("env") or {}), None
    import subprocess, tempfile, time, socket, sys as _sys, site as _site
    dd = tempfile.mkdtemp()
    home = os.path.join(dd, "home"); tmpd = os.path.join(dd, "tmp")
    cfgdir = os.path.join(home, DOTS)
    os.makedirs(cfgdir, exist_ok=True); os.makedirs(tmpd, exist_ok=True)
    cfg = os.path.join(cfgdir, "config")
    open(cfg, "w").write("API_KEY=sk-stub\n"); os.chmod(cfg, 0o600)
    port = _free_port()
    rp = os.path.join(dd, "reply.json"); open(rp, "w").write(d.get("reply") or "[]")
    senv = dict(os.environ, STUB_LOG=os.path.join(dd, "req.jsonl"), STUB_REPLY=rp,
                STUB_AUTH_DENY_IF="", STUB_FINAL_DENY_IF="")
    stub = subprocess.Popen([_sys.executable, os.path.join(HERE, "stub_gateway.py"), str(port)],
                            env=senv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close(); break
        except OSError:
            time.sleep(0.1)
    us = _site.getusersitepackages()
    env = {"BASE_URL": "http://127.0.0.1:%d/v1/chat/completions" % port,
           "WCHAT_API_KEY": "sk-stub", "HOME": home, "TMPDIR": tmpd,
           "MAX_TOOL_ROUNDS": "25", "CMD_TIMEOUT": "20",
           "PYTHONPATH": us + ((":" + os.environ["PYTHONPATH"]) if os.environ.get("PYTHONPATH") else "")}
    return env, stub



ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Za-z0-9]|\x1b[=>]")


def normalize(b):
    """把屏幕字节归一成**业务文本序列**，供带 TUI 重绘的段比对。

    做什么：剥 ANSI 控制序列 → 按 \r 与 \n 切行 → 去空白 → 丢空行 → **折叠连续重复行**
    （流式重绘会把同一行画很多次）。
    不做什么：不排序、不去重（非连续重复保留）——顺序仍有意义。
    """
    t = ANSI.sub("", b.decode("utf-8", "replace"))
    # 掩掉易变量: auto_session_id(时间戳+PID)、临时路径、端口。
    # 不掩这些, auto 段天然不可重复(实测: 连录两次不一致)。
    t = re.sub(r"\d{8}-\d{6}-\d+", "<SID>", t)
    t = re.sub(r"/var/folders/[^\s]*|/tmp/[^\s]*", "<TMP>", t)
    t = re.sub(r"127\.0\.0\.1:\d+", "127.0.0.1:<PORT>", t)
    out = []
    for seg in re.split(r"[\r\n]+", t):
        seg = seg.strip()
        if not seg:
            continue
        if out and out[-1] == seg:
            continue
        out.append(seg)
    return out


def compare(want, got, mode, anchors=None):
    # anchor 模式:只要求**关键短语按序出现**,允许其他行差异。
    # 用途:交互时序不稳定、但"业务结论"必须出现的段(auto 档/弹窗/中断)。
    # 强度:bytes > norm > anchor —— 用哪个必须在段里写明理由,不许默认降级。
    if mode == "anchor":
        text = "\n".join(normalize(got))
        pos = 0
        for a in (anchors or []):
            k = text.find(a, pos)
            if k < 0:
                return ("anchor", a, "<缺失或顺序不对>", "")
            pos = k + len(a)
        return None
    if mode == "norm":
        nw, ng = normalize(want), normalize(got)
        if nw == ng:
            return None
        i = next((k for k in range(min(len(nw), len(ng))) if nw[k] != ng[k]), None)
        return ("norm", i, (nw[i] if i is not None else "<末尾>"), (ng[i] if i is not None else "<末尾>"))
    if want == got:
        return None
    return ("bytes",) + (diff_at(want, got) or (None, b"", b""))

def check_one(path, exe=None):
    d = json.load(open(path, encoding="utf-8"))
    argv = [exe or os.path.join(REPO, "wchat")] + d["argv"]
    want = base64.b64decode(d["out_b64"])
    env, stub = _sandbox_env(d)
    try:
        got = run(argv, d.get("feed") or [], d.get("limit", 30), env)
    finally:
        if stub is not None:
            stub.terminate()
    mode = d.get("mode", "bytes")
    r = compare(want, got, d.get("mode", "bytes"), d.get("anchors"))
    if r is None:
        print("  ok   %-16s %d 字节一致(%s)" % (d["name"], len(got), mode))
        return True
    print("  FAIL %-16s 期望 %d 字节 / 实得 %d 字节(%s)" % (d["name"], len(want), len(got), mode))
    if r[0] == "anchor":
        print("       缺失的锚点: %r (%s)" % (r[1], r[2]))
    elif r[0] == "norm":
        print("       首个不同的业务行 #%s\n       基线: %r\n       实得: %r" % (r[1], r[2], r[3]))
    else:
        print("       首个差异偏移 %s\n       基线: %r\n       实得: %r" % (r[1], r[2], r[3]))
    return False


def main(argv):
    exe = None
    names = []
    self_test = False
    for a in argv:
        if a == "--self-test":
            self_test = True
        else:
            names.append(a)
    files = ([os.path.join(RDIR, n + ".json") for n in names] if names
             else sorted(glob.glob(os.path.join(RDIR, "*.json"))))
    if not files:
        print("没有回放基线（tests/replay/*.json）")
        return 2
    if self_test:
        # 篡改基线 1 个字节 → 比对器必须报 FAIL。防"恒绿判据"。
        p = files[0]
        d = json.load(open(p, encoding="utf-8"))
        raw = bytearray(base64.b64decode(d["out_b64"]))
        if not raw:
            print("自检失败：基线为空，无法篡改")
            return 2
        raw[len(raw) // 2] ^= 0xFF
        d["out_b64"] = base64.b64encode(bytes(raw)).decode("ascii")
        tmp = os.path.join(RDIR, "_selftest.json")
        d["name"] = "_selftest"
        json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        ok = check_one(tmp)
        os.unlink(tmp)
        print("自检：" + ("✅ 篡改后确实变红（判据有效）" if not ok
                        else "❌ 篡改后仍然绿 —— 判据是假的！"))
        return 0 if not ok else 1
    okall = True
    for p in files:
        if not check_one(p, exe):
            okall = False
    print("回放：%d 段，%s" % (len(files), "全部一致" if okall else "有差异"))
    return 0 if okall else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

