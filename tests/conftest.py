"""Explicit opt-in for tests that require the PostgreSQL Compose service."""

import os
import subprocess
from pathlib import Path

import pytest

from solar_platform.settings import DatabaseSettings, load_database_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_PASSWORD_FILE = PROJECT_ROOT / ".secrets/postgres-test-password"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-postgres",
        action="store_true",
        default=False,
        help="Run tests marked postgres against the local Compose test service.",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("postgres") and not item.config.getoption(
        "--run-postgres"
    ):
        pytest.skip("PostgreSQL integration requires --run-postgres")


@pytest.fixture(scope="session")
def test_database_settings() -> DatabaseSettings:
    """Fail closed unless opt-in tests target the running Compose test service."""
    settings = load_database_settings()
    if (
        settings.host != "127.0.0.1"
        or settings.name != "solar_platform_test"
        or settings.user != "postgres"
        or Path(os.environ["SOLAR_PLATFORM_DB_PASSWORD_FILE"]).resolve()
        != TEST_PASSWORD_FILE.resolve()
    ):
        pytest.fail("PostgreSQL integration must target the Compose test service")

    result = subprocess.run(
        ["docker", "compose", "--profile", "test", "port", "postgres-test", "5432"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or result.stdout.strip() != (
        f"127.0.0.1:{settings.port}"
    ):
        pytest.fail("Compose postgres-test is unavailable at the configured port")
    return settings
