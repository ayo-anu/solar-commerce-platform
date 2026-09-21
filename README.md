# Solar Energy Digital Platform

This repository contains the Python backend foundation for a production-grade
solar-energy commerce platform. It is intended to grow into a modular monolith
supporting ecommerce and later corporate project workflows.

## Current state

- Foundation status: B2 application architecture, configuration, and HTTP
  foundation implemented, accepted, and approved for phase exit
- Application implementation: FastAPI application factory with a reviewed,
  database-free operational HTTP surface
- Local infrastructure: separate Docker Compose PostgreSQL development and test
  services; application database wiring remains deferred
- Technology direction: Python/FastAPI/PostgreSQL modular monolith

The current B2 foundation includes:

- a fresh FastAPI application from `create_app(settings)`;
- immutable typed configuration for development, test, and production;
- disclosure-safe RFC 9457 Problem Details and validation-error translation;
- canonical request correlation through `X-Request-ID`;
- database-independent `GET /health/live` process liveness;
- OpenAPI at `/openapi.json` and Swagger UI at `/docs`;
- an explicit, currently resource-free application lifespan boundary; and
- structured, redaction-aware terminal request logging as JSON Lines to stderr.

It does not yet include application persistence, migrations, database
dependency readiness, authentication, business APIs, a server/deployment
entrypoint, or other later-phase behavior. Development continues in small,
reviewed changes; jurisdiction-sensitive behavior and live-business
integrations are added only when their requirements and validation boundaries
are established.

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

### Run the installed-package smoke test

Synchronize first, then run:

```bash
uv run --locked --no-sync python -P -m unittest discover \
  -s tests \
  -p "test_*.py" \
  -v
```

`--no-sync` prevents the test invocation itself from implicitly modifying or
reconciling the environment. This focused integration smoke test covers
installed-distribution discovery and package importability. Application and
HTTP behavior are covered by the pytest suites below.

### Check and format Python code

After synchronizing the environment, run the Python lint and formatting checks:

```bash
uv run --locked --no-sync ruff check src tests scripts
uv run --locked --no-sync ruff format --check src tests scripts
```

To apply intentional, reviewable fixes and formatting changes, run:

```bash
uv run --locked --no-sync ruff check --fix src tests scripts
uv run --locked --no-sync ruff format src tests scripts
```

Review the resulting diff and rerun both check commands before considering the
change valid. Ruff fixes are not a substitute for code review.

### Type-check Python code

After synchronizing the environment, run strict type checking across source,
tests, and local scripts:

```bash
uv run --locked --no-sync mypy
```

The checked paths and strictness policy are defined in `pyproject.toml`.

### Run pytest suites

After `uv sync --locked`, run the complete suite or select a marker:

```bash
uv run --locked --no-sync pytest
uv run --locked --no-sync pytest -m integration
uv run --locked --no-sync pytest -m "integration and not migration"
uv run --locked --no-sync pytest -m unit
uv run --locked --no-sync pytest -m migration
uv run --locked --no-sync pytest -m "not slow"
uv run --locked --no-sync pytest -m slow
```

`unit` and `integration` normally classify isolation level; migration tests
normally also carry `integration`, while `slow` may overlap either category.
The current unit suite covers settings, application composition, correlation,
Problem Details, request logging, and architecture boundaries. Integration
tests cover installed-package behavior and the real ASGI stack for liveness,
lifecycle, OpenAPI, error, correlation, and logging behavior. The `unit` and
`integration` selections are populated. Selections with no matching tests
return pytest exit status 5; this is currently expected for `migration` and
`slow`.

### Run the local quality gate

Synchronize the environment first, then run the four existing checks together:

```bash
uv sync --locked
uv run --locked --no-sync python -P scripts/quality.py
```

This runs Ruff lint, Ruff format checking, strict mypy, and the complete pytest
suite in that order. It stops at the first failure; `--no-sync` prevents the
quality invocation itself from implicitly reconciling the environment.

## Local PostgreSQL

The Compose configuration provides independent PostgreSQL 18.6 services for
development and tests. Development data uses a persistent named volume. Test
data uses a disposable in-memory filesystem and is lost whenever the test
container is stopped or replaced. Neither service is connected to the Python
application yet.

The supported interface is the current Compose Specification through the
`docker compose` CLI. It must support profiles, secrets, named volumes, tmpfs,
health checks, and `docker compose up --wait`. Legacy `docker-compose` v1 is not
supported.

Verify Docker before setup:

```bash
docker info
docker compose version
```

### Create local database secrets

From a fresh checkout, run the following once. It creates separate random
passwords without displaying them, refuses to replace existing secret files,
and applies restrictive host permissions:

```bash
python3 - <<'PY'
import os
import secrets
from pathlib import Path

secret_dir = Path(".secrets")
targets = (
    secret_dir / "postgres-dev-password",
    secret_dir / "postgres-test-password",
)

existing = [str(path) for path in targets if path.exists()]
if existing:
    raise SystemExit(
        "Refusing to overwrite existing secret files: " + ", ".join(existing)
    )

secret_dir.mkdir(mode=0o700, exist_ok=True)
secret_dir.chmod(0o700)

for path in targets:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(secrets.token_urlsafe(48) + "\n")
    path.chmod(0o600)
PY
```

Confirm that Git ignores the files and does not track or report them:

```bash
git check-ignore -v \
  .secrets/postgres-dev-password \
  .secrets/postgres-test-password
git ls-files -- .secrets
git status --short --untracked-files=all -- .secrets
```

The last two commands must produce no output. Passwords are exposed to each
container only through its own Compose secret and `POSTGRES_PASSWORD_FILE`.
Changing the development secret after the database is initialized does not
rotate the stored PostgreSQL password; rotate it with SQL or deliberately reset
the development volume.

### Start, inspect, and stop PostgreSQL

Start the persistent development database:

```bash
docker compose up -d --wait postgres-dev
```

Start the isolated test database when needed:

```bash
docker compose --profile test up -d --wait postgres-test
```

The default loopback ports are `5432` for development and `5433` for tests.
Override them without changing the Compose file when a port is occupied:

```bash
SOLAR_PLATFORM_POSTGRES_DEV_PORT=15432 \
  docker compose up -d --wait postgres-dev
SOLAR_PLATFORM_POSTGRES_TEST_PORT=15433 \
  docker compose --profile test up -d --wait postgres-test
```

Inspect service status and query each server with the client inside its own
container:

```bash
docker compose --profile test ps
docker compose exec postgres-dev sh -lc \
  'psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="SHOW server_version;"'
docker compose --profile test exec postgres-test sh -lc \
  'psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="SHOW server_version;"'
```

Stop and remove the containers and network while preserving development data:

```bash
docker compose down
```

Reset the disposable test database deterministically:

```bash
docker compose --profile test rm --stop --force postgres-test
docker compose --profile test up -d --wait postgres-test
```

To remove the persistent development database too, use the following explicit
destructive reset. It permanently deletes the Compose project's named volume:

```bash
docker compose down --volumes
```

Test tmpfs is initially limited to 256 MiB. This is a local operational default
and may be adjusted if measured integration workloads require more capacity.

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
  artifacts are ignored. pytest, type-checker, frontend, and coverage exclusions
  remain deferred until their tools are introduced.
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
