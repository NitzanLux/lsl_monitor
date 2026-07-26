# Repository Guidelines

## Project Structure & Module Organization

This repository is currently an empty scaffold. Keep the root limited to project-wide files such as `README.md`, dependency manifests, and configuration. As implementation is added, use a predictable layout:

- `src/lsl_monitor/` for application code
- `tests/` for automated tests mirroring the source tree
- `assets/` for static fixtures, sample data, or images
- `scripts/` for small development and maintenance utilities

Group code by responsibility rather than placing unrelated helpers in a single module. Do not commit generated output, virtual environments, caches, or local recordings.

## Build, Test, and Development Commands

No build system or test runner is configured yet. Add exact, copy-pasteable commands to `README.md` and this guide when tooling is introduced. Prefer commands runnable from the repository root and expose common operations through one standard interface (for example, `pyproject.toml`, `package.json`, or a `Makefile`).

Before submitting changes, run every configured formatter, linter, and test command. Avoid documenting commands that depend on uncommitted local setup.

## Coding Style & Naming Conventions

Follow the formatter and linter configuration committed with the project; do not override it locally. Use four-space indentation for Python and two spaces for JSON, YAML, and Markdown lists. Choose descriptive names: `snake_case` for Python modules and functions, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Keep modules focused and public interfaces documented.

## Testing Guidelines

Place tests under `tests/` and name them after the behavior or module they cover (for example, `tests/test_stream_monitor.py`). Tests should be deterministic, independent of live LSL streams by default, and use fixtures or mocks for external devices and timing-sensitive data. Every bug fix should include a regression test. Document any hardware or integration test prerequisites separately.

## Commit & Pull Request Guidelines

There is no Git history from which to infer an existing convention. Use short, imperative commit subjects such as `Add stream timeout handling`, and keep each commit focused. Pull requests should explain the problem, summarize the solution, list verification performed, and link relevant issues. Include screenshots or sample output for user-visible changes, and call out new dependencies, configuration, or hardware requirements.

## Security & Configuration

Never commit credentials, participant data, machine-specific paths, or `.env` files. Provide sanitized examples such as `.env.example`, and keep sensitive defaults out of source control.
