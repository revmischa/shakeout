from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="Rewrite tests/cases/**/*.golden from the current analysis output.",
    )


@pytest.fixture(scope="session")
def update_goldens(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-goldens"))
