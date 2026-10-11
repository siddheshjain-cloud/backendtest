# SPA Backend Development Instructions

These are persistent rules for Codex work in this repository.
Task-specific instructions take precedence when explicitly stated.

## Environment

Use the established SPA development environment.

Python:
C:\venvs\spa-m1\Scripts\python.exe

Canonical full backend test:
C:\venvs\spa-m1\Scripts\python.exe -m pytest -q

Do not rediscover, download, reinstall, or provision Python or another virtual
environment unless the established environment is demonstrably unusable.

Do not reinstall dependencies merely as exploration. Treat the validated
environment as stable unless requirements manifests changed or an actual
dependency failure proves otherwise.

## Development Method

Use TDD for implementation and bug fixes:

RED -> verify the intended failure -> minimal GREEN -> regression.

Stay strictly within the authorized task.
Do not add speculative functionality, unrelated refactors, or future architecture.

Inspect the repository state before editing.
Preserve unrelated user work.
Never reset, clean, discard, or overwrite unrelated changes.

## Safety Boundaries

Unless the task explicitly authorizes it, do not interact with:

- active databases
- Kite or broker systems
- Telegram
- live websockets or trading systems
- deployment or production environments
- secrets or credentials
- access-control or permission configuration
- main/master branches

Fail closed when a requested action crosses a consequential boundary.

## Verification

Use fresh verification before claiming work is complete.
Do not rely on previous test results as proof of the current change.

Run focused tests during development and the appropriate regression suite before
completion.

Commit and push only when the task explicitly requires it and verification is green.

## Operating Principle

Stable infrastructure is established once and reused.
Repeatedly check only state that can realistically drift or affect the current task.

Correct and safe before complete; complete before elegant.
