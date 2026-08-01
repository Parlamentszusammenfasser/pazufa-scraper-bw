# Gesetzblatt Baden-Württemberg

> ♻️ **Seit DD-047 ist das Gesetzblatt keine eigene Vorgangsquelle mehr.** Der
> frühere `BawueGesetzblattScraper` (DD-044) erzeugte Vorgänge, die nur aus einer
> `postparl-gsblt`-Station bestanden — die BW-Track-Validierung verlangt jedoch
> zwingend ein `parl-initiativ`, sodass **jeder** dieser Vorgänge mit HTTP 400
> abgelehnt wurde. Das Gesetzblatt wird stattdessen als
> `(Jahr, Nr.) → Publikationsdatum`-Lookup (`GesetzblattDateLookup`) vom
> `BawueVorgaengeScraper` genutzt, der die `postparl-gsblt`-Station ohnehin aus
> seiner eigenen PARLIS-Fundstelle baut und sie bislang nur falsch datierte
> (Issue #9). Die Abschnitte unten beschreiben Datenquelle und Seitenaufbau und
> gelten unverändert; die Beschreibungen des Scrapers/Registrys sind historisch.

Diese Dokumentation hält die Datenquelle, die MVP-Implementierung und die
Roadmap zur vollständigen Lifecycle-Abdeckung fest. Bezug:
[Roadmap #6](status.md), Issue
[parlamentszusammenfasser#40](https://codeberg.org/PaZuFa/parlamentszusammenfasser/issues/40).

## Datenquelle

| Aspekt | Wert |
|---|---|
| Index-URL | `https://www.baden-wuerttemberg.de/de/service/gesetze-und-verordnungen/gesetzblatt` |
| Detail-URL-Pattern | `/de/service/gesetze-und-verordnungen/gesetzblatt/detail/<JAHR>-<NUMMER>` |
| API / RSS | Keine — reines HTML-Scraping |
| Digital verfügbar seit | 01.01.2024 (vorher Papier; Archiv über LEO-BW / Staatsanzeiger) |
| Entdeckung | Binärsuche über `detail/<JAHR>-N` per HEAD-Requests (kein Index-Page-Crawling, keine Pagination-Begrenzung) |
| `robots.txt` | `/system/pdf/` und `/de/system/suchergebnisseite` blockiert; Detail-Pfade frei |
| User-Agent | `PaZuFa-BaWue-Scraper/0.1` |
| Default Rate-Limit | 1.0 s zwischen Requests (konfigurierbar in `[gesetzblatt]`) |

### Verfügbare Metadaten je Eintrag

- Titel
- Gesetzblatt-Nummer (`Nr. N`)
- Publikationsdatum (aus `<time datetime="…">`)
- Ausfertigungsdatum (aus `Ausfertigung: DD.MM.YYYY`)
- Gesetzblatt-Typ (`Gesetz`, `Verordnung`, `Bekanntmachung`, `Berichtigung`, …)
- Federführung (`Innenministerium (IM)` etc.)
- PDF-Link inkl. SHA256 (TYPO3 `eID=dumpFile`-URL mit Token)

### Nicht in den Metadaten

- **Drucksachennummer** des zugrundeliegenden Landtags-Vorgangs
- **Inkrafttretensdatum**

Beide Informationen stehen ausschließlich im PDF-Volltext und werden in
Phase 2 / Phase 3 erschlossen (siehe Roadmap unten).

## Architektur (MVP)

Drei neue Module nach dem etablierten Beteiligungsportal-Muster. Es gibt **kein**
Index-Page-Crawling: `listing_page_extractor` iteriert `start_year..current_year`
und findet pro Jahr die höchste vergebene Nummer per Binärsuche
(`GesetzblattClient.find_max_number`, HEAD-Requests gegen `detail/<JAHR>-N`);
jede Nummer `1..max` wird anschließend einzeln per GET abgerufen. Das deckt ein
Jahr immer vollständig ab, unabhängig davon, wie viele Einträge die Index-Seite
der Website anzeigen würde.

| Datei | Rolle |
|---|---|
| `src/bawue/gesetzblatt_client.py` | Synchroner `requests`-Client mit `AdaptiveRateLimiter`, 429-Retry und der Binärsuche (`find_max_number`) |
| `src/bawue/gesetzblatt_parser.py` | Stateless lxml/XPath-Parser für Detail-HTML |
| `src/bawue/bawue_gesetzblatt_scraper.py` | `VorgangsScraper`-Subklasse: Listing → Item → `Vorgang` mit `postparl-gsblt`-Station |

### Vorgang-Mapping

| Feld | Wert |
|---|---|
| `api_id` | `uuid5(NAMESPACE_URL, f"gsblt-{jahr}-{nummer}")` |
| `kurztitel` | `gsblt-{jahr}-{nummer}` |
| `typ` | `Vorgangstyp.GG_LAND_PARL` |
| `wahlperiode` | aus `[gesetzblatt] wahlperiode` (Default: 17) |
| `verfassungsaendernd` | `False` |
| `initiatoren` | `[Autor(organisation=federfuehrung)]`, Fallback `"Landesregierung"` |
| `stationen` | Genau eine `postparl-gsblt`-Station |

### Station-Mapping

| Feld | Wert |
|---|---|
| `typ` | `Stationstyp.POSTPARL_GSBLT` |
| `zp_start` | Publikationsdatum |
| `gremium` | `Gremium(parlament=BW, name="Gesetzblatt", wahlperiode=…)` |
| `dokumente` | Eine PDF-Referenz (Doktyp `MITTEILUNG`, `zp_referenz` = Ausfertigungsdatum) |

### Filter

Nur Einträge mit Gesetzblatt-Typ `Gesetz` werden zu Vorgängen. Verordnungen,
Bekanntmachungen, Berichtigungen und sonstige Typen werden im
`item_extractor` übersprungen — das Backend-Datenmodell deckt sie aktuell
nicht sauber ab und sie würden den Vorgangsfluss verzerren. Konstante:
`_GSBLT_ACCEPTED_TYPES = frozenset({"Gesetz"})`.

### Backend-Merge mit PARLIS

Der Backend-Merge (`pazufa-backend/src/db/merge/candidates.rs`) prüft:

```
api_id matcht
   ODER
(wahlperiode UND vorgangstyp UND mindestens ein VgIdent matcht)
```

Im MVP emittiert der GSBLT-Scraper **noch keine** `VgIdent`s, weil er die
Drucksachennummer nicht kennt — sie steckt nur im PDF-Text. Damit entstehen
zunächst eigenständige GSBLT-Vorgänge in der Datenbank.

**Wichtig:** Auf der PARLIS-Seite ist das Emittieren von `VgIdent(typ="initdrucks")`
aktuell bewusst **deaktiviert** (`emit-initdrucks-ident`, Default `false`, DD-041),
weil geteilte `initdrucks` im Backend fremde Vorgänge zusammenführen → HTTP500
`rel_station_dokument_pkey` (DD-034). Ein automatischer Cross-Source-Merge setzt
daher voraus, dass Phase 2 sowohl die Drucksnr aus dem GSBLT-PDF extrahiert **als
auch** die `initdrucks`-Emission auf beiden Seiten kollisionsfrei reaktiviert.

## Konfiguration

```toml
[gesetzblatt]
wahlperiode = 17       # used as Vorgang.wahlperiode for backend matching
request-delay-s = 1.0
```

## Tests

| Datei | Zweck |
|---|---|
| `tests/unit/test_gesetzblatt_parser.py` | Parser auf echten Fixtures (Gesetz / Verordnung / Bekanntmachung) |
| `tests/unit/test_gesetzblatt_client.py` | HTTP-Mocks (responses), 429-Retry, UTF-8-Encoding |
| `tests/unit/test_gesetzblatt_scraper.py` | Vorgang-Mapping, Typ-Filter, deterministische `api_id` |
| `tests/fixtures/gesetzblatt/` | Echte HTML-Seiten von baden-wuerttemberg.de |

## Roadmap

### Phase 2 — Drucksache-Linking

**Ziel:** Backend-Merge mit PARLIS-Vorgängen aktivieren.

- PDF-Download über `aiohttp`, Textextraktion via bestehender
  `bawue_dok.extract_pdf_text()`-Funktion (kreuzberg, OCR-Fallback)
- Drucksachen-Regex `Drucksache\s+(\d+/\d+)` (etabliert in
  `parlis_parser.py:170-173`)
- Emission von `VgIdent(typ="initdrucks", id=drucksnr)` in
  `_build_vorgang` — bedingt die kollisionsfreie Reaktivierung von
  `emit-initdrucks-ident` (DD-041/DD-034)
- Wahlperiode aus Drucksnr ableiten (`"17/1234"` → `17`); Vorgangstyp per
  Default `gg-land-parl` (Volksanträge sind seltener und im Titel
  erkennbar)
- Empirische Validierung der Drucksachen-Referenzierungs-Robustheit an
  10–20 Beispielen vor produktivem Rollout

### Phase 3 — Inkrafttreten / `postparl-kraft`

**Ziel:** Echte K-Stage statt der „Virtual K"-Diskussion aus Issue #40
abschließen.

- Regex `tritt\s+(?:am|mit Wirkung vom)\s+(\d{1,2}\.\s*\w+\s+\d{4})\s+in Kraft`
  als erste Approximation
- LLM-Fallback via `bawue_dok.enrich_dokument` für komplexe Fälle
  (gestuftes Inkrafttreten, bedingte Geltung)
- Erzeugung einer zweiten Station `postparl-kraft` mit echtem Datum
- Policy für gestuftes Inkrafttreten klären: frühestes Datum, alle als
  separate Stationen, oder eigene Modellierung im Backend

### Phase 4 — Archiv vor 2024

- LEO-BW oder Staatsanzeiger als sekundäre Quelle für historische
  Wahlperioden (insb. WP16)
- Eigener Client/Parser, da andere Datenquelle und -struktur

### Pagination — nicht erforderlich

Die Entdeckung läuft nicht über die Index-Seite (die serverseitig nur die
letzten 10 Einträge liefert), sondern über die Binärsuche in
`GesetzblattClient.find_max_number` gegen die Detail-URLs. Ein Bulk-Backfill
oder eine lange Pause erfordert daher **keine** TYPO3-Solr-Pagination
(`?lawsheetData=1&page=N&…`, `cHash`-pflichtig) — jedes Jahr in
`start_year..current_year` wird beim nächsten Lauf vollständig neu durchsucht.

## Offene Punkte / Designfragen

1. **GSBLT-Typ-Filter** (`_GSBLT_ACCEPTED_TYPES`): Wenn das Backend
   später Verordnungen oder Bekanntmachungen als eigene Vorgangstypen
   modelliert, müssen diese hier wieder zugelassen werden.
2. **Wahlperiode-Default:** Die Setzung aus `[gesetzblatt] wahlperiode`
   ist statisch. Sobald Phase 2 die Drucksnr liefert, sollte die
   Wahlperiode dynamisch aus `drucksnr.split("/")[0]` berechnet werden.
3. **PARLIS-seitiger Pfad zu GSBLT:** Heute behandelt
   `parlis_parser.py:164-168` seltene GSBLT-Erwähnungen in PARLIS
   (Fallback-Datum). Sobald der Gesetzblatt-Scraper produktiv läuft,
   sollte evaluiert werden, ob dieser Pfad obsolet wird.
4. **Stationsreihenfolge bei Merge:** Die GSBLT-Synthese erzeugt
   `postparl-*`-Stationen. Bei Phase 2 (Merge mit PARLIS) muss die
   Reihenfolge relativ zu den `parl-*`-Stationen sauber bleiben — der
   Backend-Merge sortiert nach `zp_start` (vgl. Track-Validierung DD-016/DD-040),
   sollte also unauffällig sein.
5. **Federführung-Normalisierung:** Heute wird der String
   `"Innenministerium (IM)"` 1:1 als `Autor.organisation` übernommen.
   Falls die DoD-Regel „kanonische Namen" hier greift, muss der String
   normalisiert werden (z.B. nur das Kürzel oder der Volltext).
