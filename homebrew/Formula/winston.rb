# Homebrew formula for W.I.N.S.T.O.N.
# Install: brew install serhatbilge/tap/winston
# Or:      brew tap serhatbilge/tap && brew install winston

class Winston < Formula
  desc "AI assistant — like Jarvis, but open-source. Runs locally with full privacy"
  homepage "https://github.com/serhatbilge/W.I.N.S.T.O.N..S"
  url "https://github.com/serhatbilge/W.I.N.S.T.O.N..S/archive/refs/tags/v1.0.0.tar.gz"
  sha256 "PLACEHOLDER_SHA256"
  license "AGPL-3.0"
  head "https://github.com/serhatbilge/W.I.N.S.T.O.N..S.git", branch: "main"

  depends_on "python@3.12"
  depends_on "portaudio"
  depends_on "ffmpeg"

  # Ollama is recommended but not strictly required (cloud providers work too)
  def install
    # Create a dedicated virtualenv inside the Cellar
    venv = virtualenv_create(libexec, "python3.12")

    # Install all Python dependencies
    system libexec/"bin/pip", "install", "-r", "requirements.txt"

    # Install winston itself (editable-style so config/ is accessible)
    system libexec/"bin/pip", "install", "."

    # Copy config templates
    (etc/"winston").install "config/settings.yaml" => "settings.yaml.default"
    (etc/"winston").install ".env.example" if File.exist?(".env.example")

    # Create the wrapper script that activates the venv and runs winston
    (bin/"winston").write <<~EOS
      #!/bin/bash
      export WINSTON_CONFIG_DIR="#{etc}/winston"
      exec "#{libexec}/bin/python" -m winston.main "$@"
    EOS
  end

  def post_install
    # Create user data directory
    (var/"winston").mkpath

    # Remind about Ollama
    ohai "Winston is installed! 🎩"
    ohai ""
    ohai "Quick start:"
    ohai "  1. Install Ollama (if not already):  brew install ollama"
    ohai "  2. Start Ollama:                     ollama serve"
    ohai "  3. Pull a model:                     ollama pull qwen2.5:7b"
    ohai "  4. Run Winston:                      winston"
    ohai "  5. Setup wizard:                     winston --setup"
    ohai ""
    ohai "Or use with cloud LLMs (no Ollama needed):"
    ohai "  OPENAI_API_KEY=sk-... winston"
  end

  def caveats
    <<~EOS
      Winston needs a local LLM (Ollama) or cloud API key to work.

      Install & start Ollama:
        brew install ollama
        ollama serve
        ollama pull qwen2.5:7b

      Then just run:
        winston

      First-time setup wizard:
        winston --setup

      Server mode (Web UI + Telegram):
        winston --mode server
    EOS
  end

  test do
    assert_match "W.I.N.S.T.O.N.", shell_output("#{bin}/winston --help 2>&1", 0)
  end
end
