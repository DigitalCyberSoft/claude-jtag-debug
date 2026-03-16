"""Tests for GDB/MI recursive descent parser."""

import pytest
from server.gdb.mi_parser import MIParser
from server.gdb.types import MIAsyncRecord, MIResultRecord, MIStreamRecord


@pytest.fixture
def parser():
    return MIParser()


class TestResultRecords:
    def test_done_no_results(self, parser):
        records = parser.feed("^done\n")
        assert len(records) == 1
        r = records[0]
        assert isinstance(r, MIResultRecord)
        assert r.result_class == "done"
        assert r.results == {}
        assert r.token is None

    def test_done_with_token(self, parser):
        records = parser.feed("42^done\n")
        assert len(records) == 1
        r = records[0]
        assert isinstance(r, MIResultRecord)
        assert r.token == 42
        assert r.result_class == "done"

    def test_error_with_message(self, parser):
        records = parser.feed('^error,msg="No symbol table loaded."\n')
        assert len(records) == 1
        r = records[0]
        assert isinstance(r, MIResultRecord)
        assert r.result_class == "error"
        assert r.results["msg"] == "No symbol table loaded."

    def test_done_with_simple_result(self, parser):
        records = parser.feed('^done,value="0x08001234"\n')
        assert len(records) == 1
        assert records[0].results["value"] == "0x08001234"

    def test_done_with_tuple_result(self, parser):
        records = parser.feed('^done,bkpt={number="1",type="breakpoint",addr="0x08001234"}\n')
        assert len(records) == 1
        bkpt = records[0].results["bkpt"]
        assert bkpt["number"] == "1"
        assert bkpt["type"] == "breakpoint"
        assert bkpt["addr"] == "0x08001234"

    def test_done_with_nested_tuples(self, parser):
        records = parser.feed(
            '^done,frame={level="0",addr="0x08001234",'
            'func="main",args=[{name="argc",value="1"}]}\n'
        )
        assert len(records) == 1
        frame = records[0].results["frame"]
        assert frame["func"] == "main"
        assert frame["args"] == [{"name": "argc", "value": "1"}]

    def test_running(self, parser):
        records = parser.feed("^running\n")
        assert len(records) == 1
        assert records[0].result_class == "running"

    def test_connected(self, parser):
        records = parser.feed("^connected\n")
        assert len(records) == 1
        assert records[0].result_class == "connected"

    def test_exit(self, parser):
        records = parser.feed("^exit\n")
        assert len(records) == 1
        assert records[0].result_class == "exit"


class TestAsyncRecords:
    def test_exec_stopped(self, parser):
        records = parser.feed(
            '*stopped,reason="breakpoint-hit",bkptno="1",'
            'frame={addr="0x08001234",func="main",file="main.c",line="10"}\n'
        )
        assert len(records) == 1
        r = records[0]
        assert isinstance(r, MIAsyncRecord)
        assert r.record_type == "exec"
        assert r.async_class == "stopped"
        assert r.results["reason"] == "breakpoint-hit"
        assert r.results["frame"]["func"] == "main"

    def test_exec_running(self, parser):
        records = parser.feed("*running,thread-id=\"all\"\n")
        assert len(records) == 1
        r = records[0]
        assert r.record_type == "exec"
        assert r.async_class == "running"

    def test_notify_thread_created(self, parser):
        records = parser.feed('=thread-created,id="1",group-id="i1"\n')
        assert len(records) == 1
        r = records[0]
        assert r.record_type == "notify"
        assert r.async_class == "thread-created"

    def test_status_download(self, parser):
        records = parser.feed('+download,{section=".text",section-size="1024"}\n')
        assert len(records) == 1
        r = records[0]
        assert r.record_type == "status"

    def test_exec_stopped_signal(self, parser):
        records = parser.feed(
            '*stopped,reason="signal-received",signal-name="SIGSEGV",'
            'signal-meaning="Segmentation fault",'
            'frame={addr="0x08001234",func="SPI1_IRQHandler",'
            'file="src/spi.c",line="42"}\n'
        )
        assert len(records) == 1
        r = records[0]
        assert r.results["signal-name"] == "SIGSEGV"
        assert r.results["frame"]["func"] == "SPI1_IRQHandler"


class TestStreamRecords:
    def test_console_output(self, parser):
        records = parser.feed('~"GNU gdb (GDB) 12.1\\n"\n')
        assert len(records) == 1
        r = records[0]
        assert isinstance(r, MIStreamRecord)
        assert r.stream_type == "console"
        assert r.content == "GNU gdb (GDB) 12.1\n"

    def test_target_output(self, parser):
        records = parser.feed('@"Hello from target\\n"\n')
        assert len(records) == 1
        assert records[0].stream_type == "target"
        assert records[0].content == "Hello from target\n"

    def test_log_output(self, parser):
        records = parser.feed('&"warning: No executable specified\\n"\n')
        assert len(records) == 1
        assert records[0].stream_type == "log"

    def test_escaped_quotes(self, parser):
        records = parser.feed('~"value is \\"hello\\"\\n"\n')
        assert len(records) == 1
        assert records[0].content == 'value is "hello"\n'

    def test_escaped_backslash(self, parser):
        records = parser.feed('~"path: C:\\\\Users\\\\test\\n"\n')
        assert len(records) == 1
        assert records[0].content == "path: C:\\Users\\test\n"


class TestListParsing:
    def test_empty_list(self, parser):
        records = parser.feed("^done,files=[]\n")
        assert records[0].results["files"] == []

    def test_value_list(self, parser):
        records = parser.feed('^done,register-names=["r0","r1","r2","r3"]\n')
        names = records[0].results["register-names"]
        assert names == ["r0", "r1", "r2", "r3"]

    def test_result_list(self, parser):
        records = parser.feed(
            "^done,register-values=["
            '{number="0",value="0x00000001"},'
            '{number="1",value="0x20001000"}'
            "]\n"
        )
        vals = records[0].results["register-values"]
        assert len(vals) == 2
        assert vals[0]["number"] == "0"
        assert vals[1]["value"] == "0x20001000"

    def test_memory_result(self, parser):
        records = parser.feed(
            '^done,memory=[{begin="0x20000000",offset="0x00000000",'
            'end="0x20000010",contents="deadbeef01020304"}]\n'
        )
        mem = records[0].results["memory"]
        assert len(mem) == 1
        assert mem[0]["contents"] == "deadbeef01020304"


class TestIncompleteAndCorruptInput:
    def test_incomplete_line_buffered(self, parser):
        records = parser.feed("^done,val")
        assert records == []
        records = parser.feed('ue="test"\n')
        assert len(records) == 1
        assert records[0].results["value"] == "test"

    def test_multiple_lines_at_once(self, parser):
        records = parser.feed('^done\n*running,thread-id="1"\n~"hello\\n"\n')
        assert len(records) == 3
        assert isinstance(records[0], MIResultRecord)
        assert isinstance(records[1], MIAsyncRecord)
        assert isinstance(records[2], MIStreamRecord)

    def test_gdb_prompt_ignored(self, parser):
        records = parser.feed("(gdb)\n")
        assert records == []

    def test_unclassified_output_skipped(self, parser):
        records = parser.feed("Hello from semihosting\n")
        assert records == []

    def test_empty_lines_skipped(self, parser):
        records = parser.feed("\n\n^done\n\n")
        assert len(records) == 1

    def test_target_stdout_interleaved(self, parser):
        records = parser.feed(
            "some random target output\n"
            "^done\n"
            "more target garbage\n"
            '*stopped,reason="breakpoint-hit"\n'
        )
        assert len(records) == 2
        assert isinstance(records[0], MIResultRecord)
        assert isinstance(records[1], MIAsyncRecord)

    def test_cr_lf_handling(self, parser):
        records = parser.feed("^done\r\n")
        assert len(records) == 1


class TestOctalEscapes:
    def test_octal_escape(self, parser):
        records = parser.feed('~"\\101\\102\\103"\n')
        assert len(records) == 1
        assert records[0].content == "ABC"

    def test_mixed_escapes(self, parser):
        records = parser.feed('~"tab\\there\\n\\101end"\n')
        assert len(records) == 1
        assert records[0].content == "tab\there\nAend"


class TestEdgeCases:
    def test_empty_tuple(self, parser):
        records = parser.feed("^done,bkpt={}\n")
        assert records[0].results["bkpt"] == {}

    def test_empty_string_value(self, parser):
        records = parser.feed('^done,value=""\n')
        assert records[0].results["value"] == ""

    def test_large_token(self, parser):
        records = parser.feed("999999^done\n")
        assert records[0].token == 999999

    def test_reset_clears_buffer(self, parser):
        parser.feed("^done,partial")
        parser.reset()
        records = parser.feed("^done\n")
        assert len(records) == 1
        assert records[0].results == {}

    def test_deeply_nested_structures(self, parser):
        records = parser.feed(
            '^done,a={b={c={d="deep"}}}\n'
        )
        assert records[0].results["a"]["b"]["c"]["d"] == "deep"
