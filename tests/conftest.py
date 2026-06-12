"""tests/conftest.py — pytest configuration."""
import pytest

def pytest_addoption(parser):
    parser.addoption('--hardware', action='store_true', default=False,
                     help='Run hardware tests (requires M20 connected)')

def pytest_configure(config):
    config.addinivalue_line('markers',
        'hardware: test requires M20 connected over USB (run with --hardware)')

def pytest_collection_modifyitems(config, items):
    if not config.getoption('--hardware'):
        skip = pytest.mark.skip(reason='requires --hardware flag and USB connection')
        for item in items:
            if 'hardware' in item.keywords:
                item.add_marker(skip)
