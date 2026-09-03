# 冒烟测试:BASE_URL 不可达时 ask_ai 应自动重试(可重试错误)后报错收尾
import os, pty, time, re, sys, tempfile

src = open("./ct.dev.sh").read()
src = src.replace('BASE_URL="https://api.xiaomimimo.com/v1/chat/completions"',
                  'BASE_URL="http://127.0.0.1:9/v1/chat/completions"')
tmp = tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, dir=os.environ.get("TMPDIR", "/tmp"))
tmp.write(src); tmp.close()

pid, fd = pty.fork()
if pid == 0:
    os.execvp("bash", ["bash", tmp.name, "-f", "-st", "你好"])

os.set_blocking(fd, False)
buf = b""
end = time.time() + 60
while time.time() < end:
    try:
        d = os.read(fd, 65536)
        if d:
            buf += d
            continue
        break  # EOF
    except BlockingIOError:
        # 进程退出检测
        r = os.waitpid(pid, os.WNOHANG)
        if r[0] != 0:
            break
        time.sleep(0.2)
    except OSError:
        break

try:
    os.kill(pid, 15)
except ProcessLookupError:
    pass
os.unlink(tmp.name)
text = re.sub(r"\x1b[\[\?0-9;]*[a-zA-Z]|\x1b[78]|\r", "", buf.decode("utf-8", "replace"))
print("========== 输出 ==========")
print(text)
print("==========================")
ok = ("重试中(2/3)" in text or "重试中(3/3)" in text) and "API 请求失败" in text
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
