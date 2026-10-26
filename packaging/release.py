#!/usr/bin/env python3
"""一条命令发版:打包 → 打 tag → 推 GitHub → 建 Release → 更新 tap 里的 Formula。

用法:
  python3 packaging/release.py 0.3.0              # 发 0.3.0
  python3 packaging/release.py patch              # 在当前版本上 +1 个补丁号
  python3 packaging/release.py 0.3.0 --dry-run    # 演练:只打包和打印,不写 git、不推、不发布
  python3 packaging/release.py 0.3.0 --skip-gates # 跳过发布前自检(不建议)
  python3 packaging/release.py 0.3.0 --full-gates # 连主闸门(45 道)一起跑

它会做的事(顺序固定,任何一步失败就停,不会留下"tag 推了但包没传"的中间态):
  1. 前置检查:git 工作区干净、gh 已登录、远程是 GitHub
  2. 发布前自检:打包闸门(tests/brew_dist_test.sh);--full-gates 再加主闸门
  3. 写 wchat_py/version.py(版本号唯一出处)
  4. 打包 dist/wchat-<ver>.tar.gz(可复现:同源码同 sha)
  5. 生成 Formula,url 指向 GitHub Release 资产地址
  6. commit(version + Formula)、打 tag v<ver>、push 分支与 tag
  7. gh release create,上传 tarball
  8. 校验 Release 资产 URL 真的可下载(HTTP 200),否则报警
  9. 打印"别人怎么升级"

为什么先 push 再建 release:gh release create 的资产 URL 依赖 tag 已存在;
反过来(先建 release)会得到一个指向不存在 tag 的 release,删起来更麻烦。
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packaging", "homebrew"))
sys.path.insert(0, ROOT)

import make_release as mr  # noqa: E402

VERFILE = os.path.join(ROOT, "wchat_py", "version.py")
TAP_FORMULA = os.path.join(ROOT, "Formula", "wchat.rb")


def sh(argv, check=True, capture=True, cwd=None):
    r = subprocess.run(argv, cwd=cwd or ROOT, capture_output=capture, text=True)
    if check and r.returncode != 0:
        sys.stderr.write("✗ 命令失败: %s\n%s%s\n" % (" ".join(argv),
                                                    (r.stdout or ""), (r.stderr or "")))
        sys.exit(1)
    return r


def step(msg):
    print("\n\033[1m▶ %s\033[0m" % msg)


def ok(msg):
    print("  ✅ %s" % msg)


def warn(msg):
    print("  ⚠️  %s" % msg)


def fail(msg):
    sys.stderr.write("\n❌ %s\n" % msg)
    sys.exit(1)


def read_version():
    with open(VERFILE, encoding="utf-8") as f:
        m = re.search(r'__version__\s*=\s*"([^"]+)"', f.read())
    if not m:
        fail("读不到 %s 里的 __version__" % VERFILE)
    return m.group(1)


def write_version(v):
    with open(VERFILE, encoding="utf-8") as f:
        s = f.read()
    s = re.sub(r'__version__\s*=\s*"[^"]+"', '__version__ = "%s"' % v, s)
    with open(VERFILE, "w", encoding="utf-8") as f:
        f.write(s)


def bump(cur, part):
    a, b, c = [int(x) for x in cur.split(".")]
    if part == "patch":
        return "%d.%d.%d" % (a, b, c + 1)
    if part == "minor":
        return "%d.%d.0" % (a, b + 1)
    if part == "major":
        return "%d.0.0" % (a + 1)
    return part


def remote_repo():
    """从 origin URL 取 owner/repo。"""
    url = sh(["git", "remote", "get-url", "origin"]).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", url)
    if not m:
        fail("origin 不是 GitHub 仓库: %s" % url)
    return m.group(1), m.group(2)


def preflight(dry):
    step("1/8 前置检查")
    if sh(["git", "status", "--porcelain"]).stdout.strip() and not dry:
        fail("工作区有未提交的改动。先 commit,或加 --dry-run 只看打包。")
    ok("工作区干净" if not sh(["git", "status", "--porcelain"]).stdout.strip() else "工作区有改动(--dry-run 允许)")
    r = sh(["gh", "auth", "status"], check=False)
    if r.returncode != 0:
        fail("gh 未登录。先跑: gh auth login")
    ok("gh 已登录")
    owner, repo = remote_repo()
    ok("远程仓库 %s/%s" % (owner, repo))
    return owner, repo


def gates(full):
    step("2/8 发布前自检")
    r = sh(["bash", "tests/brew_dist_test.sh"], check=False)
    tail = [l for l in (r.stdout or "").splitlines() if l.startswith("RESULT")]
    if r.returncode != 0:
        sys.stderr.write(r.stdout[-2000:])
        fail("发行包闸门未通过,拒绝发布")
    ok("发行包闸门 %s" % (tail[-1] if tail else "通过"))
    if full:
        r = sh(["bash", "tests/run_gates.sh", "--main"], check=False)
        g = [l for l in (r.stdout or "").splitlines() if l.startswith("GATES ")]
        if r.returncode != 0 or not g:
            sys.stderr.write(r.stdout[-3000:])
            fail("主闸门未通过,拒绝发布")
        ok("主闸门 %s" % g[-1])
    else:
        warn("未跑主闸门(需要时加 --full-gates)")


def build(version, owner, repo, dry=False):
    step("3/8 写版本号 + 4/8 打包")
    old_ver_src = None
    if dry:
        with open(VERFILE, encoding="utf-8") as f:
            old_ver_src = f.read()
    write_version(version)
    ok("wchat_py/version.py -> %s" % version)
    url = "https://github.com/%s/%s/releases/download/v%s/wchat-%s.tar.gz" % (
        owner, repo, version, version)
    tar = mr.build_tarball(version)
    h = mr.sha256(tar)
    ok("打包 %s (%d KB)" % (os.path.relpath(tar, ROOT), os.path.getsize(tar) // 1024))
    ok("sha256 %s" % h)
    step("5/8 生成 Formula")
    text = (mr.FORMULA.replace("__URL__", url).replace("__SHA__", h)
            .replace("__VER__", version).replace("__HOMEPAGE__", mr.GITHUB_URL))
    if not dry:                      # dry-run 不落盘:演练不该留下"版本已改"的假象
        for p in (TAP_FORMULA, os.path.join(mr.FORMULA_DIR, "wchat.rb")):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            ok("写入 %s" % os.path.relpath(p, ROOT))
    else:
        warn("--dry-run:不写 Formula(以下为关键行预览)")
        for ln in text.splitlines():
            if ln.strip().startswith(("url ", "sha256 ", "version ")):
                print("     " + ln.strip())
        with open(VERFILE, "w", encoding="utf-8") as f:
            f.write(old_ver_src)
        ok("版本号已还原为 %s" % read_version())
    ok("资产 URL %s" % url)
    return tar, h, url


def commit_and_push(version, owner, repo, dry):
    tag = "v%s" % version
    step("6/8 commit + tag + push")
    if dry:
        warn("--dry-run:跳过 git commit/tag/push")
        return tag
    sh(["git", "add", "wchat_py/version.py", "Formula/wchat.rb", "packaging/homebrew/Formula/wchat.rb"])
    r = sh(["git", "commit", "-m", "release: %s" % tag], check=False)
    if r.returncode != 0:
        warn("没有可提交的改动(版本与 Formula 未变?)")
    else:
        ok("已提交 release: %s" % tag)
    if sh(["git", "tag", "-l", tag]).stdout.strip():
        warn("tag %s 已存在,复用" % tag)
    else:
        sh(["git", "tag", "-a", tag, "-m", "wchat %s" % version])
        ok("已打 tag %s" % tag)
    br = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    sh(["git", "push", "origin", br])
    ok("已推分支 %s" % br)
    sh(["git", "push", "origin", tag])
    ok("已推 tag %s" % tag)
    return tag


def create_release(tag, tar, version, dry):
    step("7/8 建 GitHub Release 并上传资产")
    if dry:
        warn("--dry-run:跳过 gh release create")
        return
    exists = sh(["gh", "release", "view", tag], check=False).returncode == 0
    if exists:
        sh(["gh", "release", "upload", tag, tar, "--clobber"])
        ok("Release 已存在,资产已覆盖上传")
    else:
        sh(["gh", "release", "create", tag, tar,
            "--title", "wchat %s" % version,
            "--generate-notes"])
        ok("已创建 Release %s 并上传资产" % tag)


def update_tap_branch(branch, formula_text, version, dry):
    """把 Formula 单独推到 tap 分支(通常是 main)。

    为什么需要:brew tap 拉的是仓库**默认分支**,而源码合并往往还没做/很久以后才做。
    Formula 只依赖 release 资产(不依赖源码),所以可以单独推一个文件——这样别人
    `brew tap` 立刻能装到新版,而不必等整个分支合并。
    用 worktree 而不是 checkout:不打扰当前工作区,也不改你正在改的分支。
    """
    step("6.5 更新 tap 分支 %s 上的 Formula" % branch)
    if dry:
        warn("--dry-run:跳过 tap 分支更新")
        return
    import tempfile
    r = sh(["git", "fetch", "origin", branch], check=False)
    if r.returncode != 0:
        warn("fetch origin %s 失败,跳过(可能分支不存在)" % branch)
        return
    tmp = tempfile.mkdtemp(prefix="wchat-tap-")
    try:
        r = sh(["git", "worktree", "add", "--detach", tmp, "origin/" + branch], check=False)
        if r.returncode != 0:
            warn("建 worktree 失败,跳过: %s" % (r.stderr or "").strip()[:120])
            return
        dest = os.path.join(tmp, "Formula", "wchat.rb")
        old = ""
        if os.path.exists(dest):
            with open(dest, encoding="utf-8") as f:
                old = f.read()
        if old == formula_text:
            ok("Formula 无变化,跳过提交")
            return
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(formula_text)
        sh(["git", "add", "Formula/wchat.rb"], cwd=tmp)
        r = sh(["git", "commit", "-m", "tap: wchat %s" % version], check=False, cwd=tmp)
        if r.returncode != 0:
            warn("提交失败(可能没配 user.email),跳过: %s" % (r.stderr or "").strip()[:120])
            return
        r = sh(["git", "push", "origin", "HEAD:" + branch], check=False, cwd=tmp)
        if r.returncode != 0:
            warn("推送 %s 失败(分支保护?)。Formula 已生成,可手动提交: %s"
                 % (branch, (r.stderr or "").strip()[:160]))
            return
        ok("已把 Formula 推到 %s" % branch)
    finally:
        sh(["git", "worktree", "remove", "--force", tmp], check=False)


def verify_asset(url, dry):
    step("8/8 校验资产可下载")
    if dry:
        warn("--dry-run:跳过 URL 校验")
        return
    r = sh(["curl", "-sSIL", "-o", "/dev/null", "-w", "%{http_code}", url], check=False)
    code = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
    if code.startswith("2") or code.startswith("3"):
        ok("资产 URL 返回 %s" % code)
    else:
        warn("资产 URL 返回 %s(刚发布可能需要几秒生效;持续失败请检查 Release)" % code)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version", help="x.y.z,或 patch/minor/major")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-gates", action="store_true")
    ap.add_argument("--full-gates", action="store_true")
    ap.add_argument("--no-verify-url", action="store_true")
    ap.add_argument("--tap-branch", default="main",
                    help="把 Formula 单独推到哪个分支供 brew tap 使用(默认 main;写空则跳过)")
    a = ap.parse_args()

    cur = read_version()
    version = bump(cur, a.version)
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        fail("版本号必须是 x.y.z(当前 %s)" % cur)
    print("\033[1mwchat 发布\033[0m  当前 %s → 新版本 %s%s"
          % (cur, version, "  \033[33m[dry-run]\033[0m" if a.dry_run else ""))

    owner, repo = preflight(a.dry_run)
    if not a.skip_gates:
        gates(a.full_gates)
    else:
        warn("已跳过发布前自检(--skip-gates)")
    tar, h, url = build(version, owner, repo, a.dry_run)
    if a.tap_branch:
        with open(os.path.join(mr.FORMULA_DIR, "wchat.rb"), encoding="utf-8") as f:
            formula_text = f.read() if not a.dry_run else None
        if a.dry_run:
            formula_text = (mr.FORMULA.replace("__URL__", url).replace("__SHA__", h)
                            .replace("__VER__", version).replace("__HOMEPAGE__", mr.GITHUB_URL))
        update_tap_branch(a.tap_branch, formula_text, version, a.dry_run)
    tag = commit_and_push(version, owner, repo, a.dry_run)
    create_release(tag, tar, version, a.dry_run)
    if not a.no_verify_url:
        verify_asset(url, a.dry_run)

    print("\n\033[1m完成\033[0m")
    print("  版本     %s (tag %s)" % (version, tag))
    print("  资产     %s" % url)
    print("  sha256   %s" % h)
    print("\n别人这样装/升级:")
    print("  # 首次(仓库不是 homebrew-* 前缀,所以要显式给 URL)")
    print("  brew tap %s/%s https://github.com/%s/%s" % (owner, repo, owner, repo))
    print("  brew install %s/%s/wchat" % (owner, repo))
    print("  # 之后每次升级")
    print("  brew update && brew upgrade wchat")


if __name__ == "__main__":
    main()