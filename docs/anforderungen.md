# Anforderungen: BaWue-Scraper für den Parlamentszusammenfasser

## Übersicht

Collector-Plugin für den **Baden-Württembergischen Landtag** (`BW`) im
[PaZuFa](https://codeberg.org/PaZuFa/parlamentszusammenfasser)-System. Implementiert `VorgangsScraper` und
`SitzungsScraper` des [pazufa-collector](https://codeberg.org/PaZuFa/pazufa-collector)-Frameworks. Das Framework
übernimmt Scheduling, Redis-Caching, API-Einlieferung und Dokumentenpipeline (PDF/OCR/LLM).

## Identifikatoren

| Identifikator         | Format    | Beispiel       | Quelle                                                      |
|-----------------------|-----------|----------------|-------------------------------------------------------------|
| Vorgangsnummer        | `V-XXXXX` | `V-42771`      | PARLIS HTML / JS                                            |
| Drucksachennummer     | `WP/NR`   | `17/10266`     | Fundstellen-Regex                                           |
| Plenarprotokollnummer | `WP/NR`   | `17/141`       | Fundstellen-Regex                                           |
| API-ID                | UUID v5   | `550e8400-...` | Vom Scraper generiert (`uuid5(NAMESPACE_URL, vorgangs_id)`) |

## Backend-API

Datenlieferung erfolgt vollständig durch das Framework. Der Scraper erzeugt nur `Vorgang`- und `Sitzung`-Objekte.

**Schreib-Endpunkte (Framework, Scope `collector`):**

| Endpunkt                               | Methode | Beschreibung                                                |
|----------------------------------------|---------|-------------------------------------------------------------|
| `/api/v2/vorgang`                      | PUT     | Vorgang einliefern (idempotent, Backend übernimmt Merging)  |
| `/api/v2/kalender/{parlament}/{datum}` | PUT     | Sitzungen für ein Datum setzen (max. 1 Tag in der Zukunft) |

**Authentifizierung:** Header `X-API-Key`, 64-Zeichen-Key mit Präfix `ltzf_`.
Konfiguration via `config.toml` (`[backend] ltzf-api-key`) oder `LTZF_API_KEY`.

**Deduplizierung:** PUT-Requests sind idempotent. Backend übernimmt Merging. Framework cacht via Redis (2-Wochen-TTL).
Der Scraper muss sich **nicht** um Deduplizierung kümmern, soll aber einheitliche Autoren-/Organisationsnamen liefern.

## Datenmodelle

Modelle werden automatisch aus der OpenAPI-Spezifikation generiert (`openapi-client`). Keine manuellen Pydantic-Modelle.

### Vorgang

| Feld                  | Typ           | Pflicht | BaWue-Hinweise                                                     |
|-----------------------|---------------|---------|--------------------------------------------------------------------|
| `api_id`              | UUID          | Ja      | `uuid5(NAMESPACE_URL, vorgangs_id)`                                |
| `titel`               | string        | Ja      |                                                                    |
| `typ`                 | Vorgangstyp   | Ja      |                                                                    |
| `wahlperiode`         | integer       | Ja      | Aktuell: 17                                                        |
| `verfassungsaendernd` | boolean       | Ja      | Immer `false` (PARLIS liefert diese Information nicht)             |
| `initiatoren`         | list[Autor]   | Ja      |                                                                    |
| `stationen`           | list[Station] | Ja      |                                                                    |
| `kurztitel`           | string        | Nein    |                                                                    |
| `ids`                 | list[VgIdent] | Nein    | Enthält `VgIdent(id=vorgangs_id, typ=VgIdentTyp.VORGNR)`          |
| `lobbyregister`       | list[...]     | Nein    |                                                                    |

### Station

| Feld        | Typ                         | Pflicht | BaWue-Hinweise                                                        |
|-------------|-----------------------------|---------|-----------------------------------------------------------------------|
| `typ`       | Stationstyp                 | Ja      |                                                                       |
| `dokumente` | list[StationDokumenteInner] | Ja      | `StationDokumenteInner`-Wrapper (Union-Typ aus OpenAPI-Spec)          |
| `zp_start`  | datetime                    | Ja      |                                                                       |
| `gremium`   | Gremium                     | Ja      | Aus PARLIS-Fundstellen abgeleitet — siehe [architecture.md](architecture.md) |
| `titel`     | string                      | Nein    |                                                                       |
| `schlagworte` | list[string]              | Nein    |                                                                       |
| `trojanergefahr` | integer (1–10)         | Nein    |                                                                       |

### Dokument

| Feld             | Typ         | Pflicht | BaWue-Hinweise                                                          |
|------------------|-------------|---------|-------------------------------------------------------------------------|
| `titel`          | string      | Ja      |                                                                         |
| `volltext`       | string      | Ja      | Initial leer — Framework füllt via Dokumentpipeline                    |
| `hash`           | string      | Ja      | Initial leer — Framework füllt via Dokumentpipeline                    |
| `typ`            | Doktyp      | Ja      |                                                                         |
| `zp_modifiziert` | datetime    | Ja      | Fundstellen-Datum                                                       |
| `zp_referenz`    | datetime    | Ja      | Fundstellen-Datum                                                       |
| `link`           | URI         | Ja      |                                                                         |
| `autoren`        | list[Autor] | Ja      | Aus Fundstelle-Text extrahiert; Fallback auf `Initiative`-Feld. Ausschuss- und Plenarprotokoll-Fundstellen ausgenommen. |
| `drucksnr`       | string      | Nein    |                                                                         |
| `zusammenfassung` | string     | Nein    |                                                                         |

### Sitzung

| Feld      | Typ       | Pflicht | BaWue-Hinweise                                                                                  |
|-----------|-----------|---------|-------------------------------------------------------------------------------------------------|
| `termin`  | datetime  | Ja      |                                                                                                 |
| `gremium` | Gremium   | Ja      |                                                                                                 |
| `nummer`  | integer   | Ja      | Plenarsitzungen: aus SUMMARY extrahiert (`"142. Sitzung"` → `142`). Ausschüsse: `0`.          |
| `tops`    | list[Top] | Ja      | Aktuell `[]` — TOP-Scraping via PDF noch nicht implementiert                                   |
| `public`  | boolean   | Ja      |                                                                                                 |

### Gremium

| Feld          | Typ       | Pflicht | BaWue-Hinweise                                                                      |
|---------------|-----------|---------|-------------------------------------------------------------------------------------|
| `parlament`   | Parlament | Ja      | `Parlament.BW` (Enum, kein String)                                                  |
| `name`        | string    | Ja      | Ausschussname, `"Plenum"` bei Plenarprotokollen, `"Landtag"` als Default            |
| `wahlperiode` | integer   | Ja      |                                                                                     |

### Autor / Top / Lobbyregistereintrag

**Autor:** `organisation` (Pflicht), optional: `person`, `fachgebiet`

**Top:** `nummer` + `titel` (Pflicht), optional: `vorgang_id`, `dokumente`

**Lobbyregistereintrag:** `organisation`, `interne_id`, `intention`, `link`, `betroffene_drucksachen` (alle Pflicht)

## Enumerationen

Enum-Member verwenden die `MINUS`-Namenskonvention (z.B. `Stationstyp.PARL_MINUS_VOLLVLSGN` für `parl-vollvlsgn`).

### Stationstypen

| Wert             | Python-Enum-Member                 | Bedeutung                               |
|------------------|------------------------------------|-----------------------------------------|
| `preparl-regent` | `Stationstyp.PREPARL_MINUS_REGENT` | Gesetzentwürfe der Landesregierung      |
| `preparl-regbsl` | `Stationstyp.PREPARL_MINUS_REGBSL` | Kabinettsbeschlüsse                     |
| `parl-initiativ` | `Stationstyp.PARL_MINUS_INITIATIV` | Gesetzentwürfe, Anträge aus dem Landtag |
| `parl-ausschber` | `Stationstyp.PARL_MINUS_AUSSCHBER` | Beratung in Fachausschüssen             |
| `parl-vollvlsgn` | `Stationstyp.PARL_MINUS_VOLLVLSGN` | Lesungen im Plenum                      |
| `parl-akzeptanz` | `Stationstyp.PARL_MINUS_AKZEPTANZ` | Verabschiedung durch den Landtag        |
| `parl-ablehnung` | `Stationstyp.PARL_MINUS_ABLEHNUNG` | Ablehnung durch den Landtag             |
| `postparl-vesja` | `Stationstyp.POSTPARL_MINUS_VESJA` | Unterschrift durch Ministerpräsident    |
| `postparl-gsblt` | `Stationstyp.POSTPARL_MINUS_GSBLT` | Verkündung im Gesetzblatt               |
| `postparl-kraft` | `Stationstyp.POSTPARL_MINUS_KRAFT` | Gesetz tritt in Kraft                   |
| `sonstig`        | `Stationstyp.SONSTIG`              | Andere Stationen                        |

### Vorgangstypen

| Wert           | Python-Enum-Member                     | Beschreibung                   |
|----------------|----------------------------------------|--------------------------------|
| `gg-land-parl` | `Vorgangstyp.GG_MINUS_LAND_MINUS_PARL` | Landesgesetz (parlamentarisch) |
| `bw-einsatz`   | `Vorgangstyp.BW_MINUS_EINSATZ`         | BaWue-spezifisch               |
| `sonstig`      | `Vorgangstyp.SONSTIG`                  | Sonstiges                      |

### Dokumententypen

| Wert              | Python-Enum-Member             | Beschreibung                 |
|-------------------|--------------------------------|------------------------------|
| `preparl-entwurf` | `Doktyp.PREPARL_MINUS_ENTWURF` | Vorparlamentarischer Entwurf |
| `entwurf`         | `Doktyp.ENTWURF`               | Gesetzentwurf                |
| `antrag`          | `Doktyp.ANTRAG`                | Antrag                       |
| `anfrage`         | `Doktyp.ANFRAGE`               | Anfrage                      |
| `antwort`         | `Doktyp.ANTWORT`               | Antwort                      |
| `mitteilung`      | `Doktyp.MITTEILUNG`            | Mitteilung                   |
| `beschlussempf`   | `Doktyp.BESCHLUSSEMPF`         | Beschlussempfehlung          |
| `stellungnahme`   | `Doktyp.STELLUNGNAHME`         | Stellungnahme                |
| `gutachten`       | `Doktyp.GUTACHTEN`             | Gutachten                    |
| `redeprotokoll`   | `Doktyp.REDEPROTOKOLL`         | Redeprotokoll                |
| `tops`            | `Doktyp.TOPS`                  | Tagesordnung                 |
| `tops-aend`       | `Doktyp.TOPS_MINUS_AEND`       | Tagesordnungsänderung        |
| `tops-ergz`       | `Doktyp.TOPS_MINUS_ERGZ`       | Tagesordnungsergänzung       |
| `sonstig`         | `Doktyp.SONSTIG`               | Sonstiges                    |

## Datenquellen

> Der Landtag BaWue bietet **keine offizielle API** und keine Open-Data-Schnittstelle.

| Quelle                   | Typ                       | Priorität  | Liefert                                                         |
|--------------------------|---------------------------|------------|-----------------------------------------------------------------|
| **PARLIS JSON-Endpunkt** | Undokumentierte API       | Primär     | `Vorgang` + `Station` (Gesetzgebungsvorgänge)                   |
| **landtag-bw.de PDFs**   | Download + Textextraktion | Primär     | `Dokument` (via Framework-Dokumentpipeline)                     |
| **ICS-Kalender**         | ICS-Feed                  | Primär     | `Sitzung` (implementiert in `BawueSitzungenScraper`)            |
| **Beteiligungsportal**   | Web-Scraping              | Ergänzend  | `preparl-regent`-Stationen, vorparlamentarische Entwürfe        |
| **Gesetzblatt BaWue**    | Web-Suche                 | Ergänzend  | `postparl-gsblt`-Stationen                                      |
| **Kabinettsberichte**    | Web-Scraping (Fließtext)  | Optional   | Signalquelle für neue Vorgänge vom Typ `preparl-regbsl`         |

### PARLIS (Primärquelle)

`parlis.landtag-bw.de` — undokumentiert, aber produktiv genutzt von [dokukratie (OKF)](https://github.com/okfde/dokukratie/blob/main/dokukratie/bw.yml).

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

| Sektion         | Schlüssel                | Standard          | Pflicht | Beschreibung                               |
|-----------------|--------------------------|-------------------|---------|--------------------------------------------|
| `[backend]`     | `ltzf-api-url`           |                   | Ja      | URL des PaZuFa-Backends                    |
| `[backend]`     | `ltzf-api-key`           |                   | Ja      | API-Key (Scope: collector)                 |
| `[main]`        | `collector-uuid`         |                   | Ja      | Eindeutige Collector-ID                    |
| `[scrapers]`    | `scraper-dir`            |                   | Ja      | Verzeichnis mit Scraper-Modulen            |
| `[cache]`       | `redis-host`             |                   | Nein    | Redis-Host                                 |
| `[cache]`       | `redis-port`             | 6379              | Nein    | Redis-Port                                 |
| `[llm]`         | `openai-api-key`         |                   | Nein    | API-Key für LLM-Zusammenfassungen          |
| `[bawue]`       | `wahlperiode`            | 17                | Nein    | Aktuelle Wahlperiode                       |
| `[bawue]`       | `parlis-request-delay-s` | 1.0               | Nein    | Verzögerung zwischen PARLIS-Anfragen (s)   |
| `[bawue]`       | `wahlperiode-start-date` | `"2021-04-26"`    | Nein    | Startdatum der Wahlperiode (Suchbereich)   |
| `[bawue]`       | `ics-url`                | *(landtag-bw.de)* | Nein    | ICS-Kalender-Feed für Sitzungen            |
| `[beteiligung]` | `wahlperiode`            | 17                | Nein    | Wahlperiode für Beteiligungsportal-Index   |
| `[beteiligung]` | `request-delay-s`        | 2.0               | Nein    | Verzögerung zwischen Anfragen (s)          |

## Generelle Anforderungen

1. **Idempotenz:** Wiederholtes Ausführen darf keine Duplikate erzeugen (Framework + Backend)
2. **Fehlertoleranz:** Einzelne fehlgeschlagene Vorgänge dürfen nicht den gesamten Scraper stoppen (Framework)
3. **Rate-Limiting:** Respektierung der Landtags-Website (konfigurierbare Verzögerung in `[bawue]`)
4. **Volltext-Extraktion:** PDFs werden über die Framework-Dokumentpipeline verarbeitet
5. **Korrekte Zuordnung:** Enum-Mapping via `enum_mapper.py` mit `sonstig` als Fallback
6. **Logging:** Nachvollziehbare Logs für Debugging und Monitoring
7. **Konfigurierbarkeit:** Alle Einstellungen über `config.toml` / Umgebungsvariablen (4-Tier)
