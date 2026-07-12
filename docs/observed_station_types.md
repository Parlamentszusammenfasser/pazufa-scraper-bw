# Observed PARLIS Station Types

Canonical inventory of `station_typ` strings emitted by PARLIS Fundstellen, extracted from a production-equivalent run.
Source for the`OBSERVED_STATION_TYPES` parametrisation in `tests/unit/test_enum_mapper.py`that satisfies the DoD rule "
Tests müssen sicherstellen, dass alle vorkommenden Stationstypen mindestens einmal geparsed wurden".

## Provenance

- **Run date:** 2026-04-21 / 22 (full WP 17 scrape, ~9 h)
- **Scope:** Vorgangstypen `Gesetzgebung`, `Haushaltsgesetzgebung`, `Volksantrag`(the production-enabled set in
  `config.toml`)
- **Volume:** 173 Vorgänge discovered, 165 published, 5 skipped(`only post-parliamentary stations`), 3 failed (Track
  validation)
- **Extraction:** `Dokument.titel` is set to the raw `station_typ_str` in`bawue_vorgaenge_scraper.py::_build_dokumente`,
  so the JSONL upload log is a faithful record of every Fundstelle prefix that produced a station with a PDF. Non-PDF
  stations are not represented here but are negligible because every parliamentary step in PARLIS carries at least one
  Drucksache.

## Observed station_typ strings (production)

| count | station_typ                                  | maps to         |
|-------|----------------------------------------------|-----------------|
| 455   | `Gesetzentwurf`                              | parl-initiativ¹ |
| 265   | `Beschlussempfehlung und Bericht`            | parl-ausschber  |
| 263   | `Erste Beratung`                             | parl-vollvlsgn  |
| 262   | `Zweite Beratung`                            | parl-vollvlsgn  |
| 219   | `Gesetzesbeschluss des Landtags`             | parl-akzeptanz² |
| 219   | `Gesetz`                                     | postparl-gsblt  |
| 83    | `Änderungsantrag`                            | parl-initiativ³ |
| 23    | `Bekanntmachung über das Inkrafttreten`      | postparl-gsblt⁴ |
| 4     | `Berichtigung des Gesetzes`                  | postparl-gsblt² |
| 4     | `Bekanntmachung der Neufassung`              | postparl-gsblt² |
| 4     | `Änderungsanträge`                           | parl-initiativ³ |
| 1     | `Zweite und Dritte Beratung`                 | parl-vollvlsgn² |
| 1     | `Beschluss des Landtags in Zweiter Beratung` | parl-vollvlsgn  |
| 1     | `Antrag`                                     | parl-initiativ³ |

¹ Or `preparl-regbsl` when initiator contains "Landesregierung" (DD-003).
² Matched via the leading substring (e.g. `Gesetzesbeschluss`, `Gesetz`,`Dritte Beratung`) — the trailing payload does
not affect the mapping.
³ Reclassified by the scraper after the enum mapping step:Änderungsanträge attach as documents to the next
`parl-vollvlsgn`(DD-001); a bare `Antrag` after a committee report is reclassified as Änderungsantrag by the positional
heuristic (DD-019).
⁴ "Bekanntmachung" outranks "Inkrafttreten" by length in the longest-first match. This is intentional — the Fundstelle
represents the Gesetzblatt publication, not a separate Inkrafttreten step.

## Other observations from the run

- **Skipped Vorgänge** ("only post-parliamentary stations"): 5 — all driven by
  the meta-categories `Neufassung der Geschäftsordnung`, `Bekanntmachung des
  Staatsministeriums über das Inkrafttreten`, `Berichtigung des Gesetzes` (DD-036).
- **Regex fallback** (`station_typ not extracted by regex`): occurred for a
  handful of Fundstellen whose raw text begins with `Plenarprotokoll N/M …`
  — no station-type prefix at all. These map to `SONSTIG` via `map_stationstyp()`.
  Since DD-031, the *first* such unlabeled Plenarprotokoll Fundstelle (before
  any labeled reading is recorded) is recovered as `parl-vollvlsgn` by a
  positional fallback in `_collect_stationen`, rather than being filtered —
  PARLIS omits the reading label often enough that dropping it silently lost
  the `gg-land-parl` track's required second `V` (see DD-016, DD-031). Any
  *subsequent* unlabeled Plenarprotokoll Fundstelle in the same Vorgang is
  still filtered as `SONSTIG` as before.
- **Reclassification** ("Antrag" → Änderungsantrag after Ausschussbericht): 1
  occurrence (V-214623), confirming DD-019 is exercised in production.

## Types in `STATIONSTYP_MAP` not observed in this run

The mapper supports additional keys that did not appear because they are
either restricted to non-enabled Vorgangstypen or are rare in WP 17:

- `Anträge` (plural without Änderung-)
- `Kleine Anfrage`, `Große Anfrage`, `Mündliche Anfrage`
- `Volksantrag`, `Bericht und Empfehlungen`, `Ausschussberatung`
- bare `Dritte Beratung`, `Beratung`, `Überweisung`
- `Beschluss des Landtags in Dritter Beratung`, bare `Beschluss des Landtags`
- `Zustimmung`, `Annahme`, `Ablehnung`
- bare `Bekanntmachung`, `Gesetzblatt`, `Inkrafttreten`

These remain in the test parametrisation as defensive coverage so that any
shorter key being shadowed by a future map change is still caught.

## Documented `SONSTIG` fallbacks

Per DoD ("Jedes Mapping auf 'Sonstig' MUSS dokumentiert sein"). None of these
appeared as observed station types in the production run, but each is a known
PARLIS label whose intentional `SONSTIG` mapping is asserted by
`TestStationstypDocumentedSonstig`:

| station_typ     | DD ref | rationale                                                  |
|-----------------|--------|------------------------------------------------------------|
| `Mitteilung`    | DD-002 | sender/timing-dependent, no single mapping is correct      |
| `Stellungnahme` | DD-005 | scraper attaches as child of preceding station             |
| `Antwort`       | DD-005 | scraper attaches as child of preceding station             |
| `Neufassung`    | DD-036 | postparl-only meta-entry, scraper skips the whole Vorgang  |
| `Berichtigung`  | DD-036 | postparl-only meta-entry, scraper skips the whole Vorgang  |
| `Dokument`      | DD-017 | generic PARLIS label with no parliamentary-process meaning |

## Refresh procedure

Re-run the inventory whenever PARLIS-enabled Vorgangstypen change or once
per legislative period:

```bash
grep -oE "Dokument\(api_id=None, touched_by=None, drucksnr=[^,]+, typ=<[^>]+>, titel='[^']+'" \
  locallogs/00000000-0000-0000-0000-000000000001.jsonl \
  | grep -oE "titel='[^']+'" | sort | uniq -c | sort -rn
```

Update both this document and the `OBSERVED_STATION_TYPES` list in
`tests/unit/test_enum_mapper.py` if new types appear.
