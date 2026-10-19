#!/usr/bin/env python3
"""B 阶段回放基线的**录制器**。

用途：在删掉 `wchat.sh` 之前，用**现在的 bash 版**录下真实会话的屏幕字节，
作为 Python 版"逐事件复现"的参照。这是 B 阶段唯一能证明"没退化"的东西。

格式（`tests/replay/<name>.json`）：
    {
      "name": "help",
      "argv": ["-h"],
      "feed": ["AUTO", "n"],          # 可空：静默 -> 喂给 pty 的按键序列
      "limit": 30,                    # 秒
      "env": {"WCHAT_API_KEY": "sk-stub", "BASE_URL": "..."},   # 可空
      "out_b64": "..."                # **屏幕字节原文**（base64，避免任何编码歧义）
    }

为什么 base64：屏幕输出含 ANSI 转义、中文、可能的半个 UTF-8 序列；任何"解码成文本再存"
的做法都可能在回放比对时引入假差异。

用法：
    python3 tests/replay_record.py <name> [--limit 30] [--feed "AUTO|n"] -- <wchat 参数...>

注意：需要 stub 网关时，由调用方先把 BASE_URL 指过去（本脚本不管 stub）。
"""
import base64
import json
import os
import pty
import select
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "replay")


def record(name, argv, feed, limit, env_extra):
    env = dict(os.environ)
    env.update(env_extra or {})
    env["TERM"] = env.get("TERM", "xterm-256color")
    pid, fd = pty.fork()
    if pid == 0:                       # child
        os.chdir("/tmp")
        os.execvpe(argv[0], argv, env)
        os._exit(127)
    buf = bytearray()
    feeds = list(feed or [])
    start = time.time()
    last = time.time()
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
    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, name + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"name": name, "argv": argv[1:], "feed": feed or [],
                   "limit": limit, "env": env_extra or {},
                   "out_b64": base64.b64encode(bytes(buf)).decode("ascii")}, f,
                  ensure_ascii=False, indent=1)
    print("录制 %s: %d 字节 -> %s" % (name, len(buf), path))
    return 0


def main(argv):
    if len(argv) < 2 or "--" not in argv:
        sys.stderr.write(__doc__)
        return 2
    i = argv.index("--")
    name = argv[0]
    opts = argv[1:i]
    target = argv[i + 1:]
    limit, feed = 30.0, []
    for k, o in enumerate(opts):
        if o == "--limit":
            limit = float(opts[k + 1])
        elif o == "--feed":
            feed = opts[k + 1].split("|")
    return record(name, target, feed, limit, {})


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

