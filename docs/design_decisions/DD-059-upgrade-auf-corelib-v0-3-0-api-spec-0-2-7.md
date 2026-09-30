[← Index](../design_decisions.md)

# DD-059: Upgrade auf corelib v0.3.0 / API-Spec 0.2.7 — `X-Scraper-Id` beim Kalender wieder Pflicht, `postparl-vgstp`

**Datum:** 30.09.2026

**Kontext:** `pazufa-corelib` war auf `v0.2.1` (Spec 0.2.5, DD-051) gepinnt. Das Upgrade
auf die neueste Version `v0.3.0` (Spec 0.2.7, über v0.2.2) ist die Voraussetzung für #33:
Erst Spec 0.2.7 kennt `Vorgang.schlagworte`.

| Änderung in corelib 0.2.2/0.3.0 | Wirkung auf BaWue |
|---|---|
| `PUT /api/v2/kalender/{parlament}/{datum}` verlangt `X-Scraper-Id` wieder (0.2.5 hatte ihn versehentlich gestrichen) | `kal_date_put()` hat `x_scraper_id` als Pflichtargument. `put_kalender` rief es ohne auf, das wäre im Betrieb ein `TypeError` gewesen. Die Unit-Tests merkten es nicht, weil sie den Client ohne Signaturprüfung mockten |
| `Stationstyp.parl-verfgstop` → `postparl-vgstp` | BaWue erzeugt den Wert nicht (DD-057). Der Kanarienvogel-Test hat die Umbenennung wie vorgesehen gemeldet |
| `Vorgangstyp`: `gg-einspruch`/`gg-zustimmung` → elf `bu-*`-Typen | Keine; BaWue nutzt `gg-land-parl`, `gg-land-volk` und `sonstig`, die unverändert sind |
| `Vorgang.schlagworte` neu, `Station.schlagworte` veraltet | BaWue füllt `Station.schlagworte` nicht. `Vorgang.schlagworte` bleibt vorerst leer (#33) |
| `normalize_volltext`: entfernt HTML-Markup, behält echte Bindestriche am Zeilenende (`Baden-\nWürttemberg` → `Baden-Württemberg`), entfernt alle Unicode-Formatzeichen und Leerzeichen am Zeilenende | Der `volltext` der Dokumente wird sauberer. BaWues Zusatzpässe (Garbled-Filter, Issue #20) bleiben |
| `hash_text` normalisiert nicht mehr, `If-Modified-Since`-Schreibweise, `CreateApiKey.keytag_prefix`, Bulk-Delete | Keine; BaWue nutzt nichts davon (Hashes sind Platzhalter aus dem Link, DD-048) |

**Entscheidung:**

1. **Pin auf `v0.3.0`**, die neueste Version.
2. **`put_kalender` sendet `X-Scraper-Id` wieder** (`str(scraper_id)`), damit ist DD-051
   Punkt 2 aufgehoben.
3. **Die API-Tests patchen den generierten Client mit `autospec=True`.** Ein
   geändertes Pflichtargument lässt dann schon den Unit-Test scheitern und nicht erst die
   Produktion. Der Kalender-Test schlug mit genau dem `TypeError` an, der sonst im Betrieb
   aufgetreten wäre.
4. **Der Kanarienvogel führt `postparl-vgstp`** statt `parl-verfgstop` (DD-057).

**Migrationshinweis:** Der geänderte `volltext` ändert nicht die Cache-Schlüssel: Der
Semantik-Cache hängt an Datei-Hash und Prompt (DD-020), der `vg2:`-Fingerprint am
PARLIS-Record (DD-052). Neu gebaute Vorgänge laden den saubereren Text hoch, gecachte
behalten den alten bis zum nächsten Neubau.

**Code:** `pyproject.toml`/`poetry.lock` (`rev = "v0.3.0"`), `api.put_kalender`,
`enum_mapper.py` (Referenzkommentar)

**Tests:**
- `tests/unit/test_api.py::TestPutKalender::test_path_params_positional_body_and_scraper_id_kwargs`
  und `TestPutVorgang::test_passes_scraper_id_as_str_header` (beide mit `autospec`)
- `tests/unit/test_enum_mapper.py::TestEnumValuesExistInFramework::test_all_stationstyp_values_valid`
- `tests/unit/test_bawue_dok.py::TestNormalizeVolltext::test_trailing_whitespace_collapsed`
