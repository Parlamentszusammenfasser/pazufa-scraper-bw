"""Tests for the dry-run report analysis and formatting module."""

import json

import pytest

from bawue.report import (
    BeteiligungReport,
    DryRunSummary,
    FieldCompleteness,
    SitzungReport,
    VorgangReport,
    analyze_beteiligung,
    analyze_sitzungen,
    analyze_vorgang,
    build_summary,
    compute_field_completeness,
    format_summary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw_vorgang(
    vid="V-001",
    titel="Test Gesetz",
    vorgangstyp="Gesetzgebung",
    initiative="Fraktion GRÜNE",
    detail_url="https://parlis.example.com/V-001",
    fundstellen=None,
):
    """Minimal RawVorgang dict for testing."""
    if fundstellen is None:
        fundstellen = [
            {
                "raw": "Gesetzentwurf 04.02.2026 Drucksache 17/10266",
                "datum": "04.02.2026",
                "drucksache": "17/10266",
                "station_typ": "Gesetzentwurf",
                "autor_text": "Fraktion GRÜNE",
                "seiten": 13,
                "pdf_url": "https://example.com/doc.pdf",
            },
        ]
    raw = {
        "titel": titel,
        "vorgangs_id": vid,
        "Vorgangstyp": vorgangstyp,
        "Initiative": initiative,
        "fundstellen_parsed": fundstellen,
    }
    if detail_url is not None:
        raw["detail_url"] = detail_url
    return raw


def _vorgang_model(typ_str="GG_LAND_PARL", station_count=1):
    """Lightweight stand-in for a Vorgang model (only needs .typ and .stationen length)."""

    class _Station:
        pass

    class _Typ:
        def __init__(self, v):
            self.value = v

        def __str__(self):
            return self.value

    class _Vorgang:
        def __init__(self, typ, stationen):
            self.typ = _Typ(typ)
            self.stationen = stationen

    return _Vorgang(typ_str, [_Station() for _ in range(station_count)])


# ---------------------------------------------------------------------------
# analyze_vorgang
# ---------------------------------------------------------------------------


class TestAnalyzeVorgang:
    def test_analyze_vorgang_extracts_drucksache_numbers(self):
        raw = _raw_vorgang(
            fundstellen=[
                {"drucksache": "17/100", "datum": "01.01.2024"},
                {"drucksache": "17/200", "datum": "02.01.2024"},
            ]
        )
        vorgang = _vorgang_model()
        report = analyze_vorgang(raw, vorgang)

        assert report.drucksache_numbers == ["17/100", "17/200"]

    def test_analyze_vorgang_deduplicates_drucksache_numbers(self):
        raw = _raw_vorgang(
            fundstellen=[
                {"drucksache": "17/100", "datum": "01.01.2024"},
                {"drucksache": "17/100", "datum": "02.01.2024"},
                {"drucksache": "17/200", "datum": "03.01.2024"},
            ]
        )
        vorgang = _vorgang_model()
        report = analyze_vorgang(raw, vorgang)

        assert report.drucksache_numbers == ["17/100", "17/200"]

    def test_analyze_vorgang_skips_missing_drucksache(self):
        raw = _raw_vorgang(
            fundstellen=[
                {"drucksache": "17/100", "datum": "01.01.2024"},
                {"datum": "02.01.2024"},  # no drucksache
            ]
        )
        vorgang = _vorgang_model()
        report = analyze_vorgang(raw, vorgang)

        assert report.drucksache_numbers == ["17/100"]

    def test_basic_fields(self):
        raw = _raw_vorgang()
        vorgang = _vorgang_model()
        report = analyze_vorgang(raw, vorgang)

        assert isinstance(report, VorgangReport)
        assert report.vorgang_id == "V-001"
        assert report.titel == "Test Gesetz"
        assert report.raw_type == "Gesetzgebung"
        assert report.mapped_type == "GG_LAND_PARL"
        assert report.station_count == 1

    def test_missing_fields_detected(self):
        raw = _raw_vorgang(detail_url=None)
        vorgang = _vorgang_model()
        report = analyze_vorgang(raw, vorgang)

        assert "detail_url" in report.missing_fields

    def test_no_missing_fields_when_all_present(self):
        raw = _raw_vorgang()
        vorgang = _vorgang_model()
        report = analyze_vorgang(raw, vorgang)

        assert "titel" not in report.missing_fields
        assert "vorgangs_id" not in report.missing_fields
        assert "Vorgangstyp" not in report.missing_fields

    def test_fundstelle_analysis(self):
        raw = _raw_vorgang(
            fundstellen=[
                {
                    "datum": "04.02.2026",
                    "drucksache": "17/10266",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/doc.pdf",
                },
                {
                    "datum": "05.02.2026",
                    "plenarprotokoll": "17/141",
                    "station_typ": "Erste Beratung",
                },
            ]
        )
        vorgang = _vorgang_model(station_count=2)
        report = analyze_vorgang(raw, vorgang)

        assert report.fundstelle_count == 2

    def test_empty_fundstellen(self):
        raw = _raw_vorgang(fundstellen=[])
        vorgang = _vorgang_model(station_count=0)
        report = analyze_vorgang(raw, vorgang)

        assert report.station_count == 0
        assert report.fundstelle_count == 0


# ---------------------------------------------------------------------------
# analyze_beteiligung
# ---------------------------------------------------------------------------


class TestAnalyzeBeteiligung:
    def test_basic_report(self):
        from bawue.beteiligung_parser import RawBeteiligungProcess

        process = RawBeteiligungProcess(
            title="Klimaschutzgesetz",
            url="/de/mitmachen/lp-17/klimaschutzgesetz",
            slug="klimaschutzgesetz",
            status="closed",
        )
        detail = type("Detail", (), {
            "title": "Klimaschutzgesetz",
            "ministry": "Umweltministerium",
            "pdf_links": [{"title": "Entwurf", "url": "https://example.com/entwurf.pdf"}],
        })()

        report = analyze_beteiligung(process, detail)

        assert isinstance(report, BeteiligungReport)
        assert report.slug == "klimaschutzgesetz"
        assert report.title == "Klimaschutzgesetz"
        assert report.ministry == "Umweltministerium"
        assert report.pdf_count == 1
        assert report.skipped is False

    def test_skipped_when_no_pdfs(self):
        from bawue.beteiligung_parser import RawBeteiligungProcess

        process = RawBeteiligungProcess(
            title="Info Only", url="/de/info", slug="info-only", status="open"
        )
        detail = type("Detail", (), {
            "title": "Info Only",
            "ministry": "Staatsministerium",
            "pdf_links": [],
        })()

        report = analyze_beteiligung(process, detail)

        assert report.skipped is True
        assert report.pdf_count == 0


# ---------------------------------------------------------------------------
# analyze_sitzungen
# ---------------------------------------------------------------------------


class TestAnalyzeSitzungen:
    def test_basic_sitzung_report(self):
        from datetime import datetime

        from bawue.ics_parser import ParsedEvent

        events = [
            ParsedEvent(
                uid="1", summary="Plenarsitzung: Tag 1",
                dtstart=datetime(2026, 2, 20, 9, 0),
                dtend=datetime(2026, 2, 20, 17, 0),
                gremium_name="Plenum",
            ),
            ParsedEvent(
                uid="2", summary="Fraktions- und Ausschusssitzungen: Ausschuesse",
                dtstart=datetime(2026, 2, 20, 14, 0),
                dtend=datetime(2026, 2, 20, 18, 0),
                gremium_name="Ausschusssitzungen",
            ),
        ]

        report = analyze_sitzungen(events, "2026-02-20")

        assert isinstance(report, SitzungReport)
        assert report.date_key == "2026-02-20"
        assert report.event_count == 2
        assert "Plenum" in report.gremium_names
        assert "Ausschusssitzungen" in report.gremium_names

    def test_empty_events(self):
        report = analyze_sitzungen([], "2026-02-20")

        assert report.event_count == 0
        assert report.gremium_names == []


# ---------------------------------------------------------------------------
# compute_field_completeness
# ---------------------------------------------------------------------------


class TestComputeFieldCompleteness:
    def test_all_present(self):
        reports = [
            _raw_vorgang(),
            _raw_vorgang(vid="V-002"),
        ]
        result = compute_field_completeness(reports, ["titel", "vorgangs_id"])

        assert len(result) == 2
        for fc in result:
            assert isinstance(fc, FieldCompleteness)
            assert fc.present == 2
            assert fc.missing == 0
            assert fc.rate == 1.0

    def test_partial_completeness(self):
        reports = [
            _raw_vorgang(),
            _raw_vorgang(vid="V-002", detail_url=None),
        ]
        result = compute_field_completeness(reports, ["detail_url"])

        assert len(result) == 1
        fc = result[0]
        assert fc.field == "detail_url"
        assert fc.present == 1
        assert fc.missing == 1
        assert fc.rate == pytest.approx(0.5)

    def test_empty_list(self):
        result = compute_field_completeness([], ["titel"])

        assert len(result) == 1
        assert result[0].present == 0
        assert result[0].missing == 0
        assert result[0].rate == 0.0


# ---------------------------------------------------------------------------
# build_summary
# ---------------------------------------------------------------------------


class TestBuildSummary:
    def test_builds_aggregate(self):
        vorgang_reports = [
            VorgangReport(
                vorgang_id="V-001", titel="Test", raw_type="Gesetzgebung",
                mapped_type="GG_LAND_PARL", station_count=2,
                missing_fields=[], fundstelle_count=2,
            ),
        ]
        beteiligung_reports = [
            BeteiligungReport(
                slug="klima", title="Klimaschutz", ministry="UM",
                pdf_count=1, skipped=False,
            ),
            BeteiligungReport(
                slug="info", title="Info", ministry="SM",
                pdf_count=0, skipped=True,
            ),
        ]
        sitzung_reports = [
            SitzungReport(date_key="2026-02-20", event_count=3, gremium_names=["Plenum"]),
        ]

        summary = build_summary(
            vorgang_reports=vorgang_reports,
            beteiligung_reports=beteiligung_reports,
            sitzung_reports=sitzung_reports,
            raw_vorgaenge=[_raw_vorgang()],
            duration_s=42.0,
            wahlperiode=17,
        )

        assert isinstance(summary, DryRunSummary)
        assert summary.total_vorgaenge == 1
        assert summary.total_beteiligung == 2
        assert summary.beteiligung_with_pdfs == 1
        assert summary.beteiligung_skipped == 1
        assert summary.total_sitzung_dates == 1
        assert summary.total_sitzung_events == 3
        assert summary.duration_s == 42.0
        assert summary.wahlperiode == 17

    def test_build_summary_collects_all_drucksache_numbers(self):
        vorgang_reports = [
            VorgangReport(
                vorgang_id="V-001", titel="Test", raw_type="Gesetzgebung",
                mapped_type="GG_LAND_PARL", station_count=1,
                missing_fields=[], fundstelle_count=2,
                drucksache_numbers=["17/100", "17/200"],
            ),
            VorgangReport(
                vorgang_id="V-002", titel="Test2", raw_type="Gesetzgebung",
                mapped_type="GG_LAND_PARL", station_count=1,
                missing_fields=[], fundstelle_count=1,
                drucksache_numbers=["17/200", "17/300"],
            ),
        ]

        summary = build_summary(
            vorgang_reports=vorgang_reports,
            beteiligung_reports=[],
            sitzung_reports=[],
            raw_vorgaenge=[_raw_vorgang(), _raw_vorgang(vid="V-002")],
            duration_s=1.0,
            wahlperiode=17,
        )

        # deduplicated and sorted
        assert summary.drucksache_numbers == ["17/100", "17/200", "17/300"]


# ---------------------------------------------------------------------------
# format_summary
# ---------------------------------------------------------------------------


class TestFormatSummary:
    def _make_summary(self):
        return DryRunSummary(
            total_vorgaenge=23,
            by_parlis_type={"Gesetzgebung": 5, "Kleine Anfrage": 12, "Antrag": 6},
            vorgang_field_completeness=[
                FieldCompleteness(field="titel", present=23, missing=0, rate=1.0),
                FieldCompleteness(field="detail_url", present=20, missing=3, rate=20 / 23),
            ],
            fundstelle_field_completeness=[
                FieldCompleteness(field="datum", present=47, missing=0, rate=1.0),
                FieldCompleteness(field="autor_text", present=30, missing=17, rate=30 / 47),
            ],
            total_beteiligung=8,
            beteiligung_with_pdfs=3,
            beteiligung_skipped=5,
            total_sitzung_dates=12,
            total_sitzung_events=34,
            sitzung_filtered=18,
            sitzung_kept=16,
            vorgang_reports=[],
            beteiligung_reports=[],
            sitzung_reports=[],
            duration_s=45.2,
            wahlperiode=17,
            drucksache_numbers=["17/100", "17/200", "17/300"],
        )

    def test_verbosity_0_includes_header_and_totals(self):
        text = format_summary(self._make_summary(), verbosity=0)

        assert "PaZuFa BaWue Dry-Run Report" in text
        assert "Total: 23" in text
        assert "Gesetzgebung" in text
        assert "Total: 8" in text

    def test_verbosity_0_includes_field_completeness(self):
        text = format_summary(self._make_summary(), verbosity=0)

        assert "titel" in text
        assert "100%" in text
        assert "detail_url" in text
        assert "!!" in text  # flag for low completeness

    def test_verbosity_0_includes_sitzungen(self):
        text = format_summary(self._make_summary(), verbosity=0)

        assert "Dates: 12" in text
        assert "Events: 34" in text

    def test_format_returns_string(self):
        text = format_summary(self._make_summary(), verbosity=0)
        assert isinstance(text, str)
        assert len(text) > 100

    def test_json_output(self):
        summary = self._make_summary()
        text = format_summary(summary, verbosity=0, as_json=True)
        data = json.loads(text)
        assert data["total_vorgaenge"] == 23
        assert data["total_beteiligung"] == 8

    def test_format_summary_header_shows_wahlperiode(self):
        text = format_summary(self._make_summary(), verbosity=0)

        assert "Wahlperiode: 17" in text
        assert "Start:" not in text
        assert "Lookback:" not in text

    def test_format_summary_lists_drucksachen(self):
        text = format_summary(self._make_summary(), verbosity=0)

        assert "Drucksachen found (3)" in text
        assert "17/100" in text
        assert "17/200" in text
        assert "17/300" in text

    def test_format_summary_empty_drucksachen_not_shown(self):
        summary = self._make_summary()
        summary.drucksache_numbers = []
        text = format_summary(summary, verbosity=0)

        assert "Drucksachen found" not in text

    def test_format_summary_verbosity2_shows_drucksachen_per_vorgang(self):
        summary = self._make_summary()
        summary.vorgang_reports = [
            VorgangReport(
                vorgang_id="V-12345",
                titel="Test Gesetz",
                raw_type="Gesetzgebung",
                mapped_type="GG_LAND_PARL",
                station_count=2,
                missing_fields=[],
                fundstelle_count=2,
                drucksache_numbers=["17/100", "17/101"],
            ),
        ]
        text = format_summary(summary, verbosity=2)

        assert "Drucksachen: 17/100, 17/101" in text

    def test_to_serializable_includes_drucksache_numbers(self):
        summary = self._make_summary()
        text = format_summary(summary, verbosity=0, as_json=True)
        data = json.loads(text)

        assert "drucksache_numbers" in data
        assert data["drucksache_numbers"] == ["17/100", "17/200", "17/300"]
        assert "wahlperiode_start_date" not in data
        assert "lookback_days" not in data

    def test_verbosity_2_placeholder(self):
        """Verbosity=2 should at least not error — detail formatting tested with real data."""
        summary = self._make_summary()
        summary.vorgang_reports = [
            VorgangReport(
                vorgang_id="V-12345",
                titel="Gesetz zur Aenderung des Schulgesetzes",
                raw_type="Gesetzgebung",
                mapped_type="GG_LAND_PARL",
                station_count=2,
                missing_fields=["detail_url"],
                fundstelle_count=2,
                drucksache_numbers=[],
            ),
        ]
        text = format_summary(summary, verbosity=2)
        assert "V-12345" in text
        assert "Gesetz zur Aenderung" in text
