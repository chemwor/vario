# Generated from packaging/homebrew/vario.rb for v0.1.0 (sha256 of the published release archive).
class Vario < Formula
  desc "Decision inbox and local-first voice briefing for Claude Code sessions"
  homepage "https://github.com/chemwor/vario"
  url "https://github.com/chemwor/vario/releases/download/v0.1.0/vario-0.1.0.tar.gz"
  sha256 "978f5e954a0663081cd8bb5221952acf5ff45b3247ea10d7c725e066712057eb"
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
