[← Index](../design_decisions.md)

# DD-022: Kanonische Namen für `Autor.organisation`

**Datum:** 22.04.2026

**Kontext:** Die Community-DoD fordert (SHOULD):

> "Ihr SOLLTET einen Mechanismus haben um kanonische Namen zu mappen. Der
> 'Verband der Podologen', 'Podologieverband e.V.', 'Podologie-Verband' und der
> 'VdPod' könnten zum Beispiel dasselbe meinen"

Hintergrund: Das Backend hat zwar einen pg_trgm-Canary
(`SIMILARITY(organisation, $2) > 0.66`, s. DD-021), der Near-Misses loggt, aber
**keine** aktive Namensvereinheitlichung. Zwei Scraper, die dasselbe Gremium
unterschiedlich benennen, erzeugen zwei `autor`-Zeilen. Produktionsdaten aus
BY (pazufa-collectors `bylt_scraper`-Testfixtures) zeigen das
Problem in der Praxis: dieselbe Person erscheint dort mit drei verschiedenen
Organisations-Strings (`Alternative für Deutschland (AfD)`, `AfD-Fraktion im
Bayerischen Landtag`, `AfD-Fraktion`). BY ist somit **nicht** Compliance-Referenz
für diese Regel.

**Entscheidung:** Der BW-Scraper normalisiert `Autor.organisation` vor dem
Upload über `canonicalize_organisation(raw)` (in `bawue/types.py`):

- **Geschlossene Menge kanonischer Formen** in der `StrEnum`
  `CanonicalOrganisation` (aktuell: 5 Landtag-BW-Fraktionen + `Landesregierung`).
  Die Schreibweise folgt der Landtag-BW-Website — insbesondere `Fraktion GRÜNE`
  ohne `der`, weil GRÜNE dort als Eigenname geführt wird.
- **Alias-Tabelle** (`_ORGANISATION_ALIASES`) mapped beobachtete + plausible
  Varianten (caps-insensitive, Whitespace-normalisiert) auf die kanonische Form.
- **Offene Menge** (einzelne Ministerien, Verbände, externe Stakeholder) passiert
  unverändert. Eine Enumeration wäre unpraktisch und ist durch den Backend-Canary
  auch nicht erforderlich — der Canary meldet schleichende Drift für diese Gruppe.

**Anwendung:**

- `bawue_vorgaenge_scraper.py::_parse_autoren` — wendet `canonicalize_organisation`
  auf jeden geparsten Autor-String an (betrifft `Vorgang.initiatoren` und
  `Dokument.autoren`).
- `bawue_beteiligung_scraper.py` — wendet `canonicalize_organisation` auf die
  Ministerium-Angabe aus dem Beteiligungsportal an.

**Bewusst nicht normalisiert:**

- **Personen-Namen** (`Autor.person`). Ehrentitel, akademische Grade, Mädchen-/
  Geburtsnamen usw. sind zu heterogen für eine zuverlässige kanonische Form.
  Der Backend-Canary bleibt hier der einzige Schutz.
- **Individuelle Ministerien.** BW-Ministerien werden häufig umbenannt
  (Koalitionswechsel, Ressortumstrukturierungen); eine starre Liste würde schnell
  veralten. Die aktuellen Namen sind in sich konsistent; bei beobachteter Drift
  wird ein einzelner Alias-Eintrag ergänzt.

**Implementierung:** `bawue/types.py`:

- `CanonicalOrganisation(StrEnum)` — 6 Werte.
- `_ORGANISATION_ALIASES: dict[str, CanonicalOrganisation]` — Varianten-Lookup.
- `_org_lookup_key(raw) -> str` — Non-Alphanumeric entfernt, lowercase.
- `canonicalize_organisation(raw) -> str` — gibt Enum-Mitglied oder
  ursprünglichen String zurück (`StrEnum` ist `str`, passt durch `StrictStr`).

**Tests:** `tests/unit/test_enum_mapper.py::TestCanonicalOrganisation`:

- `OBSERVED_ORGANISATIONS` lockt alle in Production beobachteten Autor-Strings
  gegen ihre erwartete kanonische Form.
- `test_enum_values_cover_landtag_bw_fraktionen` — schlägt fehl, wenn die
  Landtag-BW-Fraktionsliste sich ändert und das Enum nicht mit aktualisiert wurde.
- `test_idempotent_on_canonical_forms` — kanonische Form bleibt kanonisch.
- `test_known_variants_map_to_canonical` — Varianten (inkl. BY-Spielarten wie
  `Alternative für Deutschland (AfD)`) werden aufgelöst.
- `test_unknown_organisations_pass_through` — Ministerien + externe Verbände
  bleiben unverändert.

**Refresh-Prozedur:** Analog zu DD-021 (observed_station_types.md): Nach jedem
vollen Re-Scrape die Autor-Organisations-Strings aus dem JSONL extrahieren:

```bash
grep -oE "Autor\(person=None, organisation='[^']+'" \
  locallogs/00000000-0000-0000-0000-000000000001.jsonl \
  | grep -oE "organisation='[^']+'" | sort | uniq -c | sort -rn
```

Neue Varianten in `_ORGANISATION_ALIASES` ergänzen; neu beobachtete
Kanonisierungs-Kandidaten in `OBSERVED_ORGANISATIONS` eintragen.
