"""
Pytest Global Configuration & Options (tests/conftest.py)
"""

import os
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--provider",
        action="store",
        default="fake",
        help="LLM provider for evaluation benchmark: fake | gemini | openai_compat",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: mark test as live LLM network test requiring RUN_LIVE_LLM=1")


def pytest_collection_modifyitems(config, items):
    if os.getenv("RUN_LIVE_LLM") != "1":
        skip_live = pytest.mark.skip(reason="Live LLM tests skipped unless RUN_LIVE_LLM=1 is set")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip_live)


@pytest.fixture
def eval_provider(request):
    return request.config.getoption("--provider")
