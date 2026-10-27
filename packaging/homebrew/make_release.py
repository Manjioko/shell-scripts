#!/usr/bin/env python3
"""生成 Homebrew 私有 tap 的源码 tarball 与 Formula。

用法:
  python3 packaging/homebrew/make_release.py 0.2.0
  # 发布到 tap 时指定最终 URL(默认用本地 file:// 方便试装):
  python3 packaging/homebrew/make_release.py 0.2.0 \
      --url https://github.com/<you>/homebrew-wchat/releases/download/v0.2.0/wchat-0.2.0.tar.gz

产物:
  dist/wchat-<ver>.tar.gz            源码包(不含 __pycache__/.pyc/.DS_Store)
  packaging/homebrew/Formula/wchat.rb  指向该包的 Formula(sha256 已填好)

为什么包内排 "垃圾文件":开发机上跑过一次 Python 就会留下 wchat_py/__pycache__,
  递归打包会把它一起带走 —— 既让包体虚胖,更糟的是**旧 .pyc 可能盖住新源码**
  (同名 .py 被删时,残留 .pyc 仍可被 import),装出来的东西和仓库不一致,极难查。
为什么固定 mtime 与 gzip 头:同一份源码重复打包得到同一 sha256,Formula 的
  sha 才不会每次提交都无意义地抖一下(diff 里全是 sha 噪音)。
"""
import argparse
import gzip
import hashlib
import os
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIST = os.path.join(ROOT, "dist")
FORMULA_DIR = os.path.join(ROOT, "packaging", "homebrew", "Formula")
INCLUDE = ["wchat", "wchat_py", "input_helper.py", "render_helper.py", "requirements.txt",
           "README.md", "docs/brew-package.md", "skills"]
GITHUB_URL = "https://github.com/Manjioko/shell-scripts"
CONFIG = os.path.join("configs", "config.example")
CONFIG_PY = os.path.join("configs", "ctmodel.py")   # 模型档案切换,随包安装为 bin/ctmodel
MCP_EXAMPLE = os.path.join("configs", "mcporter.example.json")  # MCP 清单模板(wchat mcp init)

# 打包时剔除:字节码缓存/编辑器/系统垃圾。匹配的是**路径任一组件或文件名**。
SKIP_DIRS = {"__pycache__", ".mypy_cache", ".pytest_cache", ".git", "build", "dist"}
SKIP_NAMES = {".DS_Store", ".gitignore", "Thumbs.db"}
SKIP_SUFFIX = (".pyc", ".pyo", ".pyd", ".swp", ".orig", ".rej")

FIXED_MTIME = int(os.environ.get("SOURCE_DATE_EPOCH") or 1700000000)


def _skip(path):
    """判断一个绝对路径是否该跳过(看它相对 ROOT 的每个组件)。"""
    rel = os.path.relpath(path, ROOT)
    for part in rel.split(os.sep):
        if part in SKIP_DIRS:
            return True
        if part in SKIP_NAMES or part.endswith(SKIP_SUFFIX):
            return True
        if part.startswith(".#"):
            return True
    return False


def _walk(top):
    """稳定序遍历(字典序),保证同内容两次打包条目顺序一致。"""
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for d in dirnames:
            yield os.path.join(dirpath, d)
        for f in sorted(filenames):
            p = os.path.join(dirpath, f)
            if not _skip(p):
                yield p


def _filter(ti):
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = "root"
    ti.mtime = FIXED_MTIME
    if ti.isdir():
        ti.mode = 0o755
    elif ti.mode & 0o111:
        ti.mode = 0o755
    else:
        ti.mode = 0o644
    return ti


def build_tarball(version, out_dir=None):
    d = out_dir or DIST
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, "wchat-%s.tar.gz" % version)
    top = "wchat-%s" % version
    with open(out, "wb") as raw:
        # mtime=0 写进 gzip 头:否则头里带当前时间,同一个包每次 sha 都不同
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tf:
                for name in INCLUDE:
                    path = os.path.join(ROOT, name)
                    if not os.path.exists(path):
                        continue
                    if os.path.isdir(path):
                        tf.add(path, arcname=os.path.join(top, name), filter=_filter,
                               recursive=False)
                        for p in _walk(path):
                            tf.add(p, arcname=os.path.join(top, os.path.relpath(p, ROOT)),
                                   filter=_filter, recursive=False)
                    else:
                        tf.add(path, arcname=os.path.join(top, name), filter=_filter)
                for rel in (CONFIG, CONFIG_PY, MCP_EXAMPLE):
                    f = os.path.join(ROOT, rel)
                    if os.path.exists(f):
                        tf.add(f, arcname=os.path.join(top, rel), filter=_filter)
    return out


FORMULA = '''class Wchat < Formula
  desc "Terminal AI assistant (Python, bundled venv)"
  homepage "__HOMEPAGE__"
  url "__URL__"
  sha256 "__SHA__"
  version "__VER__"
  license "MIT"

  depends_on "python@3.12"

  def install
    libexec.install "wchat", "wchat_py", "input_helper.py", "render_helper.py", "requirements.txt"
    (libexec/"configs").install "configs/config.example"
    (libexec/"configs").install "configs/ctmodel.py"
    (libexec/"doc").install "README.md" if File.exist?("README.md")
    (libexec/"doc").install Dir["docs/*.md"] if Dir.exist?("docs")

    # 安装期把依赖装进 libexec/venv:运行期零联网、零自举、启动快。
    # 不这么做就得靠运行时 bootstrap,而 brew test(以及别人的 CI)是断网环境,
    # 必然红;更糟的是首次运行要联网下载,对"装完就能用"是硬伤。
    py = Formula["python@3.12"].opt_libexec/"bin/python"
    venv = libexec/"venv"
    system py, "-m", "venv", venv
    system venv/"bin/pip", "install", "-q", "--disable-pip-version-check",
           "--no-warn-script-location", "-r", libexec/"requirements.txt"
    # 清掉 pip 缓存与 __pycache__,别让 Cellar 里塞一堆用不上的字节码
    rm_rf Dir[venv/"**/__pycache__"]

    (bin/"wchat").write <<~EOS
      #!/bin/bash
      exec "#{venv}/bin/python" "#{libexec}/wchat" "$@"
    EOS
    chmod 0755, bin/"wchat"
    (bin/"ctmodel").write <<~EOS
      #!/bin/bash
      # 兼容壳:实现只有一个(wchat_py/setup.cmd_model),这里直接转过去。
      exec "#{venv}/bin/python" "#{libexec}/wchat" model "$@"
    EOS
    chmod 0755, bin/"ctmodel"
  end

  test do
    # 依赖已随包安装,这里必须能在**无网络**下直接跑通
    assert_match "使用方法", shell_output("#{bin}/wchat -h 2>&1")
    # 模型切换的两个入口必须是同一个实现:直接入口 + 兼容壳
    assert_match "当前模型", shell_output("#{bin}/wchat model 2>&1")
    assert_match "当前模型", shell_output("#{bin}/ctmodel 2>&1")
  end
end
'''


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version", nargs="?", default="0.2.0")
    ap.add_argument("--url", default="")
    ap.add_argument("--tap", default="local/wchat", help="本地 tap 名(user/repo)")
    ap.add_argument("--out-dir", default="", help="tarball 输出目录(默认 <repo>/dist)")
    ap.add_argument("--formula-out", default="", help="Formula 输出路径(默认 packaging/homebrew/Formula/wchat.rb)")
    ap.add_argument("--homepage", default=GITHUB_URL, help="Formula 的 homepage")
    a = ap.parse_args()
    tar = build_tarball(a.version, a.out_dir or None)
    h = sha256(tar)
    url = a.url or ("file://" + os.path.abspath(tar))
    fp = a.formula_out or os.path.join(FORMULA_DIR, "wchat.rb")
    os.makedirs(os.path.dirname(os.path.abspath(fp)), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(FORMULA.replace("__URL__", url).replace("__SHA__", h)
                .replace("__VER__", a.version).replace("__HOMEPAGE__", a.homepage))
    print("tarball:", tar)
    print("sha256:", h)
    print("formula:", fp)
    print("url:", url)
    print()
    print("Homebrew 7 要求 Formula 位于 tap 内;本地试装:")
    print("  brew tap-new %s" % a.tap)
    print('  mkdir -p "$(brew --repository %s)/Formula"' % a.tap)
    print('  cp "%s" "$(brew --repository %s)/Formula/"' % (fp, a.tap))
    print("  brew install %s/wchat" % a.tap)
    print("发布到 tap: 把 tarball 作为 release 附件, 用 --url 重新生成本 Formula")


if __name__ == "__main__":
    main()