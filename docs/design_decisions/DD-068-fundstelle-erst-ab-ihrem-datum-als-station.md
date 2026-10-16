[← Index](../design_decisions.md)

# DD-068: Eine PARLIS-Fundstelle wird erst ab ihrem Datum zur Station (GitHub Issue #75)

**Datum:** 16.10.2026

**Kontext:** PARLIS führt geplante Plenarsitzungen schon vorab als Fundstelle. Am 30.09.2026
stand bei V-248237 bereits „Erste Beratung Plenarprotokoll 18/13 08.10.2026“. Der Scraper
lud sie als `parl-vollvlsgn` mit `zp_start` 08.10. hoch; der synthetische `parl-initiativ`
(DD-012) übernahm dasselbe Datum. Die Website zeigte beides als bereits geschehen. Laut Spec ist
`zp_start` der Zeitpunkt der ersten Handlung der Station; geplante Stationen gibt es nicht.
Abstimmung im Issue und im Mattermost-Thread: Stationen bilden ab, was geschehen ist. Geplante
Lesungen gehören als TOP in die Sitzung (GitHub Issue #66).

**Entscheidung:**

1. `_collect_stationen` überspringt eine Fundstelle, deren Datum nach dem heutigen Tag liegt.
   „Heute“ ist der Kalendertag in Europe/Berlin (`_today`); PARLIS-Daten sind Landtagstage.
   Eine Sitzung am heutigen Tag gilt als geschehen. Fehlt ihr Protokoll noch, greift der
   bestehende Retry für unveröffentlichte PDFs (DD-067).
2. Der Vorgang wird nicht gecacht (`_retry_next_cycle`, wie bei fehlenden PDFs). Die Station
   erscheint im ersten Lauf ab ihrem Datum.
3. Nur ein INFO-Log, kein Eintrag unter „dropped Fundstellen“ im Run-Report: Das Zurückhalten
   ist erwartet und vorübergehend.
4. Der synthetische `parl-initiativ` braucht keine eigene Regel. Ohne die Lesung fällt er auf
   das Datum des Gesetzentwurfs zurück; seine `api_id` hängt nicht vom Datum ab (DD-046).
   Der Track `preparl-regbsl → parl-initiativ` ist gültig (jeder frische Gesetzentwurf).
5. Das Datum wird jetzt einmal in `_collect_stationen` geparst und an `_build_station`
   übergeben, statt dort ein zweites Mal (sonst doppelte Logs bei kaputten Daten).

**Verworfen:**

- *Geplante Stationen weiter veröffentlichen:* sieht aus wie geschehen. Wird die Sitzung
  verlegt oder der Vorgang zurückgezogen, bleibt die Station mit dem alten Datum im Backend
  (ihre `api_id` enthält `zp_start`, DD-028).
- *`parl-initiativ` zusätzlich zurückhalten:* Ohne ihn wäre ein Regierungsentwurf nur
  `preparl-regbsl`; die Einbringung ist mit der Drucksache bereits geschehen.

**Nicht abgedeckt:**

- Der Beteiligungsportal-Scraper datiert `preparl-regent` weiter auf das Fristende (Issue #75,
  Frage 2, eigener PR).
- Ein Vorgang mit geplanter Sitzung wird bis zu ihrem Datum in jedem Lauf neu hochgeladen.
- Eine Platzhalter-Jahresangabe (`00.00.YYYY` → 01.01.) in einem künftigen Jahr wird ebenfalls
  zurückgehalten.
- Bereits hochgeladene künftige Stationen bleiben im Backend. Sie werden mit ihrem Datum
  richtig, solange die Sitzung nicht verlegt wurde.

**Code:** `BawueVorgaengeScraper._collect_stationen`, `_today`, `_build_station`, `store_extracted_result`

**Tests:** `tests/unit/test_bawue_scraper.py::TestIssue75FutureFundstellen`
