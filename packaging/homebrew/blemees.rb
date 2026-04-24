class Blemees < Formula
  include Language::Python::Virtualenv

  desc "Headless Claude Code daemon exposing `claude -p` over a Unix socket"
  homepage "https://github.com/juanheyns/agent-daemon"
  # Pin a released tag. The SHA256 below is a placeholder; regenerate on each
  # release with `brew fetch --force blemees` or `shasum -a 256 <tarball>`.
  url "https://github.com/juanheyns/agent-daemon/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "REPLACE_ME_ON_RELEASE"
  license "MIT"
  head "https://github.com/juanheyns/agent-daemon.git", branch: "main"

  # Runtime: stdlib-only; we just need a working Python.
  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  # Default service definition so `brew services start blemees` works.
  service do
    run [opt_bin/"blemeesd"]
    keep_alive true
    log_path   var/"log/blemees/blemeesd.log"
    error_log_path var/"log/blemees/blemeesd.err.log"
  end

  test do
    # Smoke: --version exits 0 and prints the installed version.
    assert_match "blemeesd #{version}", shell_output("#{bin}/blemeesd --version")
  end
end
