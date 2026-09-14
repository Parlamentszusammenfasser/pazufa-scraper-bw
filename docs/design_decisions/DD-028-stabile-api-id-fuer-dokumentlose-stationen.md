[← Index](../design_decisions.md)

# DD-028: Stabile `api_id` für dokumentlose Stationen

**Datum:** 13.06.2026

**Kontext:** Im Staging-Lauf 2026-06-13 lehnte das Backend V-246637 („Gesetz
zur Änderung des Abgeordnetengesetzes", `gg-land-parl`, Fraktion der AfD) mit
HTTP 400 *Track validation Failed* ab:

```
station ordering: ["(2026-06-03 / parl-initiativ)", "(2026-06-03 / parl-initiativ)"]
  … has at least one station that is not adhering to the track …: ["(… / parl-initiativ)"]
```

Der gesendete Vorgang (api_obj_log) enthielt jedoch **nur eine** Station:
ein `parl-initiativ` ohne Dokumente (die einzige Fundstelle „Gesetzentwurf"
hatte keine PDF-URL → kein `Dokument`). Per Prefix-Matching (DD-016) ist ein
einzelnes `I` ein gültiger Track-Präfix (`SI` in der DD-016-Tabelle); `II` ist
es nie. Der zweite `parl-initiativ` stammte also aus einem **früheren Lauf**,
der im Backend persistiert blieb (das Löschen des Redis-Caches betrifft nur
LLM-/Scrape-Caching, nicht den Backend-Zustand).

**Ursache:** Das Backend matcht eine eingehende Station beim Re-Upload gegen
eine bestehende über ihre `api_id` **oder** einen geteilten Dokument-Hash
(`station_merge_candidates`, vgl. DD-010). Eine dokumentlose Station hat
keinen der beiden Schlüssel — bei `api_id=None` kann das Backend sie über
Läufe hinweg nicht wiedererkennen und fügt bei jedem Cycle ein Duplikat ein.
DD-010 hatte exakt dieses Problem bereits für die synthetische
`parl-ablehnung` per deterministischer `api_id` gelöst, aber nur dort.

**Entscheidung:** Jede dokumentlose Station ohne eigene `api_id` erhält eine
deterministische `api_id` aus `(vorgang_id, typ, zp_start)`
(`uuid5(NAMESPACE_URL, "bawue-station-<vid>-<typ>-<iso-zp_start>")`). Damit
matcht das Backend die Station über Läufe hinweg und **aktualisiert** die
bestehende Zeile, statt zu duplizieren. Dokumenttragende Stationen behalten
das Dokument-Hash-Matching (unverändert); Stationen mit bereits gesetzter
`api_id` (synthetische Ablehnung, DD-010) bleiben unangetastet. Die Zuweisung
erfolgt **nach** `_enforce_total_ordering`, sodass `zp_start` final ist und
der Schlüssel mit dem tatsächlich gesendeten Zeitstempel übereinstimmt.

**Warum nicht über Zeitstempel (+1h / Mittag):** `_enforce_total_ordering`
(DD-024/025) verschiebt nur **verschieden**-typige Kollisionen; gleich-typige
Stationen dürfen `zp_start` teilen. Selbst mit Offset bliebe `II` aber eine
verbotene Track-Sequenz — Zeitstempel ordnen, retten aber keine unzulässige
Typabfolge. Das Problem ist Identität/Idempotenz, nicht Sortierung.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Funktion
`_assign_stable_station_ids()`, aufgerufen in `_build_vorgang()`.

**Tests:** `tests/unit/test_bawue_scraper.py::TestBuildVorgang` —
`test_documentless_station_gets_stable_api_id` (V-246637-Muster),
`test_stable_station_api_id_is_deterministic` (gleicher Vorgang → gleiche
api_id), `test_document_bearing_station_keeps_no_api_id`,
`test_assign_stable_station_ids_skips_existing_and_documented`.

**Folgearbeit (Backend-seitig, einmalig):** Die Sammelschnittstelle des
Collectors kennt nur `vorgang_put` (kein Delete), also kann der Scraper das
bereits duplizierte V-246637 nicht selbst bereinigen. Der Fix verhindert
**künftige** Duplikate, heilt aber den Bestand nicht (die neue stabile api_id
matcht die alte `api_id=None`-Waise nicht). Das polluierte V-246637
(`ca960eec-0617-58f5-aa5e-d8bb1710f25a`) muss einmalig über den
Admin-Endpunkt `vorgang_delete` entfernt und neu eingelesen werden.
