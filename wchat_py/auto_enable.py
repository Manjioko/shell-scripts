#!/usr/bin/env python3
"""auto 模式装配(对应 bash auto_enable):横幅 + 签字 + 回滚点/备份 + 审计。

只做装配与状态;真正的裁决在 run_shell_command/_auto_judge。
"""
import os
import subprocess
import sys
import time


def _git_rev():
    try:
        p = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        return p.stdout.strip() if p.returncode == 0 else "(非 git 仓库)"
    except Exception:
        return "(非 git 仓库)"


def _branch():
    try:
        p = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        return p.stdout.strip() if p.returncode == 0 else "-"
    except Exception:
        return "-"


def _count_paths(v):
    return len([x for x in (v or "").replace(",", " ").split() if x])


def enable(tier):
    if not tier:
        return True
    from wchat_py import audit
    root = os.environ.get("WORK_ROOT") or os.path.realpath(os.getcwd())
    ct = os.environ.get("CT_HOME") or os.path.join(os.environ.get("HOME", ""), ".ct")
    flash = os.environ.get("FLASH_MODEL", "") or "?"
    auth = os.environ.get("AUTH_MODEL") or os.environ.get("PRO_MODEL") or flash
    allow_w = os.environ.get("WCHAT_ALLOW_WRITE", "")
    max_rounds = os.environ.get("MAX_TOOL_ROUNDS") or "250"
    cmd_to = os.environ.get("CMD_TIMEOUT") or "120"
    quota = os.environ.get("AUTO_MAX_APPROVALS") or "200"
    streak = os.environ.get("AUTO_DENY_STREAK") or "3"
    install = os.environ.get("WCHAT_ALLOW_INSTALL", "")
    mcp = "已启用" if os.environ.get("MCP_ENABLE") == "1" else \
        "未启用(不带 -mcp 时不注册 mcp_* 通道;模型仍可经 shell 自行调用,见 -h)"

    w = sys.stdout.write
    w("═══ auto 模式:%s ═══\n" % tier)
    w("  工作目录: %s\n" % root)
    w("  审批器:   %s(与执行模型 %s 分离)\n" % (auth, flash))
    w("  越界读:   自动放行(记入审计)\n")
    w("  越界写:   仅 %d 个 --allow-write 路径,且覆盖前备份\n" % _count_paths(allow_w))
    w("  shell N:  由终审器带 reason 复审后自动决定\n")
    w("  硬墙(任何档位都不放行): H1 提权 H2 下载即执行 H3 家目录/系统删 H4 磁盘\n")
    w("    H5 家目录配置 H6 凭据 H7 改写发布历史 H8 电源 H9 批量杀进程 H10 外发密钥 H11 系统路径\n")
    w("    H12 越界写目标 H13 目标不可解析(defaults/plutil/osascript/launchctl/钥匙串) H14 安装(默认拒,除非启动时点名)\n")
    w("    越界写(工作目录之外)按路径判据报 P1/P2/P3,与文件工具同一口径\n")
    w("  刹车: 单轮工具≤%s 次 · 单命令≤%ss · 自动放行配额 %s 次 · 连续拒 %s 次降级人工\n"
      % (max_rounds, cmd_to, quota, streak))
    w("  安装面:   一律拒绝(H14)。要放行请退出重启加 --allow-install 包名1,包名2\n")
    w("  MCP:      %s\n" % mcp)
    w("  回滚点:   git %s @ %s\n\n" % (_branch(), _git_rev()))
    sys.stdout.flush()

    if os.environ.get("WCHAT_AUTO_ASSUME_YES") != "1":
        w("  开启后敏感权限不再逐次询问你。确认输入大写 AUTO(其他任意输入 = 关闭 auto): ")
        sys.stdout.flush()
        try:
            ans = (sys.stdin.readline() or "").strip()
        except Exception:
            ans = ""
        w("\n")
        if ans != "AUTO":
            os.environ["AUTO_MODE"] = ""
            os.environ["WCHAT_AUTO_MODE"] = ""
            w("auto 已关闭(未输入 AUTO)\n")
            sys.stdout.flush()
            return False

    ts = time.strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(ct, "auto", "backup", ts)
    try:
        os.makedirs(bdir, exist_ok=True)
    except OSError:
        pass
    alog = os.environ.get("AUTO_AUDIT_LOG") or os.path.join(ct, "auto", "audit.log")
    os.environ["AUTO_AUDIT_LOG"] = alog
    audit.record(alog, "session", verdict="started", tier=tier, work_root=root,
                 backup=bdir, rollback=_git_rev())
    w("⚡ auto 已开启 审计: %s · 备份: %s · 急停: Ctrl+C 两次\n\n" % (alog, bdir))
    w("⚡ auto=%s · 本次会话已自动放行 0 次 · 审计 %s · -noauto 关闭 · Ctrl+C 两次中止并急停\n"
      % (tier, alog.replace(os.path.expanduser("~"), "~")))
    sys.stdout.flush()
    return True

