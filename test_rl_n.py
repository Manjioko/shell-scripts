#!/usr/bin/env python3
"""测 read -n/-N 在 stty raw 下读 \\r 是否原样"""
import os, pty, select, time

def run(mode, label):
    script = f'''
stty raw
read -r -t 0.4 -{mode} 50 chunk
rc=$?
printf "[rc=%s got=%q]\\n" "$rc" "$chunk"
stty sane
'''
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp("/opt/homebrew/bin/bash", ["/opt/homebrew/bin/bash", "-c", script])
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
    time.sleep(0.3)
    drain(0.1)
    os.write(fd, b"a\rb\nc")   # \r 在中间
    time.sleep(0.2)
    out = drain(0.8)    print(f"{label}: {out.decode(errors='replace')!r}")
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass

run("N", "read -N")
run("n", "read -n")
