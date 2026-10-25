"""
Shared pytest fixtures.

Using pytest fixtures (not module-level globals) means each test gets a fresh
OperationLog instance, preventing cross-test contamination.
"""

import pytest

from promptcut.models.operation_log import OperationLog
from tests.fixtures.single_speaker import make_single_speaker
from tests.fixtures.two_speaker import make_two_speaker


@pytest.fixture
def transcript_s1():
    """Single-speaker fixture transcript (~3 min)."""
    return make_single_speaker()


@pytest.fixture
def transcript_2spk():
    """Two-speaker interview fixture transcript (~90 s)."""
    return make_two_speaker()


@pytest.fixture
def empty_log():
    """Fresh empty OperationLog."""
    return OperationLog()
