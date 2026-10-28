#!/usr/bin/env python3
"""apilog —— 每次 API 调用的**结构化记账**(谁发的、发了多大、花了多少 token)。

为什么需要它:`-u` 只显示"最近一次请求的 prompt_tokens",看不出**钱花在哪个调用上**。
而这个项目里有 5 个不同的 API 调用点(主对话流式 / 生成标题 / 压缩摘要 / 提炼技能 /
连通性自检),其中 4 个是非流式的、以前**完全不记 usage** —— 出了"token 消耗快",
根本没法归因。

三条硬规矩:
  1. **绝不记录正文**。只记 role 序列、字节数、每条消息的 sha1 前 8 位。
     理由:对话里有用户隐私,还可能夹着密钥;审计日志不是内容备份。
  2. **记账失败绝不影响主流程**。所有写盘都包在 try 里 —— 记账是观测,不是功能。
  3. **有上限**。默认单文件 8 MiB 就轮转一次,最多保留 3 个旧文件。
     记账把磁盘写爆,在这个项目里属于"顺手清理"级别的自伤。

用途(全部离线,不需要网络):
    python3 -m wchat_py.apilog report            # 按来源汇总 token 占比
    python3 -m wchat_py.apilog tail -n 20        # 最近 20 次调用
    python3 -m wchat_py.apilog audit             # 找"可疑浪费"(见 audit())
"""
import hashlib
import json
import os
import sys
import time

MAX_BYTES = 8 * 1024 * 1024
KEEP = 3


def _is_local_endpoint():
    """端点是不是本机桩(测试用的 127.0.0.1:PORT / localhost)。

    为什么必须判它:**闸门测试会给 wchat 喂一个本地桩端点**,如果不区分,测试流量
    就会写进用户真实的 ~/.ct/usage/calls.jsonl。实测踩过:一次全量闸门塞进去 24 条
    `prompt=0` 的记录,把"生产用量"的统计彻底搅浑 —— 而这类污染**看起来像产品缺陷**
    (A5 判据会对它们全报警),实际只是测试。
    """
    base = (os.environ.get("BASE_URL") or "").strip().lower()
    if not base:
        return False
    for host in ("127.0.0.1", "localhost", "[::1]", "0.0.0.0"):
        if host in base:
            return True
    return False


def enabled():
    """默认开。WCHAT_API_LOG=0 关掉;本机桩端点一律不记(见 _is_local_endpoint)。"""
    if os.environ.get("WCHAT_API_LOG", "1") in ("0", "off", "no", "false"):
        return False
    if _is_local_endpoint():
        return False
    return True


def log_dir():
    if os.environ.get("WCHAT_API_LOG_DIR"):
        return os.environ["WCHAT_API_LOG_DIR"]
    ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME") or "", ".ct")
    return os.path.join(ct, "usage")


def log_path():
    return os.path.join(log_dir(), "calls.jsonl")


# ------------------------------------------------------------------ 内容指纹

def _text_of(content):
    """把消息 content(可能是字符串或 parts 数组)折成文本,只用于算指纹/字节。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for p in content:
            if isinstance(p, dict):
                t = p.get("text")
                if isinstance(t, str):
                    out.append(t)
                elif p.get("type") == "image_url":
                    out.append("<image>")
        return "\n".join(out)
    if content is None:
        return ""
    return str(content)


def _role_of(m):
    return (m.get("role") if isinstance(m, dict) else "") or "?"


def _sha8(s):
    return hashlib.sha1((s or "").encode("utf-8", "replace")).hexdigest()[:8]


def digest(msgs):
    """给一组 messages 算结构摘要(**不含正文**)。

    返回:
      n        : 消息条数
      bytes    : 正文字节合计(UTF-8)
      chars    : 字符数
      roles    : 角色序列,如 "system,user,assistant,tool"
      dg       : 整组内容的指纹(用于判"两次请求是不是同一份东西")
      dg_first : 第一条消息的指纹(用于判"前缀是否稳定 → 缓存能不能命中")
      sys_bytes: system 消息字节(单独列:它每轮都发,是主要固定成本)
    """
    n = len(msgs or [])
    total = 0
    sys_b = 0
    parts = []
    first = ""
    for i, m in enumerate(msgs or []):
        t = _text_of(m.get("content") if isinstance(m, dict) else m)
        b = len(t.encode("utf-8", "replace"))
        total += b
        if _role_of(m) == "system":
            sys_b += b
        if i == 0:
            first = t
        parts.append("%s:%d:%s" % (_role_of(m), b, _sha8(t)))
    return {"n": n, "bytes": total, "sys_bytes": sys_b,
            "roles": ",".join(_role_of(m) for m in (msgs or [])),
            "dg": _sha8("\x1f".join(parts)), "dg_first": _sha8(first)}


def est_tokens(byte_n):
    """粗估 token:中英混排按 ~3.6 字节/token(与 docs 里的实测折算一致)。

    只在**拿不到真实 usage** 时用来给一个量级;有 usage 就一律用真值。
    """
    return int(byte_n / 3.6)


# ------------------------------------------------------------------ 记账

def _rotate(p):
    try:
        if not os.path.isfile(p) or os.path.getsize(p) < MAX_BYTES:
            return
        for i in range(KEEP - 1, 0, -1):
            a = "%s.%d" % (p, i)
            b = "%s.%d" % (p, i + 1)
            if os.path.isfile(a):
                os.replace(a, b)
        os.replace(p, p + ".1")
    except OSError:
        pass


def tools_bytes(tools):
    """把 tools schema 折成字节数。

    为什么必须算它:实测 10 个内置工具的 schema 是 **9036 字节(约 2510 token)**,
    而一句"你好"的 messages 只有 1674 字节。**每轮请求里约 95% 的输入 token 是
    工具定义**,但它不在这条消息列表里 —— 只算 messages 的账本会虚报 5 倍,
    所有"用量"分析全歪。
    """
    if not tools:
        return 0
    try:
        return len(json.dumps(tools, ensure_ascii=False).encode("utf-8", "replace"))
    except Exception:
        return 0


def record(source, model="", msgs=None, usage=None, ok=True, extra=None, tools=None):
    """记一笔调用。source 是**必填**的归因标签,例如:
         main        主对话(流式)
         title       生成对话标题
         compact     压缩摘要
         skill       提炼技能
         ping        连通性自检
         mcp         预留给 MCP 相关请求
    绝不让异常冒出去:记账坏了不该让对话坏掉。
    """
    if not enabled():
        return
    try:
        d = digest(msgs or [])
        u = usage or {}
        rec = {
            "ts": int(time.time()),
            # pid 是"同一次任务"的判据:跨进程的相同内容 = 用户自己问了两次(正常),
            # 同进程内的相同内容 = 才是"同一轮里重复发送"(可疑)。没有这个字段的话
            # A2 会对"两次独立提问同一个问题"误报 —— 实测栽过一次。
            "pid": os.getpid(),
            "sess": os.environ.get("WCHAT_SESSION_ID") or "",
            "src": source or "?",
            "model": model or "",
            "msgs": d["n"],
            "bytes": d["bytes"],
            # 工具定义**每轮都发**,是最大的固定成本;单列一栏,不用它污染 messages 的字节
            "tools_bytes": tools_bytes(tools),
            "total_bytes": d["bytes"] + tools_bytes(tools),
            "sys_bytes": d["sys_bytes"],
            "roles": d["roles"],
            "dg": d["dg"],
            "dg_first": d["dg_first"],
            # usage:有真值就记真值,没有就记空(不要用估算冒充计费数)
            "prompt": int(u.get("prompt") or 0),
            "cached": int(u.get("cached") or 0),
            "completion": int(u.get("completion") or 0),
            "image": int(u.get("image") or 0),
            "ok": bool(ok),
        }
        if extra:
            for k, v in extra.items():
                if k not in rec:
                    rec[k] = v
        p = log_path()
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        _rotate(p)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_all():
    """读全部记录(含轮转过的旧文件),按时间升序。坏行跳过。"""
    p = log_path()
    files = [p + ".%d" % i for i in range(KEEP, 0, -1)] + [p]
    out = []
    for f in files:
        if not os.path.isfile(f):
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        out.append(json.loads(ln))
                    except ValueError:
                        continue
        except OSError:
            continue
    out.sort(key=lambda r: r.get("ts") or 0)
    return out


# ------------------------------------------------------------------ 报告

def summarize(rows):
    """按来源汇总:调用次数、prompt/completion 总量、平均消息条数。"""
    agg = {}
    for r in rows:
        s = r.get("src") or "?"
        a = agg.setdefault(s, {"n": 0, "prompt": 0, "cached": 0,
                               "completion": 0, "msgs": 0, "bytes": 0, "ok": 0,
                               "tools_bytes": 0})
        a["n"] += 1
        a["prompt"] += int(r.get("prompt") or 0)
        a["cached"] += int(r.get("cached") or 0)
        a["completion"] += int(r.get("completion") or 0)
        a["msgs"] += int(r.get("msgs") or 0)
        a["bytes"] += int(r.get("bytes") or 0)
        a["tools_bytes"] += int(r.get("tools_bytes") or 0)
        a["ok"] += 1 if r.get("ok") else 0
    return agg


def audit(rows):
    """找"可疑浪费"。每条都给出**可复核的证据**,不是打分。

    判据(都是结构性的、可解释的):
      A1 非主对话调用带了大上下文:src != main 且 msgs > 3。标题/压缩各自只需 1-2 条;
         带 10 条就是"把整个会话发给 AI"。
      A2 同一轮内完全相同内容的重复调用:src+model+dg 相同且相隔 < 60s。
         常见于失败重试没变输入,或同一个请求被发了两遍。
      A3 缓存疑似不命中:prompt 很大(> 2000)而 cached == 0,且是同一会话的连续 main。
         后者要求"system 前缀稳定";前缀一变,每轮都按全价重算。
      A4 单次请求里 system 占比过高:sys_bytes / bytes > 0.5 且 bytes > 20000。
         技能清单/MCP 清单每轮都发,膨胀会直接放大每次调用。
      A5 usage 缺失:有调用但 prompt == 0 且它是主对话 —— 说明 usage 没回来,
         所有"用量"数字都是估的(这一条以前**所有非流式调用**都中)。
    """
    out = []
    prev_main = None
    for i, r in enumerate(rows):
        src = r.get("src") or "?"
        msgs = int(r.get("msgs") or 0)
        if src != "main" and msgs > 3:
            out.append(("A1", i, "非主对话 %s 带了 %d 条消息(%d 字节)"
                        % (src, msgs, int(r.get("bytes") or 0))))
        if src == "main" and int(r.get("prompt") or 0) == 0:
            out.append(("A5", i, "主对话调用没有 usage(prompt=0),用量只能估算"))
        b = int(r.get("bytes") or 0)
        sb = int(r.get("sys_bytes") or 0)
        if b > 20000 and sb and sb / float(b) > 0.5:
            out.append(("A4", i, "system 占 %d/%d 字节(%.0f%%),固定成本偏大"
                        % (sb, b, 100.0 * sb / b)))
        if src == "main":
            p_tok = int(r.get("prompt") or 0)
            if p_tok > 2000 and int(r.get("cached") or 0) == 0 and prev_main is not None:
                out.append(("A3", i, "prompt=%d 但缓存命中 0(上一轮 prompt=%d);"
                            "检查 system 前缀是否每轮都变" % (p_tok, prev_main)))
            prev_main = p_tok
    # A2 重复调用
    seen = {}
    for i, r in enumerate(rows):
        pid = r.get("pid")
        k = (r.get("src"), r.get("model"), r.get("dg"), pid)
        t = int(r.get("ts") or 0)
        if k in seen:
            j, tj = seen[k]
            # 只有**同一进程**内的相同内容才算重复发送。跨进程相等是用户自己
            # 问了两次同样的话,不该报。
            if pid is not None and 0 <= t - tj < 60:
                out.append(("A2", i, "同一进程内与第 %d 行内容完全相同且间隔 %ds"
                            "(疑似重复发送)" % (j + 1, t - tj)))
        seen[k] = (i, t)
    return out


def cmd_report(argv):
    rows = read_all()
    if not rows:
        sys.stdout.write("还没有记录(目录: %s)\n" % log_dir())
        sys.stdout.write("用 WCHAT_API_LOG=1 跑一次对话后再看。\n")
        return 0
    agg = summarize(rows)
    total_p = sum(a["prompt"] for a in agg.values())
    total_c = sum(a["cached"] for a in agg.values())
    total_o = sum(a["completion"] for a in agg.values())
    sys.stdout.write("API 调用记账: %s\n" % log_path())
    sys.stdout.write("共 %d 次调用\n\n" % len(rows))
    sys.stdout.write("%-8s %5s %10s %10s %7s %8s %10s\n"
                     % ("来源", "次数", "prompt", "其中缓存", "completion",
                        "占比", "工具定义"))
    for src, a in sorted(agg.items(), key=lambda kv: -kv[1]["prompt"]):
        share = (100.0 * a["prompt"] / total_p) if total_p else 0.0
        sys.stdout.write("%-8s %5d %10d %10d %7d %7.1f%% %10d\n"
                         % (src, a["n"], a["prompt"], a["cached"],
                            a["completion"], share, a["tools_bytes"]))
    sys.stdout.write("\n合计 prompt=%d(缓存 %d,未命中 %d) completion=%d\n"
                     % (total_p, total_c, total_p - total_c, total_o))

    # 固定成本占比 —— 这是本项目最容易被忽略的一笔账。
    # 实测:10 个内置工具的 schema = 9036 字节 ≈ 2510 token;而一句闲聊只有 ~400 token。
    # 也就是说**每次请求里约 95% 的输入是工具定义**。
    tb = sum(a["tools_bytes"] for a in agg.values())
    nb = sum(a["bytes"] for a in agg.values())
    if nb + tb:
        sys.stdout.write("输入构成: 消息正文 %d 字节 + 工具定义 %d 字节"
                         "(工具占 %.0f%%)\n"
                         % (nb, tb, 100.0 * tb / (nb + tb)))
        sys.stdout.write("  ↑ 工具定义随**每次**请求重发。它是固定成本,不是你这轮说的话。\n")
    if total_p and total_c:
        sys.stdout.write("缓存命中率: %.1f%%(命中部分按缓存价计费)\n"
                         % (100.0 * total_c / total_p))
    return 0


def cmd_tail(argv):
    n = 20
    if argv:
        try:
            n = int(argv[0])
        except ValueError:
            pass
    rows = read_all()[-n:]
    for i, r in enumerate(rows):
        sys.stdout.write(
            "%s %-8s msgs=%-3d 正文=%-6d 工具=%-6d prompt=%-6d cached=%-6d out=%-4d %s\n"
            % (time.strftime("%m-%d %H:%M:%S", time.localtime(r.get("ts") or 0)),
               r.get("src"), int(r.get("msgs") or 0), int(r.get("bytes") or 0),
               int(r.get("tools_bytes") or 0),
               int(r.get("prompt") or 0), int(r.get("cached") or 0),
               int(r.get("completion") or 0),
               r.get("roles", "")[:50]))
    return 0


def cmd_audit(argv):
    rows = read_all()
    if not rows:
        sys.stdout.write("还没有记录\n")
        return 0
    found = audit(rows)
    if not found:
        sys.stdout.write("没发现结构性浪费(判据 A1-A5 全过)\n")
        return 0
    sys.stdout.write("发现 %d 处可疑:\n" % len(found))
    for code, idx, msg in found:
        r = rows[idx]
        sys.stdout.write("  [%s] 第 %d 次调用(%s, %s): %s\n"
                         % (code, idx + 1, r.get("src"), r.get("model"), msg))
    return 0


def main(argv):
    sub = argv[0] if argv else "report"
    if sub in ("report", "r"):
        return cmd_report(argv[1:])
    if sub in ("tail", "t"):
        return cmd_tail(argv[1:])
    if sub in ("audit", "a"):
        return cmd_audit(argv[1:])
    if sub in ("-h", "--help", "help"):
        sys.stdout.write(__doc__)
        return 0
    sys.stderr.write("未知子命令: %s(可用 report / tail / audit)\n" % sub)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

