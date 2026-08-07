"""Shared fixtures for lex tests."""

from pathlib import Path

import pytest
from lxml.etree import _ElementTree

from lex_akn.parse import parse_file

DATA_DIR = Path(__file__).parent.parent.parent / "data"
BV_PATH = DATA_DIR / "ch" / "101" / "de.xml"
KV_PATH = DATA_DIR / "vs" / "101.1" / "de.xml"


@pytest.fixture(scope="session")
def bv_tree() -> _ElementTree:
    """Parse the Bundesverfassung AKN XML once per test session."""
    if not BV_PATH.exists():
        pytest.skip(f"BV test data not found: {BV_PATH}")
    return parse_file(BV_PATH)


@pytest.fixture(scope="session")
def kv_tree() -> _ElementTree:
    """Parse the Kantonsverfassung VS 101.1 AKN XML once per test session."""
    if not KV_PATH.exists():
        pytest.skip(f"KV test data not found: {KV_PATH}")
    return parse_file(KV_PATH)
