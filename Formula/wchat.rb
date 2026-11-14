class Wchat < Formula
  desc "Terminal AI assistant (Python, bundled venv)"
  homepage "https://github.com/Manjioko/shell-scripts"
  url "https://github.com/Manjioko/shell-scripts/releases/download/v0.5.0/wchat-0.5.0.tar.gz"
  sha256 "0fb53b68a8a43f55318e88280154ebd341902a2ea5898b41e07be861b23c1b96"
  version "0.5.0"
  license "MIT"

  depends_on "python@3.12"

  def install
    libexec.install "wchat", "wchat_py", "requirements.txt", "requirements-doc.txt"
    (libexec/"configs").install "configs/config.example"
    (libexec/"configs").install "configs/ctmodel.py"
    (libexec/"configs").install "configs/mcporter.example.json"
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
    # 可选:文档转换(.docx/.pdf→Markdown,markitdown)。装不上不算失败。
    begin
      system venv/"bin/pip", "install", "-q", "--disable-pip-version-check",
             "--no-warn-script-location", "-r", libexec/"requirements-doc.txt"
    rescue => e
      opoo "文档转换依赖安装失败(不影响主功能): #{e}"
    end
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
