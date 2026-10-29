#!/usr/bin/env python3
"""wchat auto 模式的桩服务端:按脚本回放响应,记录每个请求。

为什么需要它:auto 的三层裁决(硬墙 → 审批器 → 终审器)散在主循环里,抽函数
单测覆盖不到"分支到底有没有被接上"。用桩网关把整条 SSE 链路跑通,才能证明
硬墙命令真的没被执行、放行命令真的被执行了、审计真的落了盘。
"""
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = os.environ["STUB_LOG"]          # 请求记录(每行一个 JSON)
REPLY = os.environ["STUB_REPLY"]      # 主对话回放脚本(一个 JSON 数组)
_ctr = {"main": 0}
_lock = threading.Lock()


def sse_tool_call(cid, name, args):
    """按 ask_ai 的解析形状输出:arguments 分片下发(真实网关就是这么干的)"""
    payload = json.dumps(args, ensure_ascii=False)
    first = {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": cid, "type": "function", "function": {"name": name, "arguments": payload[:40]}}]}}]}
    rest = {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": payload[40:]}}]}}]}
    end = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
    return "".join("data: %s\n\n" % json.dumps(x) for x in (first, rest, end)) + "data: [DONE]\n\n"


def sse_text_pieces(text, pieces=8):
    """把正文切成多片下发(每片一个 SSE 事件),供"流式过程中被 Ctrl+C"的测试用。

    原来的 sse_text 一次性把整段吐完,客户端还没来得及被中断就结束了 —— 那样测不出
    中断行为(测出来的只是"正常收完")。
    """
    if pieces < 1:
        pieces = 1
    n = max(1, (len(text) + pieces - 1) // pieces)
    parts = [text[i:i + n] for i in range(0, len(text), n)] or [""]
    evs = [{"choices": [{"delta": {"content": p}}]} for p in parts]
    evs.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    return evs


def sse_text(text):
    chunk = {"choices": [{"delta": {"content": text}}]}
    end = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
    return "".join("data: %s\n\n" % json.dumps(x, ensure_ascii=False) for x in (chunk, end)) + "data: [DONE]\n\n"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = {}
        # 分类必须看 system 首条的确切开头,不能在整个 messages 里搜关键词:
        # auto 模式会把"审批器/终审器"字样注入主对话的 system(见 inject_auto),
        # 用 in 判断会把每一次主请求都误判成终审请求(实测桩只吐 DENY,主流程
        # 直接报"API 请求失败",看起来像产品坏了其实是测试自己的坑)。
        msgs = body.get("messages", [])
        head = ""
        if msgs and isinstance(msgs[0], dict):
            head = str(msgs[0].get("content", ""))[:24]
        kind = "main"
        if head.startswith("你是 bash 命令安全审查器"):
            kind = "auth"
        elif head.startswith("你是终端 AI 助手在 auto 模式下的终审器"):
            kind = "final"
        with _lock:
            with open(LOG, "a") as f:
                _tk = body.get("thinking")
                _msgs = body.get("messages") or []

                def _orphans(ms):
                    # 孤儿 tool 消息:tool_call_id 没有被**前面任何 assistant 的 tool_calls** 声明过。
                    # 这种消息会让真实服务端 400,且屏幕上没有任何提示 —— 它是"回滚没做干净"的指纹
                    # (失败/被拒的工具调用残留在上下文里)。W12-4d 改状态所有权前先把它钉成 0。
                    declared, n = set(), 0
                    for m in ms:
                        if not isinstance(m, dict):
                            continue
                        if m.get("role") == "assistant":
                            for tc in (m.get("tool_calls") or []):
                                if isinstance(tc, dict):
                                    declared.add(tc.get("id") or "")
                        elif m.get("role") == "tool":
                            if (m.get("tool_call_id") or "") not in declared:
                                n += 1
                    return n

                f.write(json.dumps({"kind": kind, "model": body.get("model"),
                                    "path": self.path,
                                    "thinking_type": (_tk.get("type")
                                                      if isinstance(_tk, dict) else _tk),
                                    "n_messages": len(_msgs),
                                    "orphan_tools": _orphans(_msgs),
                                    "stream": body.get("stream"),
                                    "has_tools": "tools" in body}, ensure_ascii=False) + "\n")

        # 答复按"最后一条 user 内容里有没有指定标记"决定,这样一个场景里可以
        # 混着放"该放行/该拒"的多条命令(整场统一回 Y 或统一回 N 测不出分支)
        last_user = ""
        for m in reversed(msgs if isinstance(msgs, list) else []):
            if isinstance(m, dict) and m.get("role") in ("user", "tool"):
                last_user = str(m.get("content", ""))
                break
        def verdict(deny_if, deny_text, ok_text):
            # 支持逗号分隔多个标记:审批器请求里只有**命令本身**(理由走的是终审),
            # 所以要按命令里的特征字选标记;单标记无法在一个场景里同时造出
            # "N→ALLOW" 与 "N→DENY" 两条路径
            key = os.environ.get(deny_if, "")
            for k in [x for x in key.split(",") if x]:
                if k in last_user:
                    return deny_text
            return os.environ.get("STUB_%s_DEFAULT" % kind.upper(), ok_text)
        if kind == "auth":
            ans = verdict("STUB_AUTH_DENY_IF", "N 疑似越界/破坏性操作", "Y")
            payload = json.dumps({"choices": [{"message": {"content": ans}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if kind == "final":
            ans = verdict("STUB_FINAL_DENY_IF", "DENY 目标不在授权范围", "ALLOW 工作目录内的常规操作")
            payload = json.dumps({"choices": [{"message": {"content": ans}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        with _lock:
            i = _ctr["main"]
            _ctr["main"] += 1
        # ⚠ 用"第几次请求"当"第几轮"的地址是**夹具的脆弱点**,不是产品的语义:
        #   中间任何一次额外请求(标题生成、压缩摘要、重试)都会把序号吃掉,于是"第二轮"
        #   拿到第三段脚本。实测(2026-10,Docker/Ubuntu):`interrupt_e2e` 的
        #   "中断后仍可继续对话"因此拿到的回复不是 SECOND-OK ——
        #   现象像"产品第二轮不渲染",真因是夹具把请求序号当轮次。
        #   `STUB_REPLY_MAP` 按**最后一条 user 内容**选回复(真实服务端就是这么按内容答的),
        #   额外请求不再能偷走某一轮的答复。
        #   **命中关键字才接管**;没命中就照旧按序号 —— 这样只给需要的场景上 map,
        #   其它套件的行为一点不变(不命中就改成 default 会悄悄改掉所有既有套件的回复)。
        _txt = None
        _map = os.environ.get("STUB_REPLY_MAP") or ""
        if _map:
            try:
                _m = json.loads(_map)
            except ValueError:
                _m = {}
            _last_user = ""
            for _msg in reversed(msgs if isinstance(msgs, list) else []):
                if isinstance(_msg, dict) and _msg.get("role") == "user":
                    _last_user = str(_msg.get("content", ""))
                    break
            # ⚠ 必须**整行相等**,不能用 `k in _last_user` 做子串匹配:
            #   实测踩过 —— key "hi" 是 "t**hi**rd" 的子串,于是第三轮 `third` 命中了
            #   `hi` 的回复,断言等 "结束" 永远等不到,看起来像"产品第三轮不渲染"。
            #   子串匹配对英文短词必然误伤(hi/in/a/no…),这是我第二次在这个文件里
            #   被同一类"看起来对"的写法骗到。
            _key = next((k for k in _m if k and k == _last_user.strip()), None)
            if _key is not None:
                _txt = _m[_key]
        if _txt is not None:
            step = {"type": "text", "text": _txt}
            # ⚠ 非流式请求(标题生成/压缩摘要走 _nonstream_msg)必须回**普通 JSON**,
            #   回 SSE 会让 SDK 解析失败 → 上游当成"失败"静默兜底,夹具里表现为
            #   某些轮次拿不到回复。auth/final 那两条早就这么分流了,这里补齐。
            if not body.get("stream"):
                payload = json.dumps({"choices": [{"message": {"content": _txt}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
        else:
            script = json.load(open(REPLY))
            step = script[i] if i < len(script) else {"type": "text", "text": "结束"}
        slow = float(os.environ.get("STUB_SLOW_MS") or 0)
        if step["type"] == "tool":
            out = sse_tool_call(step.get("id", "call_%d" % i), step["name"], step["args"]).encode()
        elif slow > 0:
            out = None            # 慢速分支自己逐片写,不走下面的整块编码
        else:
            out = sse_text(step.get("text", "结束")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        if out is None:
            evs = sse_text_pieces(step.get("text", "结束"), int(os.environ.get("STUB_SLOW_PIECES") or 8))
            gap = slow / 1000.0 / max(1, len(evs))
            for ev in evs:
                piece = ("data: %s\n\n" % json.dumps(ev, ensure_ascii=False)).encode()
                try:
                    self.wfile.write(("%X\r\n" % len(piece)).encode() + piece + b"\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return          # 客户端被 Ctrl+C 打断后断开:正常结束
                time.sleep(gap)
            try:
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        # 分两块吐,顺带验证 ask_ai 的增量解析不粘包
        half = len(out) // 2
        for piece in (out[:half], out[half:]):
            self.wfile.write(("%X\r\n" % len(piece)).encode() + piece + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")


port = sys.argv[1]
# ⚠ 必须用**多线程**版本:单线程 HTTPServer 会把下一个请求排在"上一个响应的慢速
# 吐字"后面 —— 于是 Ctrl+C 打断后那句"socket 已断但仍要吐满 8 秒"的响应会把
# **下一条请求**一起拖住。实测(Docker/Ubuntu)`interrupt_e2e` 的"中断后仍可继续对话"
# 就是被这个拖过 25 秒窗口而假红;macOS 上因为 socket 更快拿到 RST 而侥幸通过。
# 真网关是并发的,桩也该并发。`_ctr`/`_lock` 已经把顺序号保护住了。
from http.server import ThreadingHTTPServer  # noqa: E402

ThreadingHTTPServer(("127.0.0.1", int(port)), H).serve_forever()