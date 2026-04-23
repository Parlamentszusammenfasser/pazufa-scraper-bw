"""Tests for run_report helpers (duration formatting, failed item section)."""

from bawue.run_report import FailedItem, format_duration, format_failed_section


class TestFormatDuration:
    def test_sub_minute(self):
        assert format_duration(0.4) == "0m 00s"

    def test_seconds_only(self):
        assert format_duration(45.0) == "0m 45s"

    def test_exact_minute(self):
        assert format_duration(60.0) == "1m 00s"

    def test_minutes_and_seconds(self):
        assert format_duration(125.0) == "2m 05s"

    def test_hours_minutes_seconds(self):
        assert format_duration(3665.0) == "1h 01m 05s"

    def test_long_run_is_readable(self):
        # The case from the user's report: 23045.7s -> 6h 24m 06s
        assert format_duration(23045.7) == "6h 24m 06s"

    def test_zero(self):
        assert format_duration(0.0) == "0m 00s"

    def test_negative_clamped(self):
        assert format_duration(-5.0) == "0m 00s"


class TestFormatFailedSection:
    def test_empty_returns_empty_list(self):
        assert format_failed_section([]) == []

    def test_single_failure_includes_id_title_reason(self):
        items = [FailedItem(item_id="V-001", titel="Klimaschutzgesetz", reason="HTTP 422")]
        lines = format_failed_section(items)
        assert lines[0] == ""
        assert "Failed items (1):" in lines[1]
        assert any("V-001" in line and "Klimaschutzgesetz" in line and "HTTP 422" in line for line in lines)

    def test_missing_title_omits_title_part(self):
        items = [FailedItem(item_id="V-002", titel=None, reason="timeout")]
        lines = format_failed_section(items)
        joined = "\n".join(lines)
        assert "V-002" in joined
        assert "timeout" in joined

    def test_truncates_long_title(self):
        long_title = "A" * 200
        items = [FailedItem(item_id="V-003", titel=long_title, reason="err")]
        lines = format_failed_section(items, title_max_len=50)
        row = lines[-1]
        assert row.count("A") <= 50
        assert "…" in row

    def test_caps_list_and_shows_remaining_count(self):
        items = [FailedItem(item_id=f"V-{i}", titel="t", reason="r") for i in range(25)]
        lines = format_failed_section(items, max_items=10)
        assert "Failed items (25):" in lines[1]
        body = lines[2:]
        # 10 rendered rows + 1 "... N more" row
        assert len(body) == 11
        assert "15 more" in body[-1]

    def test_header_is_customizable(self):
        items = [FailedItem(item_id="S-01", titel=None, reason="r")]
        lines = format_failed_section(items, header="Failed Sitzung dates")
        assert "Failed Sitzung dates (1):" in lines[1]
