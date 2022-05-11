import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--perf",
        action="store_true",
        default=False,
        help="run performance tests",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--perf"):
        # We want to run the performance tests
        return
    skip_perf = pytest.mark.skip(reason="need --perf option to run")
    for item in items:
        if "perftest" in item.keywords:
            item.add_marker(skip_perf)
