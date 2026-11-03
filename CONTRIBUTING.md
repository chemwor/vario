# Contributing

Thanks for looking. Please read this first; it sets expectations.

## The maintenance contract

- **Vario is an opinionated personal tool.** It's built for one way of working (many Claude Code sessions,
  decisions over dashboards, voice that runs locally) and it's shared because it might suit you too. It isn't
  trying to be everything to everyone.
- **Pull requests are welcome.** Bug fixes, Linux and accessibility improvements, clearer docs and tests are
  the most likely to be merged. For anything bigger than a bug fix, open an issue first so we can agree on the
  shape before you spend time on it.
- **Issues are triaged weekly.** Expect a first response within about a week. Bugs with `vario doctor` output
  and steps to reproduce get fixed fastest.
- **No roadmap requests.** Feature requests without a PR will usually be closed with thanks. That isn't
  rudeness, just honesty about the time available. If you want something, the best way to get it is to build it,
  ideally after a quick issue to check it fits.
- **Security problems** go through private reporting (see [SECURITY.md](SECURITY.md)), never a public issue.

## Working on it

```bash
make setup      # backend venv (uv, Python 3.12) and web dependencies (npm)
make dev        # API on :8787 with auto-reload, UI dev server on :3000
make check      # ruff, eslint, tsc, shell syntax
make test       # backend tests (pytest); they never touch your real settings, hooks or data
```

- Backend: FastAPI in `backend/app/`. One module per concern; [docs/reference.md](docs/reference.md) maps them.
- UI: Next.js (static export) in `web/`. `make build` writes `web/out`, which the backend serves.
- Hooks and the voice daemon: `voice/hooks/`.
- macOS/Linux differences go in `backend/app/host.py` and nowhere else.

## Ground rules for changes

- **Fail safe on voice.** Anything that makes a spoken utterance more likely to act needs a test showing the
  confidence gate still holds (`backend/tests/test_comms.py`).
- **Never write to a user's `settings.json` without showing the diff first.** The wizard and uninstall depend
  on that.
- **No telemetry, no new network calls** without a line in SECURITY.md's "what leaves your machine" table
  and a setting to turn them off.
- **Keep it local.** New features should work without an internet connection, apart from the Claude calls
  Vario already makes.
- Add a line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) for anything a user would notice.

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
