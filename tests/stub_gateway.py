#!/usr/bin/env python3
"""wchat auto 模式的桩服务端:按脚本回放响应,记录每个请求。

为什么需要它:auto 的三层裁决(硬墙 → 审批器 → 终审器)散在主循环里,抽函数
单测覆盖不到"分支到底有没有被接上"。用桩网关把整条 SSE 链路跑通,才能证明
硬墙命令真的没被执行、放行命令真的被执行了、审计真的落了盘。
"""
import json, os, sys, threading
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
                f.write(json.dumps({"kind": kind, "model": body.get("model"),
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
        script = json.load(open(REPLY))
        step = script[i] if i < len(script) else {"type": "text", "text": "结束"}
        if step["type"] == "tool":
            out = sse_tool_call(step.get("id", "call_%d" % i), step["name"], step["args"]).encode()
        else:
            out = sse_text(step.get("text", "结束")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        # 分两块吐,顺带验证 ask_ai 的增量解析不粘包
        half = len(out) // 2
        for piece in (out[:half], out[half:]):
            self.wfile.write(("%X\r\n" % len(piece)).encode() + piece + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")


port = sys.argv[1]
HTTPServer(("127.0.0.1", int(port)), H).serve_forever()