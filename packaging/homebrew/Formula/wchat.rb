class Wchat < Formula
  desc "Terminal AI assistant (Python, first-run venv bootstrap)"
  homepage "https://example.invalid/wchat"
  url "file:///Users/jwan/bin/dist/wchat-0.2.0.tar.gz"
  sha256 "bdca6fff599fbeaa1b952de4fb82ce3476f0fa1dcbe5c806f364b1a70744894a"
  version "0.2.0"
  license "MIT"

  depends_on "python@3.12"

  def install
    libexec.install "wchat", "wchat_py", "input_helper.py", "render_helper.py", "requirements.txt"
    (libexec/"configs").install "configs/config.example"
    (libexec/"configs").install "configs/ctmodel.py"
    (libexec/"doc").install "README.md" if File.exist?("README.md")
    (libexec/"doc").install Dir["docs/*.md"] if Dir.exist?("docs")
    py = Formula["python@3.12"].opt_bin/"python3"
    (bin/"wchat").write <<~EOS
      #!/bin/bash
      exec "#{py}" "#{libexec}/wchat" "$@"
    EOS
    chmod 0755, bin/"wchat"
    (bin/"ctmodel").write <<~EOS
      #!/bin/bash
      exec "#{py}" "#{libexec}/configs/ctmodel.py" "$@"
    EOS
    chmod 0755, bin/"ctmodel"
  end

  test do
    assert_match "使用方法", shell_output("WCHAT_NO_BOOTSTRAP=1 #{bin}/wchat -h 2>&1")
  end
end
