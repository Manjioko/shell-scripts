# 验证粘贴修复:macOS 增量追加路径,连续两次多行粘贴不叠加不重复
import os, pty, time, re, sys

pid, fd = pty.fork()
if pid == 0:
    os.execvp("bash", ["bash", "./ct.dev.sh", "-f"])

os.set_blocking(fd, False)
buf = b""

def drain(sec):
    global buf
    end = time.time() + sec
    while time.time() < end:
        try:
            d = os.read(fd, 65536)
            if d:
                buf += d
        except (BlockingIOError, OSError):
            time.sleep(0.05)

drain(2)  # 等提示符
# 第一次粘贴(单次 write 模拟粘贴:多行一次性入队)
os.write(fd, b"Here's a paste text1\rHere's a paste text2\r")
drain(2)
# 第二次粘贴
os.write(fd, b"Second paste lineA\rSecond paste lineB\r")
drain(2)

os.kill(pid, 15)
text = buf.decode("utf-8", "replace")
# 剥 ANSI 序列后统计每行出现次数
clean = re.sub(r"\x1b[\[\?0-9;]*[a-zA-Z]|\x1b[78]|\r", "", text)
print("========== 屏幕输出(剥离转义后) ==========")
print(clean)
print("==========================================")
ok = True
for s in ["Here's a paste text1", "Here's a paste text2",
          "Second paste lineA", "Second paste lineB"]:
    n = clean.count(s)
    print(f"{s!r}: 出现 {n} 次")
    if n != 1:
        ok = False
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
