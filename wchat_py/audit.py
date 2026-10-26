#!/usr/bin/env python3
"""审计记录（auto 审批链的可测地基）：把一次决策追加一行 JSON 到 AUTO_AUDIT_LOG。

bash 侧 auto_audit 写的是"只追加"审计；此处先做最小可验的记录器，字段可扩。
"""
import json
import os
import time


def default_log():
    """与 bash 侧一致:AUTO_AUDIT_LOG 缺省为 ${CT_HOME:-$HOME/.ct}/auto/audit.log。

    测试会 `env -u AUTO_AUDIT_LOG` 清宿主泄漏,所以缺省值必须由本函数补回,
    否则拒绝路径"静默不记审计"——那正是审计最该留痕的地方。
    """
    ct = os.environ.get("CT_HOME") or os.path.join(
        os.environ.get("HOME") or os.path.expanduser("~"), ".ct")
    return os.path.join(ct, "auto", "audit.log")


def record(log_path, kind, **fields):
    """追加一行审计 {ts,kind,...fields} 到 log_path；路径空则用 default_log()。
    写失败返回 False(不抛)。"""
    if not log_path:
        log_path = default_log()
    d = os.path.dirname(log_path)
    try:
        if d:
            os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    row = {"ts": int(time.time()), "kind": kind}
    row.update(fields)
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        return True
    except OSError:
        return False