#!/usr/bin/env python3
"""完全按压测时序的单轮诊断"""
import os, pty, select, time, random, sys
sys.path.insert(0, "/Users/manjioko/shell-scripts")
import paste_stress as ps

for seed in [0, 1, 2]:
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(ps.BASH, [ps.BASH, ps.REPRO])

    def drain(dur):
        buf = b""
        end = time.time() + dur
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    d = os.read(fd, 65536)
                except OSError:
                    return buf
                if not d:
                    return buf
                buf += d
        return buf

    drain(0.6)
    os.write(fd, b"nihao\\\r")
    time.sleep(0.5)
    drain(0.2)
    ps.send_chunked(fd, ps.make_bytes(ps.PASTE_LINES), seed=seed, **ps.SEND_KW)
    time.sleep(1.0)
    drain(0.2)
    os.write(fd, b"END\r")
    time.sleep(0.6)
    out = drain(1.2)
    text = out.decode(errors="replace")
    print(f"===== seed={seed} 有 INPUT 段: {'=== INPUT ===' in text} =====")
    print("输出尾部:", repr(text[-300:]))
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
