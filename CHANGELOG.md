# Changelog

All notable changes to Vario. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
versions follow [Semantic Versioning](https://semver.org/). The release workflow copies a version's section into
its GitHub release notes, so write entries for people who use Vario, not for people who build it.

## [Unreleased]

### Added
- Research playbooks: competition, market, features and end-user research aimed at a session's product.
- Test previews: test runs a session makes (pytest, vitest, jest, go test, cargo test, npm test and more) show
  on its row (✓ 27 passed / ✗ 1 failed / 28) and in a Tests view in its pane, with failing tests and output.

## [0.1.0] - 2026-11-01

First public release.

### Added
- **Decision inbox.** Every Claude Code session event (permission prompts, questions, errors, finished turns),
  every file dropped in `~/Vario/inbox` and every scout result becomes a report, triaged by a small Claude model
  into decision / research / fyi with an urgency. Decisions are ordered first; identical errors from one session
  collapse into one report with a count, and the third repeat asks "Retry, or kill it?".
- **Voice, local-first.** Spoken alerts through Bubo, a warm Kokoro TTS daemon running on your Mac. Voice input
  transcribed on your Mac by Whisper. Briefing mode reads the queue aloud and takes spoken answers.
- **Confidence gate.** Utterances under 40% confidence are logged and never acted on; approvals and anything sent
  to a session need 70% or an explicit "confirm" / click. Silence never sends a low-confidence action.
- **Session board.** Dashboard-owned sessions (Claude Agent SDK) and terminal sessions side by side, ordered
  error > blocked > working > done, with context meters, outcome badges (done / done-check / failed), stuck
  detection, evidence panels (the diff or command a decision is about) and a Retry action.
- **`vario` command.** `vario` (start), `stop`, `status`, `doctor`, `demo`, `mute` / `unmute`, `voice`,
  `autostart`, `logs`, `uninstall`.
- **Setup wizard** on first launch: dependency check, voice model download with sha256 verification, Claude Code
  hook install behind a consent screen showing the exact `settings.json` diff, sound and microphone tests, theme
  and voice. Every step can be skipped; skipped steps show as warnings in Health.
- **Demo mode** (`vario demo`): a separate instance with made-up sessions and reports, marked DEMO.
- **Install** with `curl … | bash` or Homebrew; `vario uninstall` removes exactly the hook entries it added.
- macOS (Apple Silicon) and Linux. Linux uses espeak-ng for speech and notify-send for notifications; the Kokoro
  voice is Apple Silicon only in this release.

[Unreleased]: https://github.com/chemwor/vario/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/chemwor/vario/releases/tag/v0.1.0
