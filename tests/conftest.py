"""Explicit opt-in for tests that require the PostgreSQL Compose service."""

import pytest


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
