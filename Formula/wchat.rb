class Wchat < Formula
  desc "Terminal AI assistant (Python, bundled venv)"
  homepage "https://github.com/Manjioko/shell-scripts"
  url "https://github.com/Manjioko/shell-scripts/releases/download/v0.2.4/wchat-0.2.4.tar.gz"
  sha256 "8ecb4f58fe43104c104209813f0850d1534d8d8f74daf1cb09ac6072fa3cd31d"
  version "0.2.4"
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
      exec "#{venv}/bin/python" "#{libexec}/configs/ctmodel.py" "$@"
    EOS
    chmod 0755, bin/"ctmodel"
  end

  test do
    # 依赖已随包安装,这里必须能在**无网络**下直接跑通
    assert_match "使用方法", shell_output("#{bin}/wchat -h 2>&1")
    assert_match "当前模型", shell_output("#{bin}/ctmodel 2>&1")
  end
end
