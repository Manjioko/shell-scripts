#!/usr/bin/env python3
"""验证:关 bracketed-paste 后 read -e 延迟是否消失"""
import os, pty, select, time

def run(label, prelude):
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp("/opt/homebrew/bin/bash", ["/opt/homebrew/bin/bash", "-c", prelude + r'''
t0=$EPOCHREALTIME
IFS= read -e -r -p "> " line
rc=$?
t1=$EPOCHREALTIME
echo "ELAPSED=$(awk "BEGIN{print $t1-$t0}")"
'''])
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
    time.sleep(0.5)
    drain(0.2)
    os.write(fd, b"hello\r")
    time.sleep(0.2)
    out = drain(1.5)
    print(f"{label}: {out.decode(errors='replace')!r}")
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass

run("默认", "")
run("bind 关括号粘贴", "bind 'set enable-bracketed-paste off'\n")
run("终端关 2004", "printf '\\033[?2004l'\n")
run("INPUTRC 关", "export INPUTRC=/dev/null\nbind 'set enable-bracketed-paste off'\n")
