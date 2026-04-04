"""Tests for the log_context module (contextvar-based vorgangs_id logging)."""

import logging

from bawue.log_context import VorgangsnummerFilter, reset_vorgangs_id, set_vorgangs_id


def _make_record(message: str = "test") -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )


class TestVorgangsnummerFilter:
    def test_sets_vorgangs_id_when_context_active(self):
        token = set_vorgangs_id("V-12345")
        try:
            record = _make_record()
            VorgangsnummerFilter().filter(record)
            assert record.vorgangs_id == "V-12345"
        finally:
            reset_vorgangs_id(token)

    def test_sets_none_when_no_context(self):
        record = _make_record()
        VorgangsnummerFilter().filter(record)
        assert record.vorgangs_id is None

    def test_always_returns_true(self):
        record = _make_record()
        assert VorgangsnummerFilter().filter(record) is True


class TestSetResetVorgangsId:
    def test_set_and_reset_restores_default(self):
        token = set_vorgangs_id("V-99999")
        reset_vorgangs_id(token)
        record = _make_record()
        VorgangsnummerFilter().filter(record)
        assert record.vorgangs_id is None

    def test_nested_contexts(self):
        outer = set_vorgangs_id("V-outer")
        inner = set_vorgangs_id("V-inner")
        record = _make_record()
        VorgangsnummerFilter().filter(record)
        assert record.vorgangs_id == "V-inner"
        reset_vorgangs_id(inner)
        record2 = _make_record()
        VorgangsnummerFilter().filter(record2)
        assert record2.vorgangs_id == "V-outer"
        reset_vorgangs_id(outer)
