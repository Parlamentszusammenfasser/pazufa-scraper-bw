[← Index](../design_decisions.md)

# DD-051: Upgrade auf corelib v0.2.1 / API-Spec 0.2.5 — `trojanergefahr` entfällt, Gesetzblatt wird `gesetz`

**Datum:** 09.09.2026

**Kontext:** `pazufa-corelib` war auf `v0.1.2` (Spec 0.2.3) gepinnt. Der Sprung auf
`v0.2.1` bringt Spec 0.2.5 und damit drei Änderungen, die BaWue direkt betreffen,
plus mehrere rein additive Felder.

| Änderung | Wirkung auf BaWue |
|---|---|
| `Station.trojanergefahr` **entfernt** | Der LLM-Score hatte kein Zielfeld mehr — `Station(trojanergefahr=…)` warf `TypeError` (174 Tests rot) |
| `X-Scraper-Id` bei `PUT /api/v2/kalender/{parlament}/{datum}` **entfernt** (bei `PUT /api/v2/vorgang` bleibt er) | `kal_date_put()` akzeptiert `x_scraper_id` nicht mehr |
| `Doktyp` **neu**: `gesetz`, `eckpunktepapier`; `Stationstyp` neu: `parl-antragsst`, `parl-verfgstop`, `parl-vermittas`, `preparl-formvs` | Der Doktyp-Kanarienvogel-Test (Gleichheit statt Teilmenge) schlug an |
| **Additiv**: `Vorgang.ressort`/`sachgebiete`, `Dokument.subdoc_id`, `DokumentHash`/`HashStrategy`/`Mime`, `Zusammenfassungstupel` | Bestehende Payloads bleiben gültig; das Füllen ist eigene Arbeit (Issues #39–#43) |

**Entscheidung:**

1. **`trojanergefahr` wird vollständig entfernt** — nicht nur an der `Station`, sondern
   auch aus den LLM-Prompts (`BODY_PROMPT_ENTWURF`, `BODY_PROMPT_BESCHLUSSEMPF`), der
   Score-Validierung (`_SCORE_RANGES`) und `EnrichmentResult`. Ein Wert, den kein Feld
   mehr aufnimmt, ist sonst nur bezahlter LLM-Output ohne Abnehmer. `meinung` (1–5)
   bleibt unverändert, es ist ein `Dokument`-Feld.
2. **`put_kalender` sendet keinen `X-Scraper-Id`-Header mehr.** Der `scraper_id`-Parameter
   bleibt in der Signatur, damit die Aufrufstelle in `bawue_sitzungen_scraper` und
   `put_vorgang` symmetrisch bleiben; er wird nur nicht mehr weitergereicht.
3. **„Gesetzblatt" und „Gesetz" mappen auf `Doktyp.GESETZ`** statt auf `mitteilung`.
   Das verkündete Gesetz ist der Gesetzestext selbst, nicht die Bekanntgabe darüber;
   `mitteilung` war die beste verfügbare Näherung, solange es `gesetz` nicht gab.
   **„Bekanntmachung" bleibt `mitteilung`** — das ist tatsächlich die Verlautbarung.
   Betroffen ist das Dokument der `postparl-gsblt`-Station (DD-047).
4. **Die neuen additiven Felder bleiben vorerst leer** und sind als Issues erfasst:
   #39 (`ressort`), #40 (`sachgebiete`), #41 (strukturierter `hash` + `subdoc_id`),
   #42 (typisierte `zusammenfassung`), #43 (neue Enum-Werte). Sie einzeln zu bauen ist
   jeweils eigene Mapping-Arbeit mit eigener DD — #41 ändert zudem die Row-Identität
   von Dokumenten und muss mit dem Backend abgestimmt werden.

**Migrationshinweis:** Die Prompt-Texte ändern sich, damit auch
`_prompt_fingerprint` und die Semantik-Cache-Schlüssel (DD-020). Der erste Lauf nach
dem Upgrade berechnet die LLM-Semantik einmalig neu; die alten Redis-Einträge laufen
per TTL aus. Bereits hochgeladene Gesetzblatt-Dokumente behalten im Backend ihren
alten `mitteilung`-Typ, bis sie erneut hochgeladen werden.

**Code:** `pyproject.toml` (`rev = "v0.2.1"`), `api.py::put_kalender`,
`enum_mapper.DOKUMENTENTYP_MAP`, `bawue_dok.py` (`EnrichmentResult`, Prompts,
`_SCORE_RANGES`), `bawue_vorgaenge_scraper._build_station`/`_build_dokumente`,
`bawue_beteiligung_scraper._build_vorgang`

**Tests:**
- `tests/integration/test_spec_conformance.py` — der tatsächlich gesendete Vorgang wird
  gegen `pazufa_corelib.api_model.Vorgang` (das handgehärtete Pydantic-Modell derselben
  Spec) validiert. Der attrs-Client serialisiert ungeprüft, sonst würde ein Spec-Drift
  erst als HTTP 422 in Produktion auffallen.
- `tests/unit/test_api.py::TestPutKalender::test_path_params_positional_body_kwarg_and_no_scraper_id`
- `tests/unit/test_enum_mapper.py::TestEnumValuesExistInFramework::test_all_doktyp_values_valid`
  (Kanarienvogel: kennt jetzt `gesetz` und `eckpunktepapier`) und `test_known_patterns`
