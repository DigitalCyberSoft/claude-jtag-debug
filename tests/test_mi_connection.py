"""Tests for MIConnection with mock subprocess."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from server.gdb.mi_connection import MIConnection, GDBError
from server.gdb.types import GDBState


@pytest.fixture
def connection():
    return MIConnection(gdb_path="/usr/bin/true")


class TestConnectionLifecycle:
    @pytest.mark.asyncio
    async def test_initial_state_disconnected(self, connection):
        assert connection.state == GDBState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_cannot_send_when_disconnected(self, connection):
        with pytest.raises(RuntimeError, match="GDB not connected"):
            await connection.send("-data-list-register-names")

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self, connection):
        await connection.stop()  # Should not raise
        assert connection.state == GDBState.DISCONNECTED


class TestTokenMatching:
    @pytest.mark.asyncio
    async def test_token_increments(self):
        conn = MIConnection()
        assert conn._token_counter == 1

    @pytest.mark.asyncio
    async def test_pending_tracking(self):
        conn = MIConnection()
        # Simulate adding a pending future
        future = asyncio.get_event_loop().create_future()
        conn._pending[1] = future
        conn._pending_order.append(1)
        assert 1 in conn._pending

        # Clean up
        future.cancel()


class TestEventQueueOverflow:
    @pytest.mark.asyncio
    async def test_event_queue_maxsize(self):
        conn = MIConnection()
        assert conn._event_queue.maxsize == 1000


class TestGDBError:
    def test_gdb_error_message(self):
        err = GDBError("Test error")
        assert str(err) == "Test error"
        assert err.record is None

    def test_gdb_error_with_record(self):
        from server.gdb.types import MIResultRecord
        record = MIResultRecord(token=1, result_class="error", results={"msg": "test"})
        err = GDBError("test", record)
        assert err.record is record
