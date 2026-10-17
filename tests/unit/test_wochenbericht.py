"""Weekly summary of new and moved Gesetzgebungsvorgänge (issue #82, DD-072)."""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from bawue.wochenbericht import report_lines, stages, weekly_section

NOW = datetime(2026, 10, 19, 3, tzinfo=UTC)  # Monday, ISO week 2026-W43


def _vorgang(api_id: str, *stationen: tuple[str, str], typ: str = "gg-land-parl", kurztitel: str = "") -> dict:
    v = {"api_id": api_id, "titel": f"Titel {api_id}", "typ": typ}
    v["stationen"] = [{"typ": t, "zp_start": f"{d}T00:00:00+00:00"} for t, d in stationen]
    if kurztitel:
        v["kurztitel"] = kurztitel
    return v


class TestStages:
    def test_latest_started_station_counts_planned_ones_not(self):
        v = _vorgang(
            "a",
            ("parl-vollvlsgn", "2026-10-21"),  # planned reading (DD-069)
            ("parl-initiativ", "2026-10-01"),
            ("parl-ausschber", "2026-10-14"),
            kurztitel="Kurz",
        )
        assert stages([v], NOW) == {"a": ["Kurz", "parl-ausschber"]}

    def test_only_gesetzgebung_and_only_started_vorgaenge(self):
        vorgaenge = [
            _vorgang("a", ("parl-initiativ", "2026-10-01"), typ="sonstig"),
            _vorgang("b", ("parl-initiativ", "2026-11-01")),
            _vorgang("c", ("parl-initiativ", "2026-10-01"), typ="gg-land-volk"),
        ]
        assert stages(vorgaenge, NOW) == {"c": ["Titel c", "parl-initiativ"]}


class TestReportLines:
    def test_new_and_moved_vorgaenge_unchanged_ones_not(self):
        old = {"a": ["A", "parl-initiativ"], "b": ["B", "parl-ausschber"]}
        new = {"a": ["A", "parl-ausschber"], "b": ["B", "parl-ausschber"], "c": ["C", "parl-initiativ"]}
        assert report_lines(old, new) == ["🆕 C: Eingebracht", "➡️ A: Eingebracht → Ausschussberatung"]

    def test_unknown_typ_is_shown_raw(self):
        assert report_lines({}, {"a": ["A", "sonstig"]}) == ["🆕 A: sonstig"]

    def test_long_lists_are_cut(self):
        new = {f"{i:02d}": [f"V{i:02d}", "parl-initiativ"] for i in range(25)}
        lines = report_lines({}, new)
        assert len(lines) == 21
        assert lines[-1] == "… 5 weitere"


class TestWeeklySection:
    @staticmethod
    def _config(stored: dict | None) -> MagicMock:
        config = MagicMock()
        config.cache.disabled = False
        config.cache.redis_client.get.return_value = json.dumps(stored) if stored is not None else None
        return config

    @staticmethod
    def _stored(config: MagicMock) -> dict:
        return json.loads(config.cache.store_raw.call_args.args[1])

    def test_first_run_stores_the_stand_without_a_report(self):
        config = self._config(None)
        with patch("bawue.wochenbericht.get_vorgaenge", return_value=[_vorgang("a", ("parl-initiativ", "2026-10-01"))]):
            assert weekly_section(config, NOW) is None
        assert self._stored(config) == {"woche": "2026-W43", "stand": {"a": ["Titel a", "parl-initiativ"]}}

    def test_reported_once_per_week(self):
        config = self._config({"woche": "2026-W43", "stand": {}})
        with patch("bawue.wochenbericht.get_vorgaenge") as get:
            assert weekly_section(config, NOW) is None
        get.assert_not_called()

    def test_new_week_reports_the_changes_since_the_stored_stand(self):
        config = self._config({"woche": "2026-W42", "stand": {"a": ["Titel a", "parl-initiativ"]}})
        vorgaenge = [_vorgang("a", ("parl-initiativ", "2026-10-01"), ("parl-ausschber", "2026-10-14"))]
        with patch("bawue.wochenbericht.get_vorgaenge", return_value=vorgaenge):
            title, lines = weekly_section(config, NOW)
        assert title == "Wochenbericht Gesetzgebung 2026-W43"
        assert lines == ["➡️ Titel a: Eingebracht → Ausschussberatung"]
        assert self._stored(config)["woche"] == "2026-W43"

    def test_a_quiet_week_says_so(self):
        config = self._config({"woche": "2026-W42", "stand": {}})
        with patch("bawue.wochenbericht.get_vorgaenge", return_value=[]):
            assert weekly_section(config, NOW)[1] == ["Keine Änderungen."]

    def test_backend_failure_keeps_the_old_stand_for_the_next_run(self):
        config = self._config({"woche": "2026-W42", "stand": {}})
        with patch("bawue.wochenbericht.get_vorgaenge", side_effect=RuntimeError("503")):
            assert weekly_section(config, NOW) is None
        config.cache.store_raw.assert_not_called()

    def test_redis_read_failure_keeps_the_stored_stand(self):
        """A failed read must not look like a first run and overwrite the stand (review #126)."""
        config = self._config(None)
        config.cache.redis_client.get.side_effect = ConnectionError("redis down")
        with patch("bawue.wochenbericht.get_vorgaenge") as get:
            assert weekly_section(config, NOW) is None
        get.assert_not_called()
        config.cache.store_raw.assert_not_called()

    def test_no_report_when_the_new_stand_was_not_stored(self):
        """Otherwise the next run sees last week's marker and posts the report again (review #126)."""
        config = self._config({"woche": "2026-W42", "stand": {}})
        config.cache.store_raw.return_value = False
        with patch("bawue.wochenbericht.get_vorgaenge", return_value=[]):
            assert weekly_section(config, NOW) is None

    def test_disabled_cache_skips_without_fetching(self):
        config = self._config(None)
        config.cache.disabled = True
        with patch("bawue.wochenbericht.get_vorgaenge") as get:
            assert weekly_section(config, NOW) is None
        get.assert_not_called()
