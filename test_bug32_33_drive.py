#!/usr/bin/env python3
# pty 驱动器:把 harness 跑在伪终端里,按计划发送 \x03(等价 Ctrl+C),
# 记录关键标记的到达时刻并断言。用法: drive.py <harness.sh> <scenario>
import os, pty, select, sys, time, signal

def drive(script, scenario, timeout=12.0):
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm"
        os.environ["SCENARIO"] = scenario
        os.execvp("/opt/homebrew/bin/bash", ["bash", script])

    # 按键计划:A 按时序发两次 ^C;A2 发一次;B 在见到 WINDOW_RETURNED 后 0.5s 发 ^C
    if scenario == "A":
        plan = [(1.5, b"\x03"), (2.0, b"\x03")]
    elif scenario == "A2":
        plan = [(1.5, b"\x03")]
    else:  # B
        plan = [("EVENT:WINDOW_RETURNED", 0.5, b"\x03")]

    start = time.time()
    data = b""
    markers = {}    # marker -> 到达时刻(相对 start)
    keys_sent = []  # (时刻, key)
    idx = 0
    pending_ev = None  # (发送时刻, key) 事件型按键
    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            break
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                d = os.read(fd, 65536)
            except OSError:
                break
            if not d:
                break
            data += d
            text = data.decode("utf-8", "replace")
            for m in ("WINDOW_RETURNED", "READ_LOOP_EXITED", "HARNESS_DONE"):
                if m in text and m not in markers:
                    markers[m] = time.time() - start
        if idx < len(plan):
            item = plan[idx]
            if isinstance(item[0], str):  # 事件触发型
                ev, delay, key = item
                if ev.startswith("EVENT:"):
                    ev = ev[6:]  # 去掉前缀,与 markers 的键对齐
                if pending_ev is None and ev in markers:
                    pending_ev = (markers[ev] + delay, key)
                if pending_ev is not None and elapsed >= pending_ev[0]:
                    os.write(fd, pending_ev[1])
                    keys_sent.append((time.time() - start, pending_ev[1]))
                    idx += 1
                    pending_ev = None
            else:  # 时序型
                t, key = item
                if elapsed >= t:
                    os.write(fd, key)
                    keys_sent.append((time.time() - start, key))
                    idx += 1
        if "HARNESS_DONE" in data.decode("utf-8", "replace"):
            break
    # 清理:先杀进程组再杀进程本身(孤儿内层命令为有界循环,会自行退出)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    text = data.decode("utf-8", "replace")
    return {"scenario": scenario,
            "timeout": "HARNESS_DONE" not in markers,
            "markers": markers, "keys_sent": keys_sent, "text": text}

if __name__ == "__main__":
    script, scenario = sys.argv[1], sys.argv[2]
    r = drive(script, scenario)
    print("=== scenario %s ===" % scenario)
    print("timeout:", r["timeout"])
    print("markers:", {k: round(v, 2) for k, v in r["markers"].items()})
    print("keys_sent:", [(round(t, 2), k) for t, k in r["keys_sent"]])
    ok = True
    fails = []
    t = r["text"]
    if scenario == "A":
        if r["timeout"]:
            fails.append("窗口未在超时内返回(第二次 Ctrl+C 未立即收尾)")
        elif len(r["keys_sent"]) < 2:
            fails.append("第二次 Ctrl+C 未发出(第一次按下后窗口就关了?)")
        else:
            t2nd = r["keys_sent"][1][0]
            t_ret = r["markers"].get("WINDOW_RETURNED", 999)
            gap = t_ret - t2nd
            print("2nd ^C -> WINDOW_RETURNED 间隔: %.2fs" % gap)
            if t_ret < t2nd - 0.05:
                fails.append("窗口在第二次按下之前就关闭了(场景未隔离成功)")
            if gap > 1.2:
                fails.append("第二次 Ctrl+C 后收尾过慢(>1.2s)")
            if "input_aborted=1" not in t:
                fails.append("input_aborted 未置位(本轮不会被放弃)")
            if "monitor=OFF" not in t:
                fails.append("monitor 未恢复(泄漏仍在)")
    elif scenario == "A2":
        if r["timeout"]:
            fails.append("窗口未在超时内返回")
        else:
            if "input_aborted=0" not in t:
                fails.append("单击 Ctrl+C 不应放弃整轮")
            if "monitor=OFF" not in t:
                fails.append("monitor 未恢复(泄漏仍在)")
    else:  # B:验证窗口后 job control 恢复初始态(bg 子进程重获 SIG_IGN 屏蔽)
        if r["timeout"]:
            fails.append("harness 超时")
        else:
            if "monitor=OFF" not in t:
                fails.append("monitor 未恢复(set -m 泄漏仍在)")
            if "BG_FIRED" not in t:
                fails.append("bg 子进程被 ^C 杀掉(SIG_IGN 屏蔽未恢复,仍处于泄漏态)")
            if "POST_INTERRUPT" not in t:
                fails.append("^C 后 harness 未继续(信号语义异常)")
    if fails:
        for f in fails:
            print("FAIL:", f)
        print("--- harness 输出末尾 ---")
        print(t[-900:])
        ok = False
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
