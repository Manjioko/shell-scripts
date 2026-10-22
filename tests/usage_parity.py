#!/usr/bin/env python3
"""session.usage_lines 对 bash print_usage 的 bytes parity（受控状态）。末行 RESULT ...。

受控: messages="[]", last_prompt_tokens=0, CTX_WINDOW=0, TOKEN_THRESHOLD=1000000,
CTX_OVERFLOW_GATE=80, 图片/缓存/标定计数=0, model=flash, AUTOCAL=1, 图观测 stub=0, IMG_REF_DIR 空。
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from wchat_py.session import Session, usage_lines  # noqa: E402

FIX = r'''_WCHAT_LIB_ONLY=1 API_KEY=sk-stub
. "$1" </dev/null >/dev/null 2>&1
messages='[]'; last_prompt_tokens=0; CTX_WINDOW=0; TOKEN_THRESHOLD=1000000; CTX_OVERFLOW_GATE=80
IMG_PRUNE_EVENTS=0; last_cached_tokens=0; last_image_tokens=0; model=flash; IMG_TOKENS_AUTOCAL=1
IMG_REF_DIR=/nonexistent_ref_$$
_img_obs_col(){ echo 0; }
img_token_calib_count(){ echo 0; }
print_usage
'''

bash_out = subprocess.run(["/opt/homebrew/bin/bash", "-c", FIX, "_", os.path.join(REPO, "wchat.sh")],
                          capture_output=True).stdout
bash_out = bash_out.replace(b"\x1b[?25h\x1b[?7h", b"")   # wchat.sh 退出 trap 的光标恢复,非 print_usage

s = Session(messages_json="[]", msg_count=0, last_prompt_tokens=0, last_cached_tokens=0,
            last_image_tokens=0, ctx_window=0, token_threshold=1000000, ctx_overflow_gate=80,
            img_prune_events=0, img_obs_unexpected=0, img_obs_tokens=0, img_ref_count=0,
            model="flash", autocal=1, calib_count=0)
py_out = "".join("\033[2m│\033[0m " + ln + "\n" for ln in usage_lines(s)).encode("utf-8")

p = f = 0


def ck(name, ok, detail=""):
    global p, f
    if ok:
        p += 1
        print("  ok   %s" % name)
    else:
        f += 1
        print("  FAIL %s%s" % (name, (": " + detail) if detail else ""))


ck("bytes 一致", bash_out == py_out, "\n bash=%r\n py  =%r" % (bash_out, py_out))
print("RESULT total=%d pass=%d fail=%d" % (p + f, p, f))
sys.exit(0 if f == 0 else 1)