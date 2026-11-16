#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TTS 工具闸门(全离线,不联网)。

守 docs/tts-tool.md 的契约:
  1. build_body:**角色语义反转**(user=风格指令、assistant=目标文本);audio.format/voice 正确。
  2. parse_audio:base64 往返;缺 audio.data / 空 / 坏 base64 → 抛 ValueError(不崩)。
  3. resolve_key:显式 TTS_API_KEY 优先;端点同 host 复用主对话 key;否则空串;**无写死 key**。
  4. default_out_path:在 WORK_ROOT 下、tts_out/、含时间戳、.wav。
  5. fmt_result:含路径/字节/音色/模型。
  6. e2e(桩 urlopen 不经网络):经 toolrun._run_tool 真跑,断言写出文件且字节==解码结果、
     返回含路径;无 key 时返回配置指引(不崩)。
"""
import base64
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ["WCHAT_NO_SETUP"] = "1"

P = F = 0


def ck(name, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  ok   %s" % name)
    else:
        F += 1
        print("  FAIL %s%s" % (name, ("  ← " + str(detail)) if detail else ""))


import wchat_py.tts as T  # noqa: E402

print("=== 1. build_body:角色反转 + audio 字段 ===")
_b = T.build_body("mimo-v2.5-tts", "你好世界", "冰糖", "轻快一点", "wav")
ck("有 style:两条消息(user, assistant)", [m["role"] for m in _b["messages"]] == ["user", "assistant"],
   repr(_b["messages"]))
ck("user=风格指令", _b["messages"][0]["content"] == "轻快一点")
ck("assistant=目标文本(反转!)", _b["messages"][1]["content"] == "你好世界")
ck("audio.format=wav", _b["audio"]["format"] == "wav")
ck("audio.voice=冰糖", _b["audio"]["voice"] == "冰糖")
ck("stream=False(非流式)", _b["stream"] is False)
_b2 = T.build_body("", "只念这句", "", "", "")
ck("无 style:只有一条 assistant 消息", [m["role"] for m in _b2["messages"]] == ["assistant"],
   repr(_b2["messages"]))
ck("空 model → 默认 mimo-v2.5-tts", _b2["model"] == T.DEFAULT_MODEL)
ck("空 voice → 默认 mimo_default", _b2["audio"]["voice"] == T.DEFAULT_VOICE)
ck("空 format → 默认 wav", _b2["audio"]["format"] == T.DEFAULT_FORMAT)

print("=== 2. parse_audio:base64 往返 / 异常 ===")
_raw = b"\x00\x01\x02WAVDATA\xff"
_enc = base64.b64encode(_raw).decode()
_resp = {"choices": [{"message": {"audio": {"data": _enc}}}]}
ck("正常:解码等于原字节", T.parse_audio(_resp) == _raw, repr(T.parse_audio(_resp))[:40])


def _raises(fn, *a):
    try:
        fn(*a)
        return False
    except ValueError:
        return True
    except Exception:
        return False


ck("缺 audio.data → ValueError", _raises(T.parse_audio, {"choices": [{"message": {}}]}))
ck("空 audio.data → ValueError", _raises(T.parse_audio, {"choices": [{"message": {"audio": {"data": ""}}}]}))
ck("坏 base64 → ValueError", _raises(T.parse_audio, {"choices": [{"message": {"audio": {"data": "!!!notb64!!!"}}}]}))

print("=== 3b. normalize_endpoint:完整端点原样(provider 各自路径),只给 /v1 兜底 ===")
ck("完整端点 .../chat/completions → 原样",
   T.normalize_endpoint("https://api.xiaomimimo.com/v1/chat/completions")
   == "https://api.xiaomimimo.com/v1/chat/completions")
ck("别的供应商完整路径 .../v1/audio/speech → **原样不拼**(换供应商不错)",
   T.normalize_endpoint("https://api.openai.com/v1/audio/speech")
   == "https://api.openai.com/v1/audio/speech", T.normalize_endpoint("https://api.openai.com/v1/audio/speech"))
ck("任意完整路径 .../tts/synthesize → 原样",
   T.normalize_endpoint("https://other.example/tts/synthesize")
   == "https://other.example/tts/synthesize")
ck("仅到 .../v1(官方 base_url 写法)→ 兜底补 /chat/completions",
   T.normalize_endpoint("https://api.xiaomimimo.com/v1")
   == "https://api.xiaomimimo.com/v1/chat/completions", T.normalize_endpoint("https://api.xiaomimimo.com/v1"))
ck("尾斜杠 .../v1/ → 兜底补",
   T.normalize_endpoint("https://api.xiaomimimo.com/v1/")
   == "https://api.xiaomimimo.com/v1/chat/completions")
ck("空 → 默认(完整端点)", T.normalize_endpoint("") == T.DEFAULT_BASE_URL == "https://api.xiaomimimo.com/v1/chat/completions")

print("=== 3. resolve_key:显式 > 同 host 复用 > 空 ===")
for k in ("TTS_API_KEY", "WCHAT_API_KEY", "API_KEY", "TTS_BASE_URL", "BASE_URL"):
    os.environ.pop(k, None)
os.environ["TTS_API_KEY"] = "explicit-key"
ck("显式 TTS_API_KEY 优先", T.resolve_key() == "explicit-key")
os.environ.pop("TTS_API_KEY")
os.environ["TTS_BASE_URL"] = "https://api.xiaomimimo.com/v1/chat/completions"
os.environ["BASE_URL"] = "https://api.xiaomimimo.com/v1/chat/completions"
os.environ["WCHAT_API_KEY"] = "mimo-active-key"
ck("同 host → 复用 WCHAT_API_KEY", T.resolve_key() == "mimo-active-key")
os.environ["BASE_URL"] = "https://api.deepseek.com/chat/completions"
ck("异 host(deepseek)→ 不复用,返回空", T.resolve_key() == "")
os.environ.pop("WCHAT_API_KEY")
ck("都没有 → 空串(不写死 key)", T.resolve_key() == "")

print("=== 4. default_out_path ===")
_root = tempfile.mkdtemp(prefix="tts_root_")
_p = T.default_out_path(_root, ts=1700000000)
ck("在 WORK_ROOT 下 tts_out/", os.path.dirname(_p) == os.path.join(_root, "tts_out"), _p)
ck("文件名含时间戳且 .wav", os.path.basename(_p) == "tts-1700000000.wav", _p)

print("=== 5. fmt_result ===")
_fr = T.fmt_result("/x/tts-1.wav", 48000, "冰糖", "mimo-v2.5-tts")
ck("含路径", "/x/tts-1.wav" in _fr, _fr)
ck("含字节数", "48000" in _fr, _fr)
ck("含音色/模型", "冰糖" in _fr and "mimo-v2.5-tts" in _fr, _fr)
ck("48000 字节 ≈ 1.0 秒", "1.0 秒" in _fr, _fr)

print("=== 6. e2e:经 toolrun._run_tool 真跑(桩 urlopen,不联网) ===")
from wchat_py import toolrun  # noqa: E402
import urllib.request as _urlreq  # noqa: E402

_work = tempfile.mkdtemp(prefix="tts_work_")
os.environ["WORK_ROOT"] = _work
os.environ["TTS_API_KEY"] = "k-for-e2e"
os.environ["TTS_BASE_URL"] = "https://api.xiaomimimo.com/v1/chat/completions"
os.environ["TTS_VOICE"] = "冰糖"
_audio = b"RIFF-fake-wav-bytes-1234567890"
_enc2 = base64.b64encode(_audio).decode()
_orig_urlopen = _urlreq.urlopen


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(req, timeout=None):
    return _FakeResp(json.dumps({"choices": [{"message": {"audio": {"data": _enc2}}}]}).encode())


_urlreq.urlopen = _fake_urlopen
try:
    _out = toolrun._run_tool("text_to_speech", json.dumps(
        {"text": "你好", "out_path": os.path.join(_work, "out.wav")}))
finally:
    _urlreq.urlopen = _orig_urlopen
_opath = os.path.join(_work, "out.wav")
ck("e2e:返回含输出路径", _out and _opath in _out, repr(_out)[:160])
ck("e2e:文件已写出", os.path.isfile(_opath), _opath)
ck("e2e:文件字节==解码结果", (open(_opath, "rb").read() if os.path.isfile(_opath) else b"") == _audio,
   "len=%d" % (os.path.getsize(_opath) if os.path.isfile(_opath) else -1))
ck("e2e:返回文案含音色", "冰糖" in (_out or ""), repr(_out)[:160])

# 无 key → 配置指引(不崩)
os.environ.pop("TTS_API_KEY", None)
os.environ["BASE_URL"] = "https://api.deepseek.com/chat/completions"
_no = toolrun._run_tool("text_to_speech", json.dumps({"text": "x", "out_path": os.path.join(_work, "y.wav")}))
ck("e2e:无 key 返回配置指引(提到 TTS_API_KEY)", "TTS_API_KEY" in (_no or ""), repr(_no)[:160])

# 空 text
_empty = toolrun._run_tool("text_to_speech", json.dumps({"text": "   "}))
ck("e2e:空 text 报错", _empty.startswith("错误:"), repr(_empty)[:80])

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)