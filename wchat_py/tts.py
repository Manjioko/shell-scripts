#!/usr/bin/env python3
"""tts —— 文本转语音的纯逻辑(mimo-v2.5-tts;方案见 docs/tts-tool.md)。

按供应商出 chat 请求体、解析 base64 音频、算默认落盘路径、解析 key。
纯函数、无副作用,便于单测与 CLI 调试(对齐 webs.py / vision.py 的接缝风格)。

⚙ 关键(别改错):mimo TTS 的**角色语义反转** —— 要合成的**目标文本放 assistant**,
`user` 只放风格指令(预置音色时 user 可选)。这跟正常对话 messages 相反。

退出码(见 `_contract`):
  0  成功;1  业务失败(解析不到音频/入参非法);2  环境异常

用法:
  python3 -m wchat_py.tts build_body <model> <voice> [style] [fmt]   # stdin=要合成的文本 -> stdout=请求体 JSON
  python3 -m wchat_py.tts parse_audio                                # stdin=响应 JSON -> stdout=音频字节数
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time

from . import _contract

DEFAULT_MODEL = "mimo-v2.5-tts"
DEFAULT_VOICE = "mimo_default"
DEFAULT_FORMAT = "wav"
# 官方文档的 `base_url`(SDK 根地址,不是完整端点):OpenAI(base_url=...) 本身就是 /v1 根,
# create() 时由 SDK 拼 `/chat/completions`。我们的 `TTS_BASE_URL` 遵循同一语义。
# ⚠ TTS_BASE_URL 的**推荐值是完整端点** `.../v1/chat/completions`(见 normalize_endpoint 注释:
#   路径不能由代码硬拼,否则换供应商会拼错);默认给完整端点,只填到 `/v1` 也能兜底。
DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1/chat/completions"


def build_body(model, text, voice, style="", fmt="wav"):
    """组 mimo TTS 的 chat.completions 请求体。

    **角色反转**:目标文本 -> assistant;style -> user(仅当非空时才加 user 消息,
    因为预置音色时 user 是可选的)。绝不塞入当前对话的 messages。
    """
    msgs = []
    if style and str(style).strip():
        msgs.append({"role": "user", "content": str(style)})
    msgs.append({"role": "assistant", "content": text or ""})
    return {
        "model": model or DEFAULT_MODEL,
        "messages": msgs,
        "audio": {"format": (fmt or DEFAULT_FORMAT), "voice": (voice or DEFAULT_VOICE)},
        "stream": False,
    }


def parse_audio(resp):
    """从响应 dict 取 `choices[0].message.audio.data` 并 base64 解码为 bytes。
    结构缺失/空/解码失败 → 抛 ValueError(带可读信息,由调用方转成给模型的话)。"""
    try:
        data = resp["choices"][0]["message"]["audio"]["data"]
    except (KeyError, IndexError, TypeError):
        raise ValueError("TTS 返回里没有 audio.data(响应格式异常)")
    if not data:
        raise ValueError("TTS 返回的 audio.data 为空")
    try:
        return base64.b64decode(data)
    except Exception as e:                      # noqa: BLE001 —— base64 各种坏法都归一
        raise ValueError("audio.data base64 解码失败: %s" % e)


def default_out_path(work_root, ts=None):
    """默认落盘路径:`<WORK_ROOT>/tts_out/tts-<时间戳>.wav`。
    放在 WORK_ROOT 内 → 过 `_path_guard("rw")`,不触发越界确认,也不污染 ~/.ct。"""
    root = work_root or os.getcwd()
    ts = int(time.time()) if ts is None else ts
    return os.path.join(root, "tts_out", "tts-%d.wav" % ts)


def fmt_result(path, nbytes, voice, model):
    """组装回给模型的文案。时长按 wav 24kHz/16bit/mono 粗估(bytes/(24000*2)),只是提示。"""
    secs = (nbytes / (24000.0 * 2.0)) if nbytes else 0.0
    return ("✅ 已生成语音: %s(%d 字节 · 约 %.1f 秒 · 音色=%s · 模型=%s)"
            % (path, nbytes, secs, voice or DEFAULT_VOICE, model or DEFAULT_MODEL))


def _host(u):
    try:
        from urllib.parse import urlparse
        return urlparse(u or "").netloc.lower()
    except Exception:
        return ""


def normalize_endpoint(url):
    """把 `TTS_BASE_URL` 归一成真正要 POST 的端点。

    **设计结论(2026-11 用户指出并采纳):路径不能由代码硬拼。**
    不同供应商的 TTS 端点路径不同 —— mimo 走 `/v1/chat/completions`,而 OpenAI 的真 TTS 其实是
    `/v1/audio/speech`。在代码里拼 `/chat/completions`(旧实现),换供应商必错:实测把
    `https://api.openai.com/v1/audio/speech` 拼成 `.../v1/audio/speech/v1/chat/completions` → 404。
    所以 `TTS_BASE_URL` **就当作完整 POST 端点,原样使用**;换供应商只改这一行配置,错误可控在 config。

    唯一兜底:值只到 `/v1`(官方文档 `base_url=.../v1` 那种"没写到端点"的写法)时,补一个
    `/chat/completions`,免得照着官方示例填就 404。**任何带真实路径的端点一律原样、绝不拼。**
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        return DEFAULT_BASE_URL
    if u.endswith("/v1"):                 # 官方 base_url 写法(明显没写到端点)→ 兜底补一次
        return u + "/chat/completions"
    return u                              # 其余一律原样(完整端点;不同供应商各自路径)


def resolve_key(base_url=None, tts_base_url=None):
    """解析 TTS API key(空串=没有)。
    1) TTS_API_KEY 显式优先(环境或 ~/.ct/config 载入);
    2) 否则当 TTS 端点与主对话端点**同 host** 时,复用 WCHAT_API_KEY/API_KEY
       (即"主对话切到 mimo 组"时,TTS 自动用同一把 mimo key);
    3) 都没有 → 返回空串(调用方给出配置指引)。
    **绝不返回写死的常量 key。**
    """
    k = (os.environ.get("TTS_API_KEY") or "").strip()
    if k:
        return k
    tb = tts_base_url if tts_base_url is not None else os.environ.get("TTS_BASE_URL", DEFAULT_BASE_URL)
    mb = base_url if base_url is not None else os.environ.get("BASE_URL", "")
    if _host(tb) and _host(tb) == _host(mb):
        return (os.environ.get("API_KEY") or os.environ.get("WCHAT_API_KEY") or "").strip()
    return ""


# --------------------------------------------------------------------------- CLI
def cmd_build_body(argv):
    model = argv[0] if len(argv) > 0 else ""
    voice = argv[1] if len(argv) > 1 else ""
    style = argv[2] if len(argv) > 2 else ""
    fmt = argv[3] if len(argv) > 3 else ""
    text = _contract.read_stdin_text()
    _contract.write_text(json.dumps(build_body(model, text, voice, style, fmt), ensure_ascii=False))
    return _contract.EXIT_OK


def cmd_parse_audio(argv):
    try:
        resp = json.loads(_contract.read_stdin_text())
        b = parse_audio(resp)
    except Exception as e:                      # noqa: BLE001
        sys.stderr.write("tts parse_audio: %s\n" % e)
        return _contract.EXIT_FAIL
    _contract.write_text(str(len(b)))
    return _contract.EXIT_OK


_COMMANDS = {"build_body": cmd_build_body, "parse_audio": cmd_parse_audio}


def main(argv):
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write("tts: 未知子命令 %r(可用: %s)\n"
                         % (argv[0] if argv else "", ", ".join(sorted(_COMMANDS))))
        return _contract.EXIT_FAIL
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))