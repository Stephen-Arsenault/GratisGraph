"""tests/golden/test_golden.py — Golden corpus drift guard.

Rebuilds every corpus case in memory and byte-compares against the committed
outputs. A failure here means the translator's output changed: either it's an
accidental regression (fix the code) or a deliberate format change (update
docs/GTS_FORMAT.md FIRST, then regenerate with generate.py and say so in the
commit message). Never regenerate just to make this pass.
"""
import hashlib
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from generate import CASES, build_all   # noqa: E402

with open(os.path.join(HERE, 'manifest.json')) as f:
    MANIFEST = json.load(f)

BUILT = build_all()


def test_manifest_covers_all_cases():
    assert set(MANIFEST['cases']) == set(CASES)


@pytest.mark.parametrize('name', sorted(CASES))
def test_case_matches_committed_output(name):
    with open(os.path.join(HERE, MANIFEST['cases'][name]['output']), 'rb') as f:
        committed = f.read()
    assert BUILT[name] == committed, (
        f'{name}: translator output drifted from committed golden file')


@pytest.mark.parametrize('name', sorted(CASES))
def test_case_matches_manifest_hash(name):
    assert hashlib.sha256(BUILT[name]).hexdigest() == \
        MANIFEST['cases'][name]['sha256']


def test_hw_proven_case_is_the_proven_bytes():
    """'two_lines_hw_proven' must equal build_gts of the exact stroke list
    that ran correctly on hardware 2026-06-09 — anchoring the whole corpus
    to physical reality, not just to itself."""
    from gts_api import build_gts
    proven = build_gts([(1000, 2000, [(1000, 0)]), (3000, 4000, [(1000, 0)])])
    assert BUILT['two_lines_hw_proven'] == proven
