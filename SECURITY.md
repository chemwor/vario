# Security and privacy

Vario sits between you and AI agents that can edit files and run commands. This page explains, in plain
language, what Vario can do, what stays on your machine, what leaves it, and how the voice controls are built so
that a mumble can't approve anything.

## What runs on your machine

Everything Vario is made of:

- **The backend**: a Python process listening on **127.0.0.1:8787**, the loopback address. Nothing else on your
  network can connect to it. It serves the dashboard UI, stores its data in a local SQLite file
  (`~/.local/share/vario/data`), and has no login, because only programs on your own machine can reach it.
- **The hooks**: small shell scripts in `~/.claude/hooks` that Claude Code runs on session events. They post the
  event to the backend on 127.0.0.1 and, if it isn't running, speak through Bubo directly.
- **Bubo**: the text-to-speech daemon (Kokoro), listening on a Unix socket in `~/.claude/hooks`.
- **Whisper**: speech-to-text, run as a local process on audio recorded by the dashboard tab.

## What never leaves your machine

- **Microphone audio.** The browser records it, the backend transcribes it with Whisper on this machine, and
  the audio is deleted. The one exception is opt-in: `STT=webspeech` in `vario.conf` uses the browser's
  speech service, which sends audio to Google. The setting says so, and the work profile ignores it.
- **Speech output.** Kokoro synthesizes it locally. No cloud TTS is used or built in.
- **Your database**: reports, the decision log, the comms log and settings.
- **Telemetry.** There is none. Vario doesn't phone home, count usage or check for updates, and it turns off
  the telemetry of the tools it runs (Next.js, Hugging Face).

## What leaves your machine, and why

| What | Where | When |
|---|---|---|
| Short report text: a permission prompt, a finished turn's last message, an error, a dropped file's text (up to 4,000 characters) | Anthropic, through the Claude Agent SDK and **your own Claude Code login** | Each report is triaged once by a small model (Haiku) |
| The transcribed text of a spoken answer (never the audio) | Anthropic, the same way | Only when local rules can't classify it |
| A research question; for research aimed at a session, a short product summary (that project's README, CLAUDE.md and package description excerpts, and what the session was asked; never `.env` or source code) | Anthropic (and the web pages the model fetches) | Only when you start a scout or a research playbook. Scouts are off in the work profile. |
| Downloads | Hugging Face (voice models), GitHub (Vario releases), PyPI / npm (dependencies) | Install and first-run setup only, and only when you press the buttons |

Your sessions themselves talk to Anthropic as they always do; Vario doesn't change that. Vario reads their
transcripts locally to follow them, and it never uploads transcripts. The only session text that leaves through
Vario is the short report text above, sent to the provider those sessions already use.

In the **work profile** (`PROFILE=work`), triage uses only the Claude Code login: if `ANTHROPIC_API_KEY` or
`ANTHROPIC_AUTH_TOKEN` is set, triage falls back to local rules instead of using the key.

**Remote access:** there isn't any in v0.1. No Tailscale, no push service, no phone app. If remote access is
added later, it will be off by default, need an explicit setting and use a token you generate.

## What Vario can do to your sessions

The same things you can do from the keyboard, nothing more:

- approve or deny a pending permission request in a session Vario started (through the Agent SDK);
- type a message plus Enter into a terminal session running in tmux or cmux (it refuses while a permission
  dialog is on screen, because Enter would answer it);
- interrupt a session it started.

It never edits your files or runs commands itself. Sessions Vario can't reach (not in tmux or cmux) are labeled
monitor-only.

## The confidence gate, and why

Voice is the riskiest input Vario takes. A transcription error that turns "no, wait" into "approve" can run a
migration. So spoken input is gated by Whisper's own confidence score:

1. **Under 40%** (`MIN_DISPATCH_CONF`): the utterance is logged as *rejected: low confidence* and stops there.
   It isn't parsed as a command or sent to the classifier, so a model never gets the chance to guess at it.
2. **Approve, deny, yes, no, skip, retry, and anything sent to a session, under 70%** (`STRICT_CONF`): a
   confirm card appears and waits for an explicit "confirm" (or a click). Silence never sends it; it expires
   unsent after a minute.
3. **Everything else**: a two-second confirm card you can stop with "no", "cancel" or Esc.

The classifier is told to answer *unintelligible* for gibberish rather than pick the nearest command, and
that is treated as a rejection. Approving a report that isn't a decision is refused outright. Every utterance,
what it was heard as, its confidence and what happened is listed in the Comms pane, so you can audit it.

Why not just raise the bar? Short words like "yes" score low even when said clearly, so a single high threshold
makes voice useless, and a low one makes it dangerous. Tiering by consequence keeps cheap actions fast and
expensive ones deliberate.

## Hooks and your settings

The setup wizard shows the exact diff to `~/.claude/settings.json` and writes nothing until you tick a box.
If the file changed between showing and writing, the write is refused. A timestamped backup is made first.
`vario uninstall` removes exactly the entries setup added and leaves hooks you added yourself.

## Model integrity

The voice models are pinned to exact Hugging Face revisions, and every file is checked against a sha256 stored
in the repo (`backend/app/models.json`). A file that fails is deleted and downloaded again.
`vario doctor --deep` re-checks them. Release archives ship with a `.sha256` file, which `install.sh` verifies.

## Reporting a security issue

Please report it privately through **GitHub's private vulnerability reporting** (Security → Report a
vulnerability on the repository), not in a public issue. Include what an attacker could do, the steps to
reproduce and your Vario version (`vario version`). You'll get an acknowledgement within a week. This is a
personal project with no bounty, but reports are taken seriously and credited in the changelog if you like.
