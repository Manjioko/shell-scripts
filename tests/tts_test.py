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
ck("model 原样透传(build_body 不再注入默认;必填由调用方保证)", _b2["model"] == "")
ck("voice 原样透传(不再注入默认)", _b2["audio"]["voice"] == "")
ck("空 format → 默认 wav", _b2["audio"]["format"] == T.DEFAULT_FORMAT)
ck("is_voice_clone_model: 普通模型 False", T.is_voice_clone_model("mimo-v2.5-tts") is False)
ck("is_voice_clone_model: 复刻模型 True", T.is_voice_clone_model("mimo-v2.5-tts-voiceclone") is True)
ck("is_voice_clone_model: 大小写无关", T.is_voice_clone_model("X-VoiceClone") is True)

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

print("=== 3b. 无兜底:TTS_BASE_URL 原样使用,代码不归一/不拼路径 ===")
ck("normalize_endpoint 已移除(代码不做路径兜底)", not hasattr(T, "normalize_endpoint"))
ck("默认值是完整端点", T.DEFAULT_BASE_URL == "https://api.xiaomimimo.com/v1/chat/completions", T.DEFAULT_BASE_URL)

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
os.environ["TTS_MODEL"] = "mimo-v2.5-tts"          # 必填(普通模式)
os.environ["TTS_VOICE"] = "冰糖"
os.environ.pop("TTS_VOICE_SAMPLE", None)
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
    _fake_urlopen.last_url = req.full_url   # 记录实际请求的 URL,断言"原样使用"
    return _FakeResp(json.dumps({"choices": [{"message": {"audio": {"data": _enc2}}}]}).encode())


_fake_urlopen.last_url = None
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
ck("e2e:TTS_BASE_URL 原样使用(未拼接)",
   _fake_urlopen.last_url == "https://api.xiaomimimo.com/v1/chat/completions", _fake_urlopen.last_url)

# 无兜底:即便只填到 .../v1,也**原样**请求(不再自动补 /chat/completions)
os.environ["TTS_BASE_URL"] = "https://api.xiaomimimo.com/v1"
_fake_urlopen.last_url = None
_urlreq.urlopen = _fake_urlopen
try:
    toolrun._run_tool("text_to_speech", json.dumps({"text": "x", "out_path": os.path.join(_work, "v1.wav")}))
finally:
    _urlreq.urlopen = _orig_urlopen
ck("e2e:.../v1 也原样请求(代码不做兜底补全)",
   _fake_urlopen.last_url == "https://api.xiaomimimo.com/v1", _fake_urlopen.last_url)

# 接口报错(如 404):返回要**解释给用户、且明确让模型不要重试**
import urllib.error as _urlerr  # noqa: E402


def _fake_404(req, timeout=None):
    raise _urlerr.HTTPError(req.full_url, 404, "Not Found", {}, None)


os.environ["TTS_BASE_URL"] = "https://api.xiaomimimo.com/v1"
_urlreq.urlopen = _fake_404
try:
    _err = toolrun._run_tool("text_to_speech", json.dumps({"text": "x", "out_path": os.path.join(_work, "e.wav")}))
finally:
    _urlreq.urlopen = _orig_urlopen
ck("报错:含 HTTP 404", "404" in (_err or ""), repr(_err)[:160])
ck("报错:含当前 TTS_BASE_URL", "https://api.xiaomimimo.com/v1" in (_err or ""), repr(_err)[:200])
ck("报错:明确让模型不要重试", "不要重试" in (_err or ""), repr(_err)[:200])
ck("报错:让用户决定(补充/放弃)", ("用户决定" in (_err or "")) and ("放弃" in (_err or "")), repr(_err)[:220])

# 无 key → 配置指引(不崩)
os.environ.pop("TTS_API_KEY", None)
os.environ["BASE_URL"] = "https://api.deepseek.com/chat/completions"
_no = toolrun._run_tool("text_to_speech", json.dumps({"text": "x", "out_path": os.path.join(_work, "y.wav")}))
ck("e2e:无 key 返回配置指引(提到 TTS_API_KEY)", "TTS_API_KEY" in (_no or ""), repr(_no)[:160])

# 空 text
_empty = toolrun._run_tool("text_to_speech", json.dumps({"text": "   "}))
ck("e2e:空 text 报错", _empty.startswith("错误:"), repr(_empty)[:80])

print("=== 7. build_voice_datauri(样本 → data-uri) ===")
# 造小 wav/mp3 样本
_swav = os.path.join(_work, "sample.wav")
_smp3 = os.path.join(_work, "sample.mp3")
_sbad = os.path.join(_work, "sample.txt")
_wav_bytes = b"RIFF" + b"\x00" * 200
with open(_swav, "wb") as f:
    f.write(_wav_bytes)
with open(_smp3, "wb") as f:
    f.write(b"ID3" + b"\x00" * 100)
with open(_sbad, "wb") as f:
    f.write(b"hello")

_du = T.build_voice_datauri(_swav)
ck("wav 样本 → data:audio/wav;base64 前缀", _du.startswith("data:audio/wav;base64,"), _du[:40])
ck("data-uri 解码等于文件字节",
   base64.b64decode(_du.split(",", 1)[1]) == _wav_bytes)
_du2 = T.build_voice_datauri(_smp3)
ck("mp3 样本 → data:audio/mpeg", _du2.startswith("data:audio/mpeg;base64,"), _du2[:40])
ck("缓存:同文件两次返回同一对象", T.build_voice_datauri(_swav) is _du)


def _raises_val(fn, *a):
    try:
        fn(*a)
        return False
    except ValueError:
        return True
    except Exception:
        return False


ck("不存在的样本 → ValueError", _raises_val(T.build_voice_datauri, os.path.join(_work, "nope.wav")))
ck("不支持的扩展名(.txt)→ ValueError", _raises_val(T.build_voice_datauri, _sbad))
# 超限:用一个**未被缓存过**的新文件,否则缓存命中会短路
_sbig = os.path.join(_work, "sample2.wav")
with open(_sbig, "wb") as f:
    f.write(b"RIFF" + b"\x00" * 200)
_orig_max = T._MAX_SAMPLE_B64
T._MAX_SAMPLE_B64 = 10
try:
    ck("超限 → ValueError", _raises_val(T.build_voice_datauri, _sbig))
finally:
    T._MAX_SAMPLE_B64 = _orig_max

print("=== 8. 模式由 TTS_MODEL 决定(必填;含 clone=复刻,否则普通) ===")
_cap = {}


def _fake_capture(req, timeout=None):
    try:
        _cap["body"] = json.loads(req.data.decode("utf-8"))
    except Exception:
        _cap["body"] = {}
    return _FakeResp(json.dumps({"choices": [{"message": {"audio": {"data": _enc2}}}]}).encode())


def _run_cap(args):
    _cap.clear()
    _urlreq.urlopen = _fake_capture
    try:
        return toolrun._run_tool("text_to_speech", json.dumps(args))
    finally:
        _urlreq.urlopen = _orig_urlopen


# 8.1 TTS_MODEL 未设 → 不提供 TTS 服务(报错在发请求之前,不会走网络)
os.environ["TTS_API_KEY"] = "k-e2e"
os.environ["TTS_BASE_URL"] = "https://api.xiaomimimo.com/v1/chat/completions"
os.environ.pop("TTS_VOICE_SAMPLE", None)
os.environ.pop("TTS_MODEL", None)
_nt = toolrun._run_tool("text_to_speech", json.dumps({"text": "x", "out_path": os.path.join(_work, "nt.wav")}))
ck("8.1 未配置 TTS_MODEL → 报错提到 TTS_MODEL", "TTS_MODEL" in (_nt or ""), repr(_nt)[:160])
ck("8.1 未配置 TTS_MODEL → 提示不重试", "不要重试" in (_nt or ""), repr(_nt)[:160])

# 8.2 复刻模型 + 无样本 → 报错提示补 TTS_VOICE_SAMPLE,不重试
os.environ["TTS_MODEL"] = T.CLONE_MODEL
os.environ.pop("TTS_VOICE_SAMPLE", None)
_cn = toolrun._run_tool("text_to_speech", json.dumps({"text": "x", "out_path": os.path.join(_work, "cn.wav")}))
ck("8.2 复刻模型无样本 → 报错提到 TTS_VOICE_SAMPLE", "TTS_VOICE_SAMPLE" in (_cn or ""), repr(_cn)[:160])
ck("8.2 复刻模型无样本 → 提示不重试", "不要重试" in (_cn or ""), repr(_cn)[:160])

# 8.3 复刻模型 + 样本存在 → voice=data-uri,忽略 voice 参数
os.environ["TTS_VOICE"] = "冰糖"
os.environ["TTS_VOICE_SAMPLE"] = _swav
_co = _run_cap({"text": "hi", "voice": "茉莉", "out_path": os.path.join(_work, "clone.wav")})
_bb = _cap.get("body", {})
ck("8.3 复刻:模型=TTS_MODEL(-voiceclone)", _bb.get("model") == T.CLONE_MODEL, _bb.get("model"))
ck("8.3 复刻:voice=样本 data-uri",
   str((_bb.get("audio") or {}).get("voice", "")).startswith("data:audio/wav;base64,"),
   str((_bb.get("audio") or {}).get("voice", ""))[:40])
ck("8.3 复刻:工具 voice 参数被忽略", (_bb.get("audio") or {}).get("voice") != "茉莉")
ck("8.3 复刻:可正常生成(返回含路径)", ".wav" in (_co or ""), repr(_co)[:120])
ck("8.3 复刻:返回**不含样本 data-uri**(不泄露声纹/不爆上下文)",
   "data:audio" not in (_co or "") and "base64" not in (_co or ""), repr(_co)[:160])
ck("8.3 复刻:返回显示『复刻音色』", "复刻音色" in (_co or ""), repr(_co)[:160])

# 8.4 复刻模型 + 样本读取失败(不存在)→ 报错,不重试
os.environ["TTS_VOICE_SAMPLE"] = os.path.join(_work, "missing.wav")
_ce = toolrun._run_tool("text_to_speech", json.dumps({"text": "x", "out_path": os.path.join(_work, "ce.wav")}))
ck("8.4 复刻样本读取失败 → 报错提到 TTS_VOICE_SAMPLE", "TTS_VOICE_SAMPLE" in (_ce or ""), repr(_ce)[:160])
ck("8.4 复刻样本读取失败 → 不重试", "不要重试" in (_ce or ""), repr(_ce)[:160])

# 8.5 普通模型 + 即使设了样本 → 忽略样本,用预置音色
os.environ["TTS_MODEL"] = "mimo-v2.5-tts"
os.environ["TTS_VOICE_SAMPLE"] = _swav
_nm = _run_cap({"text": "hi", "voice": "茉莉", "out_path": os.path.join(_work, "norm.wav")})
_bn = _cap.get("body", {})
ck("8.5 普通:模型=TTS_MODEL", _bn.get("model") == "mimo-v2.5-tts", _bn.get("model"))
ck("8.5 普通:voice=预置音色(茉莉)", (_bn.get("audio") or {}).get("voice") == "茉莉",
   (_bn.get("audio") or {}).get("voice"))
ck("8.5 普通:即使设了 TTS_VOICE_SAMPLE 也忽略(不传 data-uri)",
   not str((_bn.get("audio") or {}).get("voice", "")).startswith("data:"),
   str((_bn.get("audio") or {}).get("voice"))[:30])

# 8.6 清理
os.environ.pop("TTS_VOICE_SAMPLE", None)

print("RESULT total=%d pass=%d fail=%d" % (P + F, P, F))
sys.exit(0 if F == 0 else 1)