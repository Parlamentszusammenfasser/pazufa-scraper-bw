# Anforderungen: BaWue-Scraper für den Parlamentszusammenfasser

## Übersicht

Eigenständiger Scraper für den **Baden-Württembergischen Landtag** (`BW`) im
[PaZuFa](https://codeberg.org/PaZuFa/parlamentszusammenfasser)-System. Der Scraper bringt Einstiegspunkt,
Konfigurationslader, Redis-Cache und Scraping-Loop selbst mit (`bawue.__main__` / `config` / `cache` /
`pipeline`) und hängt nur von [pazufa-scraper-core](https://codeberg.org/PaZuFa/pazufa-scraper-core)
(API-Client, LLM, Normalisierung) plus Standard-Python-Paketen ab. Die drei Scraper leiten von den lokalen
`VorgangsScraper`- / `SitzungsScraper`-Basisklassen ab; Scheduling, Redis-Caching, API-Einlieferung und
Dokumentenpipeline (PDF/OCR/LLM) laufen im Scraper selbst.

Das Projekt richtet sich nach der
Community-[Definition of Done](https://wiki.pazufa.de/books/scraper/page/definition-of-done); die daraus folgenden
Anforderungen sind in den jeweiligen Abschnitten unten integriert.

## Identifikatoren

| Identifikator         | Format    | Beispiel       | Quelle                                                      |
|-----------------------|-----------|----------------|-------------------------------------------------------------|
| Vorgangsnummer        | `V-XXXXX` | `V-42771`      | PARLIS HTML / JS                                            |
| Drucksachennummer     | `WP/NR`   | `17/10266`     | Fundstellen-Regex                                           |
| Plenarprotokollnummer | `WP/NR`   | `17/141`       | Fundstellen-Regex                                           |
| API-ID                | UUID v5   | `550e8400-...` | Vom Scraper generiert (`uuid5(NAMESPACE_URL, vorgangs_id)`) |

## Backend-API

Der Scraper erzeugt `Vorgang`- und `Sitzung`-Objekte und liefert sie selbst über `bawue.api`
(httpx-Client aus `pazufa-scraper-core`) ein.

**Schreib-Endpunkte (Scope `collector`):**

| Endpunkt                               | Methode | Beschreibung                                               |
|----------------------------------------|---------|------------------------------------------------------------|
| `/api/v2/vorgang`                      | PUT     | Vorgang einliefern (idempotent, Backend übernimmt Merging) |
| `/api/v2/kalender/{parlament}/{datum}` | PUT     | Sitzungen für ein Datum setzen (max. 1 Tag in der Zukunft) |

**Authentifizierung:** Header `X-API-Key`, 64-Zeichen-Key mit Präfix `ltzf_`.
Konfiguration via `config.toml` (`[backend] ltzf-api-key`) oder `LTZF_API_KEY`.

**Deduplizierung:** PUT-Requests sind idempotent. Backend übernimmt Merging. Der Scraper cacht via Redis (`bawue.cache`,
2-Wochen-TTL).
Der Scraper muss sich **nicht** um Deduplizierung kümmern, soll aber einheitliche Autoren-/Organisationsnamen liefern.

## Datenmodelle

Modelle werden automatisch aus der OpenAPI-Spezifikation generiert (`openapi-client`). Keine manuellen Pydantic-Modelle.

### Vorgang

| Feld                  | Typ              | Pflicht | BaWue-Hinweise                                                                                                                                                                                                                                                                                           |
|-----------------------|------------------|---------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `api_id`              | UUID             | Ja      | `uuid5(NAMESPACE_URL, vorgangs_id)`                                                                                                                                                                                                                                                                      |
| `titel`               | string           | Ja      |                                                                                                                                                                                                                                                                                                          |
| `typ`                 | Vorgangstyp      | Ja      |                                                                                                                                                                                                                                                                                                          |
| `wahlperiode`         | integer          | Ja      | Konfigurationsdefault: 17 (`config.sample.toml`/`config.prod.toml`); Landtag ist seit 2026-05-01 in WP 18, `config.staging.toml`/`config.dev.toml` laufen bereits auf 18 — Entscheidung offen in [#63](https://github.com/Parlamentszusammenfasser/pazufa-scraper-bw/issues/63)                          |
| `verfassungsaendernd` | boolean          | Ja      | Titel-Heuristik: `True` bei Match auf `Änderung der (Landes)?Verfassung` oder `Verfassungsänderung`, sonst `False`. PARLIS liefert das Attribut nicht — DoD-Konflikt zur „omit object"-Regel dokumentiert in [DD-023](design_decisions/DD-023-verfassungsaendernd-titel-heuristik-statt-omit-object.md). |
| `initiatoren`         | list[Autor]      | Ja      |                                                                                                                                                                                                                                                                                                          |
| `stationen`           | list[Station]    | Ja      |                                                                                                                                                                                                                                                                                                          |
| `kurztitel`           | string           | Nein    | LLM-generiert (≤ 60 Zeichen), Fallback auf `titel` ohne `[llm]` (DD-053)                                                                                                                                                                                                                                 |
| `ids`                 | list[VgIdent]    | Nein    | Enthält `VgIdent(id=vorgangs_id, typ=VgIdentTyp.VORGNR)`                                                                                                                                                                                                                                                 |
| `links`               | list[string]     | Nein    | PARLIS-Detailseiten-URL (`bawue_vorgaenge_scraper.py`) bzw. Beteiligungsportal-URL (`bawue_beteiligung_scraper.py`) als Backlink zur Quelle (Issue #31)                                                                                                                                                  |
| `lobbyregister`       | list[...]        | Nein    | Noch nicht befüllt                                                                                                                                                                                                                                                                                       |
| `ressort`             | Ressort          | Nein    | LLM-klassifiziert nach sachlichem Schwerpunkt (nicht Akteur), eigener LLM-Call je Vorgang (DD-055). `UNSET` ohne LLM, bei `null`, unauflösbarer Antwort oder Fehler                                                                                                                                      |
| `sachgebiete`         | list[Sachgebiet] | Nein    | Noch nicht befüllt (Issue #40)                                                                                                                                                                                                                                                                           |

### Station

| Feld          | Typ                         | Pflicht | BaWue-Hinweise                                                               |
|---------------|-----------------------------|---------|------------------------------------------------------------------------------|
| `typ`         | Stationstyp                 | Ja      |                                                                              |
| `dokumente`   | list[StationDokumenteInner] | Ja      | `StationDokumenteInner`-Wrapper (Union-Typ aus OpenAPI-Spec)                 |
| `zp_start`    | datetime                    | Ja      |                                                                              |
| `gremium`     | Gremium                     | Ja      | Aus PARLIS-Fundstellen abgeleitet — siehe [architecture.md](architecture.md) |
| `titel`       | string                      | Nein    |                                                                              |
| `schlagworte` | list[string]                | Nein    |                                                                              |

### Dokument

| Feld              | Typ                         | Pflicht | BaWue-Hinweise                                                                                                                                                                                                                      |
|-------------------|-----------------------------|---------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `titel`           | string                      | Ja      |                                                                                                                                                                                                                                     |
| `volltext`        | string                      | Ja      | Initial `TODO`-Platzhalter. Bei aktivem LLM (`[llm]`) füllt `bawue_dok.py` den Volltext direkt via PDF-Extraktion (kreuzberg).                                                                                                      |
| `hash`            | string                      | Ja      | Initial `TODO`-Platzhalter. Bei aktivem LLM (`[llm]`) berechnet `bawue_dok.py` den SHA256-Hash.                                                                                                                                     |
| `typ`             | Doktyp                      | Ja      |                                                                                                                                                                                                                                     |
| `zp_modifiziert`  | datetime                    | Ja      | Fundstellen-Datum                                                                                                                                                                                                                   |
| `zp_referenz`     | datetime                    | Ja      | Fundstellen-Datum                                                                                                                                                                                                                   |
| `link`            | URI                         | Ja      |                                                                                                                                                                                                                                     |
| `autoren`         | list[Autor]                 | Ja      | Aus Fundstelle-Text extrahiert; sonst Ausschuss, sonst ausstellendes Organ (Landtag bzw. Landesregierung, DD-042), sonst Fallback auf `Initiative`-Feld.                                                                            |
| `drucksnr`        | string                      | Nein    |                                                                                                                                                                                                                                     |
| `zusammenfassung` | list[Zusammenfassungstupel] | Nein    | LLM-generiert via `bawue_dok.py`, typisiertes Tupel `[(full-llm, …)]` (150–250 Worte, DD-054); bei Entwurf/Beschlussempfehlung zusätzlich `intention-llm`, `regelungsinhalt-llm`, `kosten-llm` (DD-056). Leer wenn LLM deaktiviert. |
| `schlagworte`     | list[string]                | Nein    | LLM-generiert via `bawue_dok.py`. Leer wenn LLM deaktiviert.                                                                                                                                                                        |
| `kurztitel`       | string                      | Nein    | LLM-generiert via `bawue_dok.py` (einfache Sprache). Leer wenn LLM deaktiviert.                                                                                                                                                     |
| `meinung`         | integer (1–5)               | Nein    | LLM-generiert via `bawue_dok.py`, nur für Stellungnahmen und Beschlussempfehlungen. 1=ablehnend, 5=zustimmend.                                                                                                                      |

### Sitzung

| Feld      | Typ       | Pflicht | BaWue-Hinweise                                                                                                |
|-----------|-----------|---------|---------------------------------------------------------------------------------------------------------------|
| `termin`  | datetime  | Ja      |                                                                                                               |
| `gremium` | Gremium   | Ja      |                                                                                                               |
| `nummer`  | integer   | Ja      | Plenarsitzungen: aus SUMMARY extrahiert (`"142. Sitzung"` → `142`). Ausschüsse: `0`. DoD-Gap bei Ausschüssen. |
| `tops`    | list[Top] | Ja      | Aktuell `[]` — TOP-Scraping via PDF noch nicht implementiert. DoD-Gap: Phase 3 offen.                         |
| `public`  | boolean   | Ja      |                                                                                                               |

### Gremium

| Feld          | Typ       | Pflicht | BaWue-Hinweise                                                                                         |
|---------------|-----------|---------|--------------------------------------------------------------------------------------------------------|
| `parlament`   | Parlament | Ja      | `Parlament.BW` (Enum, kein String)                                                                     |
| `name`        | string    | Ja      | Ausschussname, `"gesetzesblatt"` bei `postparl-gsblt`, sonst `"plenum"` (reservierte Namen, s. DD-021) |
| `wahlperiode` | integer   | Ja      |                                                                                                        |

### Autor / Top / Lobbyregistereintrag

**Autor:** `organisation` (Pflicht), optional: `person`, `fachgebiet`

**Top:** `nummer` + `titel` (Pflicht), optional: `vorgang_id`, `dokumente`

**Lobbyregistereintrag:** `organisation`, `interne_id`, `intention`, `link`, `betroffene_drucksachen` (alle Pflicht)

## Enumerationen

Enum-Member ersetzen den Bindestrich durch `_` (z.B. `Stationstyp.PARL_VOLLVLSGN` für `parl-vollvlsgn`).
Die Tabellen unten zeigen nur die Werte, die BaWue tatsächlich erzeugt (`enum_mapper.py`); die
volle Werteliste pro Enum gibt die OpenAPI-Spezifikation (`pazufa_corelib.api_client.models`) vor —
spec 0.2.7 kennt zusätzlich `parl-antragsst`, `postparl-vgstp`, `parl-vermittas`, `preparl-formvs`
(Stationstyp), die Bundes-Vorgangstypen `bu-*` (Vorgangstyp) und `eckpunktepapier` (Doktyp), die
BaWue bewusst nicht abbildet (DD-057).

### Stationstypen

| Wert             | Python-Enum-Member           | Bedeutung                                                   |
|------------------|------------------------------|-------------------------------------------------------------|
| `preparl-regent` | `Stationstyp.PREPARL_REGENT` | Regierungsentwürfe (Beteiligungsportal)                     |
| `preparl-regbsl` | `Stationstyp.PREPARL_REGBSL` | Kabinettsbeschlüsse (PARLIS: Gesetzentwurf Landesregierung) |
| `parl-initiativ` | `Stationstyp.PARL_INITIATIV` | Gesetzentwürfe, Anträge aus dem Landtag                     |
| `parl-ausschber` | `Stationstyp.PARL_AUSSCHBER` | Beratung in Fachausschüssen                                 |
| `parl-vollvlsgn` | `Stationstyp.PARL_VOLLVLSGN` | Lesungen im Plenum                                          |
| `parl-akzeptanz` | `Stationstyp.PARL_AKZEPTANZ` | Verabschiedung durch den Landtag                            |
| `parl-ablehnung` | `Stationstyp.PARL_ABLEHNUNG` | Ablehnung durch den Landtag                                 |
| `postparl-gsblt` | `Stationstyp.POSTPARL_GSBLT` | Verkündung im Gesetzblatt                                   |
| `postparl-kraft` | `Stationstyp.POSTPARL_KRAFT` | Gesetz tritt in Kraft                                       |
| `sonstig`        | `Stationstyp.SONSTIG`        | Andere Stationen                                            |

### Vorgangstypen

| Wert           | Python-Enum-Member         | Beschreibung                                                |
|----------------|----------------------------|-------------------------------------------------------------|
| `gg-land-parl` | `Vorgangstyp.GG_LAND_PARL` | Landesgesetz (parlamentarisch, inkl. Haushaltsgesetzgebung) |
| `gg-land-volk` | `Vorgangstyp.GG_LAND_VOLK` | Volksantrag                                                 |
| `sonstig`      | `Vorgangstyp.SONSTIG`      | Sonstiges (alle anderen PARLIS-Vorgangstypen)               |

### Dokumententypen

| Wert              | Python-Enum-Member       | Beschreibung                                                             |
|-------------------|--------------------------|--------------------------------------------------------------------------|
| `preparl-entwurf` | `Doktyp.PREPARL_ENTWURF` | Vorparlamentarischer Entwurf                                             |
| `entwurf`         | `Doktyp.ENTWURF`         | Gesetzentwurf                                                            |
| `antrag`          | `Doktyp.ANTRAG`          | Antrag                                                                   |
| `anfrage`         | `Doktyp.ANFRAGE`         | Anfrage                                                                  |
| `antwort`         | `Doktyp.ANTWORT`         | Antwort                                                                  |
| `mitteilung`      | `Doktyp.MITTEILUNG`      | Mitteilung (Bekanntmachung; Gesetzblatt-Verlautbarung ohne Gesetzestext) |
| `gesetz`          | `Doktyp.GESETZ`          | Verkündetes Gesetz (Gesetzblatt/Gesetz-Fundstelle, DD-051)               |
| `beschlussempf`   | `Doktyp.BESCHLUSSEMPF`   | Beschlussempfehlung                                                      |
| `stellungnahme`   | `Doktyp.STELLUNGNAHME`   | Stellungnahme                                                            |
| `gutachten`       | `Doktyp.GUTACHTEN`       | Gutachten                                                                |
| `redeprotokoll`   | `Doktyp.REDEPROTOKOLL`   | Redeprotokoll                                                            |
| `tops`            | `Doktyp.TOPS`            | Tagesordnung                                                             |
| `tops-aend`       | `Doktyp.TOPS_AEND`       | Tagesordnungsänderung                                                    |
| `tops-ergz`       | `Doktyp.TOPS_ERGZ`       | Tagesordnungsergänzung                                                   |
| `sonstig`         | `Doktyp.SONSTIG`         | Sonstiges                                                                |

## Datenquellen

> Der Landtag BaWue bietet **keine offizielle API** und keine Open-Data-Schnittstelle.

| Quelle                   | Typ                                     | Priorität | Liefert                                                                                                                 |
|--------------------------|-----------------------------------------|-----------|-------------------------------------------------------------------------------------------------------------------------|
| **PARLIS JSON-Endpunkt** | Undokumentierte API                     | Primär    | `Vorgang` + `Station` (Gesetzgebungsvorgänge)                                                                           |
| **landtag-bw.de PDFs**   | Download + Textextraktion               | Primär    | `Dokument` (via `bawue_dok.py` bei aktivem LLM)                                                                         |
| **ICS-Kalender**         | ICS-Feed                                | Primär    | `Sitzung` (implementiert in `BawueSitzungenScraper`)                                                                    |
| **Beteiligungsportal**   | Web-Scraping                            | Ergänzend | `preparl-regent`-Stationen, vorparlamentarische Entwürfe                                                                |
| **Gesetzblatt BaWue**    | Datums-Lookup (`GesetzblattDateLookup`) | Ergänzend | Ausgabedatum für bestehende `postparl-gsblt`-Stationen des PARLIS-Vorgangs (DD-047); keine eigenständige Vorgangsquelle |
| **Kabinettsberichte**    | Web-Scraping (Fließtext)                | Optional  | Signalquelle für neue Vorgänge vom Typ `preparl-regbsl`                                                                 |
| **LLM-Provider**         | API (litellm)                           | Optional  | `Dokument`-Metadaten: `zusammenfassung`, `schlagworte`, `kurztitel`, `meinung` (via `bawue_dok.py`)                     |

### PARLIS (Primärquelle)

`parlis.landtag-bw.de` — undokumentiert, aber produktiv genutzt
von [dokukratie (OKF)](https://github.com/okfde/dokukratie/blob/main/dokukratie/bw.yml).
PARLIS bettet strukturierte JSON-Objekte in HTML-Kommentare ein (`<!--{...}-->`). Diese enthalten Felder mit stabilen
Feldcodes (z.B. `EWBV10` für Titel, `WMV35` für Fundstellen). Der Parser nutzt diese primär; das HTML/XPath-Parsing
dient als Fallback (DD-014).

| Endpunkt                                             | Methode     | Funktion                                              |
|------------------------------------------------------|-------------|-------------------------------------------------------|
| `https://parlis.landtag-bw.de/parlis/browse.tt.json` | POST (JSON) | Suche, liefert `report_id` + `item_count`             |
| `https://parlis.landtag-bw.de/parlis/report.tt.html` | GET         | Paginierte Ergebnisse (HTML) via `report_id`, `start` |

Implementierungsdetails: siehe [architecture.md](architecture.md).

### Beteiligungsportal

[beteiligungsportal.baden-wuerttemberg.de](https://beteiligungsportal.baden-wuerttemberg.de/de/mitmachen/lp-17) — deckt
die vorparlamentarische Phase ab (Gesetzentwürfe vor Landtag-Einbringung, Stellungnahmen). Nur ausgewählte Vorhaben,
HTML-Scraping erforderlich.

### Landtag-Website (landtag-bw.de)

| Bereich          | URL-Pfad                         |
|------------------|----------------------------------|
| Drucksachen      | `/de/dokumente/drucksachen`      |
| Plenarprotokolle | `/de/dokumente/plenarprotokolle` |
| ICS-Kalender     | `terminkalender.ics`             |

PDFs mit Blob-IDs (`/resource/blob/{id}/...`). Kein REST-API, kein RSS-Feed.

## Konfiguration

4-Tier: Defaults → `config.toml` → Umgebungsvariablen → CLI-Argumente.

| Sektion         | Schlüssel                | Standard                                                   | Pflicht | Beschreibung                                                                                         |
|-----------------|--------------------------|------------------------------------------------------------|---------|------------------------------------------------------------------------------------------------------|
| `[backend]`     | `ltzf-api-url`           |                                                            | Ja      | URL des PaZuFa-Backends                                                                              |
| `[backend]`     | `ltzf-api-key`           |                                                            | Ja      | API-Key (Scope: collector)                                                                           |
| `[main]`        | `collector-uuid`         |                                                            | Ja      | Eindeutige Collector-ID                                                                              |
| `[scrapers]`    | `scraper-dir`            |                                                            | Ja      | Verzeichnis mit Scraper-Modulen                                                                      |
| `[cache]`       | `redis-host`             |                                                            | Nein    | Redis-Host                                                                                           |
| `[cache]`       | `redis-port`             | 6379                                                       | Nein    | Redis-Port                                                                                           |
| `[llm]`         | `provider-key`           |                                                            | Nein    | API-Key für LLM-Provider (via `LLM_PROVIDER_KEY` Umgebungsvariable)                                  |
| `[llm]`         | `model`                  | *(gpt-5-nano)*                                             | Nein    | LLM-Modellname (z.B. `gpt-5-nano`, `gpt-4.1-nano`)                                                   |
| `[bawue]`       | `enabled-vorgangstypen`  | `["Gesetzgebung", "Haushaltsgesetzgebung", "Volksantrag"]` | Nein    | PARLIS-Vorgangstypen die gescrapt werden (`listing_urls` der Pipeline)                               |
| `[bawue]`       | `wahlperiode`            | 17                                                         | Nein    | Wahlperiode; Prod/Sample-Default 17, Staging/Dev bereits 18 (s. #63)                                 |
| `[bawue]`       | `parlis-request-delay-s` | 1.0                                                        | Nein    | Verzögerung zwischen PARLIS-Anfragen (s)                                                             |
| `[bawue]`       | `wahlperiode-start-date` | `"2021-04-26"`                                             | Nein    | Startdatum der Wahlperiode (Suchbereich)                                                             |
| `[bawue]`       | `ics-url`                | *(landtag-bw.de)*                                          | Nein    | ICS-Kalender-Feed für Sitzungen                                                                      |
| `[beteiligung]` | `wahlperiode`            | 17                                                         | Nein    | Wahlperiode für Beteiligungsportal-Index                                                             |
| `[beteiligung]` | `request-delay-s`        | 2.0                                                        | Nein    | Verzögerung zwischen Anfragen (s)                                                                    |
| `[gesetzblatt]` | `request-delay-s`        | 1.0                                                        | Nein    | Verzögerung zwischen Gesetzblatt-Anfragen (s), s. [gesetzblatt_scraping.md](gesetzblatt_scraping.md) |

## Generelle Anforderungen

Das Projekt erfüllt die Community-[Definition of Done](https://wiki.pazufa.de/books/scraper/page/definition-of-done).
Konkret:

### Core Completion

1. **Vorgänge + Sitzungen** im PaZuFa-Format erzeugen (alle drei Scraper)
2. **Einlieferung** ans Backend (über `bawue.api`)
3. **Automatisierung** (Cloud-Run-Job mit Cloud Scheduler; lokal Docker-Compose mit `CYCLE_TIME_S`)
4. **Abdeckung der aktuellen Wahlperiode** (Prod/Sample-Default `wahlperiode = 17`,
   `wahlperiode-start-date = 2021-04-26`; Landtag ist seit 2026-05-01 in WP 18 — s. #63)

### Coding-Regeln

5. **Idempotenz:** Wiederholtes Ausführen erzeugt keine Duplikate (Scraper + Backend)
6. **Fehlertoleranz:** Einzelne fehlgeschlagene Vorgänge stoppen den Scraper nicht (`bawue.pipeline`)
7. **Determinismus:** Gleicher Input erzeugt identisches API-Mapping (Ausnahme: LLM-Ausgaben)
8. **Fail-loud:** Fehlende/nicht-parsbare Pflichtfelder werden geloggt; `None`/Weglassen vor leeren Defaults
9. **Kanonische Namen:** Einheitliche Autoren-/Organisationsnamen (Fraktionen, Institutionen)
10. **Reservierte Entity-Namen:** `regierung`, `gesetzesblatt`, `plenum` entsprechend der Spezifikation verwenden
11. **Präzise Gremium-Namen** statt generischer Bezeichnungen

### Betrieb

12. **Rate-Limiting:** Konfigurierbare Verzögerung (`[bawue]`, `[beteiligung]`)
13. **Volltext-Extraktion:** `bawue_dok.py` + kreuzberg (bei aktivem LLM)
14. **Enum-Mapping:** `enum_mapper.py` mit `sonstig` als dokumentiertem Fallback
15. **Logging:** Strukturiertes JSON-Logging für Debugging und Monitoring
16. **Konfigurierbarkeit:** 4-Tier (Defaults → `config.toml` → ENV → CLI)
17. **Caching:** Redis (2-Wochen-TTL) + LLM-Hash-Cache zur Schonung von Ressourcen/Tokens
18. **LLM-Anreicherung (optional):** Bei konfiguriertem `[llm]`-Abschnitt extrahiert `bawue_dok.py` semantische
    Metadaten (Zusammenfassung, Schlagworte, Kurztitel, Meinung) aus Dokumenten. 3-stufige Degradation: Voll (PDF+LLM) →
    Text-only (PDF ok, LLM fehlt) → Metadaten-only (PDF-Download fehlgeschlagen). Aktivierung via `LLM_PROVIDER_KEY`
    Umgebungsvariable.

### Qualitätssicherung

19. **Automatisierte Tests:** Unit + Integration (pytest; aktueller Stand via `make test` / CI, kein
    fixer Zähler hier — der geht schnell stale)
20. **CI:** Trivy-Dependency-Scan → ruff-Linting + ruff-Formatter → pytest (GitHub Actions, `.github/workflows/ci.yml`)
21. **Testabdeckung aller Stationstypen:** mindestens ein Test pro auftretendem Stationstyp (siehe
    `test_enum_mapper.py`)
22. **Drift-Detection:** `verify_fulltext.py`, `wahlperiode_check.py` erkennen zerbrochenes Parsing
23. **Dokumentation:** Design-Entscheidungen in [design_decisions.md](design_decisions.md) (fortlaufend
    nummeriert, siehe Index dort) und im Wiki-Chapter
24. **Scope:** Ausschließlich Gesetzgebungsvorgänge des Landes BaWü (`enabled-vorgangstypen` standardmäßig restriktiv)
