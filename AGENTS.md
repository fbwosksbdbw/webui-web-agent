# Repository Guidance

This repository contains a Python CLI/workflow agent for HUI Playwright test generation.

## Setup

Use Python 3.10 or newer.

```bash
python -m pip install -e ".[dev]"
```

If a task touches Stagehand exploration assets, also install the Node dependencies:

```bash
cd knowledge/scripts/stagehand_explorer
npm install
```

## Verification

Run focused tests first, then the full suite when practical:

```bash
pytest
```

The pytest cache is configured under `_local/pytest_cache`. Do not commit runtime outputs.

## Sensitive Files

`private/resume.pdf` is a private resume artifact. Treat it as confidential user data:

- Do not quote or summarize personal details unless the user explicitly asks.
- Do not copy it into public docs, examples, logs, screenshots, or generated fixtures.
- Do not upload it outside the private repository or Codex task context.
- If resume content is needed, read only the minimum required for the requested task.

Keep secrets out of the repository. Use Codex environment secrets or local `.env` files for API keys and credentials.

## Project Notes

- Prefer small, focused changes that preserve the existing CLI and workflow structure.
- Keep generated runs, logs, databases, browser state, and temporary files out of git.
- When changing CLI behavior, update or add tests under `tests/`.
