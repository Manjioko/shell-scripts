#!/usr/bin/env python3
"""生成 Homebrew 私有 tap 的源码 tarball 与 Formula。

用法:
  python3 packaging/homebrew/make_release.py 0.1.0
  # 发布到 tap 时指定最终 URL(默认用本地 file:// 方便试装):
  python3 packaging/homebrew/make_release.py 0.1.0 \
      --url https://github.com/<you>/homebrew-wchat/releases/download/v0.1.0/wchat-0.1.0.tar.gz
"""
import argparse
import hashlib
import os
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIST = os.path.join(ROOT, "dist")
FORMULA_DIR = os.path.join(ROOT, "packaging", "homebrew", "Formula")
INCLUDE = ["wchat", "wchat_py", "input_helper.py", "render_helper.py", "requirements.txt"]
CONFIG = os.path.join("configs", "config.example")

FORMULA = '''class Wchat < Formula
  desc "Terminal AI assistant (Python, first-run venv bootstrap)"
  homepage "https://example.invalid/wchat"
  url "__URL__"
  sha256 "__SHA__"
  version "__VER__"

  depends_on "python@3.12"

  def install
    libexec.install "wchat", "wchat_py", "input_helper.py", "render_helper.py", "requirements.txt"
    (libexec/"configs").install "configs/config.example"
    py = Formula["python@3.12"].opt_bin/"python3"
    (bin/"wchat").write <<~EOS
      #!/bin/bash
      exec "#{py}" "#{libexec}/wchat" "$@"
    EOS
    chmod 0755, bin/"wchat"
  end

  test do
    assert_match "使用方法", shell_output("WCHAT_NO_BOOTSTRAP=1 #{bin}/wchat -h 2>&1")
  end
end
'''


def build_tarball(version):
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "wchat-%s.tar.gz" % version)
    top = "wchat-%s" % version
    with tarfile.open(out, "w:gz") as tf:
        for name in INCLUDE:
            path = os.path.join(ROOT, name)
            if os.path.exists(path):
                tf.add(path, arcname=os.path.join(top, name),
                       filter=lambda ti: (setattr(ti, "uid", 0), setattr(ti, "gid", 0),
                                          setattr(ti, "uname", ""), setattr(ti, "gname", ""), ti)[-1])
        cfg = os.path.join(ROOT, CONFIG)
        if os.path.exists(cfg):
            tf.add(cfg, arcname=os.path.join(top, CONFIG))
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version", nargs="?", default="0.1.0")
    ap.add_argument("--url", default="")
    a = ap.parse_args()
    tar = build_tarball(a.version)
    h = sha256(tar)
    url = a.url or ("file://" + os.path.abspath(tar))
    os.makedirs(FORMULA_DIR, exist_ok=True)
    fp = os.path.join(FORMULA_DIR, "wchat.rb")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(FORMULA.replace("__URL__", url).replace("__SHA__", h)
                .replace("__VER__", a.version))
    print("tarball:", tar)
    print("sha256:", h)
    print("formula:", fp)
    print("url:", url)
    print()
    print("本地试装: brew install --formula %s" % fp)
    print("发布到 tap: 把 tarball 作为 release 附件, 用 --url 重新生成本 Formula")


if __name__ == "__main__":
    main()