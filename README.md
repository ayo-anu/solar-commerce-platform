# Solar Energy Digital Platform

This repository contains the Python backend foundation for a production-grade
solar-energy commerce platform. It is intended to grow into a modular monolith
supporting ecommerce and later corporate project workflows.

## Current state

- Foundation status: reproducible Python package skeleton complete
- Application implementation: importable package scaffold only; behavior not started
- Technology direction: Python/FastAPI/PostgreSQL modular monolith

Development proceeds in small, reviewed changes. Application behavior,
jurisdiction-sensitive behavior, and live-business integrations will be added
only when their requirements and validation boundaries are established.

## Backend development workflow

Run commands from the repository root. The project supports CPython
`>=3.12,<3.13`; CPython 3.12.3 is the currently verified local interpreter, and
Python 3.13+ support is not currently claimed. uv 0.12.1 is the currently
verified project tooling baseline and minimum. Tool-version changes remain
deliberate as the project evolves, particularly when they affect lock behavior
or project semantics.

Inspect the active tools with:

```bash
python3 --version
uv --version
```

### First-time setup from an existing project checkout

```bash
uv sync --locked
```

This creates or reconciles the project environment and installs the project from
the reviewed dependency state. `--locked` refuses to proceed when `uv.lock` is
inconsistent with project metadata instead of silently updating the lock. The
workflow should also be verified from a fresh clone once a remote checkout is
available.

### Synchronize an existing environment

Use the same command to reconcile an existing project environment with the
approved locked dependency state:

```bash
uv sync --locked
```

### Run the current smoke test

Synchronize first, then run:

```bash
uv run --locked --no-sync python -P -m unittest discover \
  -s tests \
  -p "test_*.py" \
  -v
```

`--no-sync` prevents the test invocation itself from implicitly modifying or
reconciling the environment. The current test covers installed-distribution
discovery and package importability only; it is not application or HTTP testing.

### Dependency-addition policy

Add an authorized runtime/application dependency with:

```bash
uv add <package>
```

Add an authorized development, test, or quality dependency to the PEP 735
development group with:

```bash
uv add --dev <package>
```

Build-system requirements are separate and belong in project metadata:

```toml
[build-system]
requires = [...]
```

Every dependency addition requires a justified project need. Runtime packages
belong in project dependencies; development/test/quality tools belong in the
development group; and build requirements are governed separately. Do not edit
`uv.lock` manually. Review `pyproject.toml` and `uv.lock` together, avoid
unrelated dependency upgrades without justification, and run locked
synchronization plus applicable validation after dependency changes. Additional
dependency groups require a demonstrated use case.

## Git policy

The initial repository foundation consists of PEP 621/build metadata, the
validated dependency lock, the importable package marker, the package smoke
test, this README, and the ignore policy. Git metadata is never part of a
commit. Environment-managed `.agents/` and `.codex/` directories and the local
`.venv/` are ignored.

The repository policy is:

- `main` is the primary branch and should contain only reviewed, coherent work.
- Short-lived `task/<task-id>-<slug>` branches are preferred for code, schema,
  risky, or multi-step changes. Direct `main` work is acceptable for a very small
  documentation/status task when deliberately authorized. No long-lived
  `develop` branch is required for this single-developer project.
- One task maps to one reviewable change set and normally one cohesive commit;
  multiple commits are acceptable when each is meaningful. Unrelated changes,
  opportunistic refactors, and multiple task scopes must not be mixed.
- Generated outputs are committed only when they are required reproducibility
  artifacts. The generated and validated `uv.lock` is required, must be reviewed
  with `pyproject.toml`, and is not ignored.
- Secrets, credentials, private keys, real customer data, and populated local
  environment files are never committed. Sanitized example configuration may be
  committed only when its owning task authorizes it.
- Local agent/editor state, OS noise, logs, temporary/cache data, local
  environment files, secret-key formats, `.venv/`, and generated Python/build
  artifacts are ignored. pytest, type-checker, linter, frontend, and coverage
  exclusions remain deferred until their tools are introduced.
- Automated agents must not stage, create branches, or commit merely because a
  task is complete. A Git commit always requires separate explicit user
  authorization.

## Working principles

- Preserve distinct B2C transactional and B2B/corporate acquisition journeys.
- Prefer the simplest production-suitable design that leaves credible evolution
  paths.
- Treat performance, accessibility, SEO, measurement, privacy, and security as
  requirements from the start.
- Keep orders, leads, quotations, and opportunities in systems of record;
  WhatsApp is a channel, not the database.
- Measure business outcomes as well as technical health.
- Do not advance multiple major phases silently.
- Build the backend first as a production-grade portfolio release, then replace
  provisional assumptions with approved solar-business inputs before live use.
