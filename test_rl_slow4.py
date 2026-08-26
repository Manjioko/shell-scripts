#!/usr/bin/env python3
"""定位 0.95s 延迟:不同 bash/设置"""
import os, pty, select, time

def run(label, bash, prelude):
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(bash, [bash, "-c", prelude + r'''
t0=$EPOCHREALTIME
IFS= read -e -r -p "> " line
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
    m = ""
    for ln in out.decode(errors="replace").splitlines():
        if "ELAPSED" in ln:
            m = ln
    print(f"{label}: {m}")
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass

run("brew bash 5.3 默认", "/opt/homebrew/bin/bash", "")
run("brew bash 5.3 keyseq-timeout 0", "/opt/homebrew/bin/bash", "bind 'set keyseq-timeout 0'\n")
run("brew bash 5.3 bell-style none", "/opt/homebrew/bin/bash", "bind 'set bell-style none'\n")
run("系统 bash 3.2", "/bin/bash", "")
