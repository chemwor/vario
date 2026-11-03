# Template for the Homebrew tap (chemwor/homebrew-vario, Formula/vario.rb). The release workflow fills in
# __REPO__, __VERSION__ and __SHA256__ and pushes it; don't edit the tap's copy by hand.
#   brew install chemwor/vario/vario
class Vario < Formula
  desc "Decision inbox and local-first voice briefing for Claude Code sessions"
  homepage "https://github.com/__REPO__"
  url "https://github.com/__REPO__/releases/download/v__VERSION__/vario-__VERSION__.tar.gz"
  sha256 "__SHA256__"
  license "MIT"

  depends_on "jq"
  depends_on "uv"

  def install
    libexec.install Dir["*"]
    # the real script finds the app folder from its own location, so call it by its full path
    (bin/"vario").write <<~SH
      #!/bin/bash
      exec "#{libexec}/bin/vario" "$@"
    SH
  end

  def caveats
    <<~EOS
      Start Vario with `vario`. The first launch opens a setup wizard in your browser: it downloads the
      voice models and installs the Claude Code hooks after showing you the exact settings.json change.
      Your data lives in ~/.local/share/vario. `vario uninstall` removes the hooks before `brew uninstall vario`.
    EOS
  end

  test do
    system "bash", "-n", libexec/"bin/vario"
    assert_path_exists libexec/"web/out/index.html"
    assert_equal version.to_s, (libexec/"VERSION").read.strip
  end
end
