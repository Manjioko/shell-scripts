#!/usr/bin/env python3
"""B 阶段回放：**带桩网关的业务场景录制器**。

业务段（`-st` / 工具链 / auto 审批 / 硬墙）必须走桩网关，否则录到的只是"连不上 API"的报错。
本脚本把 `auto_test.sh::run_case` 那套抽出来复用：起桩 → 等端口 → pty 跑 wchat → 收屏 → 存基线
（**连同 env 一起存**，回放时才能复现同样的端点）。

用法：
    python3 tests/replay_record_stub.py <name> '<reply-json>' -- <wchat 参数...>
    # 例：st_text
    python3 tests/replay_record_stub.py st_text \
        '[{"type":"text","text":"HI"}]' -- -st "hi"

reply-json 的格式与 tests/stub_gateway.py 一致（`[{"type":"text"|"tool",...}]`）。
"""
import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from replay_record import record  # 复用录制核心（pty + 收屏）


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main(argv):
    if len(argv) < 4 or "--" not in argv:
        sys.stderr.write(__doc__)
        return 2
    i = argv.index("--")
    name = argv[0]
    reply = argv[1]
    # --feed "AUTO|n":在 pty 里按顺序喂键(auto 档要签一次字)。放在 reply 之后的选项位。
    feeds = []
    opts = argv[2:i]
    for k, o in enumerate(opts):
        if o == "--feed":
            feeds = [x for x in opts[k + 1].split("|") if x]
    target = [os.path.join(REPO, "wchat")] + argv[i + 1:]   # 前置可执行文件(与 replay_record 的约定一致)
    port = free_port()
    import subprocess
    import tempfile
    import time
    import socket
    d = tempfile.mkdtemp()
    rp = os.path.join(d, "reply.json")
    open(rp, "w").write(reply)
    env = dict(os.environ,
               STUB_LOG=os.path.join(d, "req.jsonl"), STUB_REPLY=rp,
               STUB_AUTH_DENY_IF="", STUB_FINAL_DENY_IF="")
    stub = subprocess.Popen([sys.executable, os.path.join(HERE, "stub_gateway.py"), str(port)],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close()
            break
        except OSError:
            time.sleep(0.1)
    # ── 照抄 auto_test::run_case 的沙箱环境 ──────────────────────────────
    # 少了 HOME/TMPDIR 时 wchat 会在启动阶段就退到"问候语+等输入",**根本不发请求**
    # (实测:录到的 242 字节只是终端初始化+问候语,桩日志为空)。
    home = os.path.join(d, "home"); tmpd = os.path.join(d, "tmp")
    os.makedirs(os.path.join(home, ".ct"), exist_ok=True); os.makedirs(tmpd, exist_ok=True)
    cfg = os.path.join(home, ".ct", "config")
    open(cfg, "w").write("API_KEY=sk-stub\n"); os.chmod(cfg, 0o600)
    env_extra = {"BASE_URL": "http://127.0.0.1:%d/v1/chat/completions" % port,
                 "WCHAT_API_KEY": "sk-stub",
                 "HOME": home, "TMPDIR": tmpd,
                 "MAX_TOOL_ROUNDS": "25", "CMD_TIMEOUT": "20"}
    # ⚠ 沙箱 HOME 会让 user-site 失效 ⇒ openai import 不到 ⇒ 请求变成"缺少依赖"错误。
    # 这与 py_prelude / auto_test 顶部是同一个修复(本项目已第 5 次踩):把**真实** user-site
    # 记进 PYTHONPATH,子进程一路继承。
    import site as _site
    _us = _site.getusersitepackages()
    env_extra["PYTHONPATH"] = _us + ((":" + os.environ["PYTHONPATH"]) if os.environ.get("PYTHONPATH") else "")
    old = {k: os.environ.get(k) for k in env_extra}
    os.environ.update(env_extra)
    try:
        # 在 proj 沙箱里跑（wchat 按 pwd -P 锁 WORK_ROOT）
        proj = os.path.join(d, "proj")
        os.makedirs(proj, exist_ok=True)
        oldcwd = os.getcwd()
        os.chdir(proj)
        try:
            rc = record(name, target, feeds, 30.0, env_extra)
        finally:
            os.chdir(oldcwd)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        stub.terminate()
    # 把 stub 日志里的请求元信息也存下来（判据用：主请求次数/孤儿 tool 等）
    lg = os.path.join(HERE, "replay", name + ".json")
    try:
        d2 = json.load(open(lg, encoding="utf-8"))
        lines = [json.loads(x) for x in open(os.path.join(d, "req.jsonl"), encoding="utf-8") if x.strip()]
        d2["requests"] = [{k: r.get(k) for k in ("kind", "n_messages", "orphan_tools")} for r in lines]
        d2["sandbox"] = True          # 回放时必须重建同样的 HOME/TMPDIR/.ct/config
        json.dump(d2, open(lg, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("  同时记录请求元信息: %d 条" % len(lines))
    except Exception as e:
        print("  (请求元信息未记录: %s)" % e)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

