class Wchat < Formula
  desc "Terminal AI assistant (Python, first-run venv bootstrap)"
  homepage "https://example.invalid/wchat"
  url "file:///Users/jwan/bin/dist/wchat-0.1.0.tar.gz"
  sha256 "71dc1d9872470edeea4ab5e1ef580ad76be7cef6ceba0b93c9b15d7d678134b6"
  version "0.1.0"

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
