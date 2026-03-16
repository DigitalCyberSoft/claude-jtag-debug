"""Tests for DebugSession state management."""

import pytest

from server.gdb.session import DebugSession
from server.gdb.types import GDBState


class TestSessionState:
    def test_initial_state(self):
        session = DebugSession("test-001")
        assert session.session_id == "test-001"
        assert session.state == GDBState.DISCONNECTED

    def test_serialize_disconnected(self):
        session = DebugSession("test-002")
        data = session.serialize()
        assert data["session_id"] == "test-002"
        assert data["state"] == "disconnected"
        assert data["breakpoints"] == []

    def test_target_info_empty(self):
        session = DebugSession("test-003")
        assert session.target_info == {}

    def test_breakpoints_empty(self):
        session = DebugSession("test-004")
        assert session.breakpoints == {}

    def test_last_stop_empty(self):
        session = DebugSession("test-005")
        assert session.last_stop == {}

    def test_require_state_raises(self):
        session = DebugSession("test-006")
        with pytest.raises(RuntimeError, match="requires state stopped"):
            session._require_state(GDBState.STOPPED)
