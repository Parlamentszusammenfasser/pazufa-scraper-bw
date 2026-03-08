# Anforderungen: BaWue-Scraper für den Parlamentszusammenfasser

## Übersicht

Der Parlamentszusammenfasser (PaZuFa) ist ein Transparenz-Tool, das Informationen zu Landesgesetzen in Deutschland
automatisch sammelt, verarbeitet und darstellt. Das System besteht aus drei unabhängigen Komponenten:

1. **Backend/Datenbank** — Zentrale Datenverwaltung und API (Rust/PostgreSQL)
2. **Website(s)** — Frontend zur Darstellung für Nutzer
3. **Collectors/Scraper** — Datensammler für verschiedene Landtage, die auf dem
   [pazufa-collector](https://codeberg.org/PaZuFa/pazufa-collector) Framework aufbauen

Dieser Scraper ist ein Collector-Plugin für den **Baden-Württembergischen Landtag** (Parlamentscode: `BW`). Er
implementiert die `VorgangsScraper`-Basisklasse des pazufa-collector Frameworks und wird von diesem automatisch
entdeckt und orchestriert.

> **Hinweis:** Das Projekt wurde von GitHub auf [Codeberg](https://codeberg.org/PaZuFa) migriert. Die alten
> GitHub-Repositories sind archiviert.

## Referenzen

| Ressource                        | Link                                                                                                                  |
|----------------------------------|-----------------------------------------------------------------------------------------------------------------------|
| PaZuFa Hauptprojekt              | [Codeberg](https://codeberg.org/PaZuFa/parlamentszusammenfasser)                                                      |
| PaZuFa Dokumentation             | [docs/README.md](https://codeberg.org/PaZuFa/parlamentszusammenfasser/src/branch/main/docs/README.md)                 |
| OpenAPI-Spezifikation            | [docs/specs/openapi.yml](https://codeberg.org/PaZuFa/parlamentszusammenfasser/src/branch/main/docs/specs/openapi.yml) |
| Authentifizierung                | [docs/authentication.md](https://codeberg.org/PaZuFa/parlamentszusammenfasser/src/branch/main/docs/authentication.md) |
| pazufa-collector Framework       | [pazufa-collector](https://codeberg.org/PaZuFa/pazufa-collector)                                                      |
| Backend                          | [pazufa-backend](https://codeberg.org/PaZuFa/pazufa-backend)                                                          |
| CONTRIBUTING                     | [CONTRIBUTING.md](https://codeberg.org/PaZuFa/parlamentszusammenfasser/src/branch/main/CONTRIBUTING.md)               |
| SETUP                            | [SETUP.md](https://codeberg.org/PaZuFa/parlamentszusammenfasser/src/branch/main/SETUP.md)                             |
| Landtag BaWue                    | [landtag-bw.de](https://www.landtag-bw.de/)                                                                           |
| BaWue Parlamentsdokumentation    | [Dokumente](https://www.landtag-bw.de/de/Dokumente)                                                                   |

## Datenlieferung an das PaZuFa-Backend

Die Datenlieferung wird vollständig vom pazufa-collector Framework übernommen. Der BaWue-Scraper erzeugt `Vorgang`-
Objekte aus den auto-generierten OpenAPI-Modellen; das Framework übernimmt Caching (Redis), Deduplizierung und
API-Einlieferung.

### API-Endpunkte (v2)

**Schreib-Endpunkte (vom Framework genutzt, benötigen Authentifizierung mit Scope `collector`):**

| Endpunkt                               | Methode | Beschreibung                                                                     |
|----------------------------------------|---------|----------------------------------------------------------------------------------|
| `/api/v2/vorgang`                      | PUT     | Neuen Gesetzgebungsvorgang einliefern (mit automatischer Deduplizierung/Merging) |
| `/api/v2/kalender/{parlament}/{datum}` | PUT     | Sitzungen für ein Datum setzen (nur bis 1 Tag nach aktuellem Zeitstempel)        |

**Lese-Endpunkte (ohne Authentifizierung):**

| Endpunkt                               | Methode | Beschreibung                                 |
|----------------------------------------|---------|----------------------------------------------|
| `/api/v2/vorgang`                      | GET     | Filterbare Liste von Vorgängen abrufen       |
| `/api/v2/vorgang/{vorgang_id}`         | GET     | Einzelnen Vorgang per UUID abrufen           |
| `/api/v2/sitzung`                      | GET     | Filterbare Liste von Sitzungen abrufen       |
| `/api/v2/sitzung/{sid}`               | GET     | Einzelne Sitzung per UUID abrufen            |
| `/api/v2/kalender`                     | GET     | Filterbare Kalenderübersicht aller Sitzungen |
| `/api/v2/kalender/{parlament}/{datum}` | GET     | Sitzungen für ein Datum und Parlament        |
| `/api/v2/dokument/{api_id}`            | GET     | Einzelnes Dokument per UUID abrufen          |
| `/api/v2/gremien`                      | GET     | Filterbare Liste von Gremien/Ausschüssen     |
| `/api/v2/autoren`                      | GET     | Filterbare Liste von Autoren                 |
| `/api/v2/enumeration/{name}`           | GET     | Enumerationswerte abrufen                    |

### Authentifizierung

- **Header**: `X-API-Key`
- **Key-Format**: 64 Zeichen, Präfix `ltzf_`, gefolgt von alphanumerischen Zeichen (Groß-/Kleinschreibung relevant)
- **Scopes** (höhere Scopes schließen niedrigere ein):
    1. **KeyAdder** — Kann neue API-Keys erstellen und invalidieren
    2. **Admin** — Kann alle Vorgänge und Sitzungen direkt editieren/löschen
    3. **Collector** — Kann neue Vorgänge einliefern und Kalender-Einträge setzen
- API-Key-Konfiguration erfolgt über `config.toml` (`[backend] ltzf-api-key`) oder Umgebungsvariable `LTZF_API_KEY`
- Für Details
  siehe [authentication.md](https://codeberg.org/PaZuFa/parlamentszusammenfasser/src/branch/main/docs/authentication.md)

### Idempotenz & Deduplizierung

- PUT-Requests auf `/api/v2/vorgang` sind idempotent
- Das Backend übernimmt die Deduplizierung und das Merging (z.B. Autoren-Zusammenführung, Stations-Abgleich)
- Das Framework übernimmt Redis-basiertes Caching (2-Wochen-TTL) zur Vermeidung unnötiger API-Aufrufe
- Der Scraper muss sich NICHT um Deduplizierung kümmern, aber SOLL für einheitliche Autoren-/Organisationsnamen sorgen

## Benötigte Datenmodelle

Die Datenmodelle werden automatisch aus der OpenAPI-Spezifikation generiert (`openapi-client` Paket). Der BaWue-Scraper
verwendet die generierten Modelle direkt — es gibt keine hand-geschriebenen Pydantic-Modelle mehr.

### Vorgang (Gesetzgebungsvorgang)

Ein `Vorgang` repräsentiert den gesamten Weg eines Gesetzes durch das Parlament.

**Pflichtfelder:**

| Feld                  | Typ            | Beschreibung                          |
|-----------------------|----------------|---------------------------------------|
| `api_id`              | UUID           | Eindeutige ID (vom Scraper generiert) |
| `titel`               | string         | Vollständiger Titel des Vorgangs      |
| `typ`                 | Vorgangstyp    | Vorgangstyp (siehe Enumerationen)     |
| `wahlperiode`         | integer        | Wahlperiode (aktuell: 17)             |
| `verfassungsaendernd` | boolean        | Ob der Vorgang die Verfassung ändert  |
| `initiatoren`         | list[Autor]    | Initiatoren des Vorgangs              |
| `stationen`           | list[Station]  | Stationen des Gesetzgebungsverfahrens |

**Optionale Felder:** `kurztitel`, `ids` (list[VgIdent] — Vorgangsnummern etc.), `links`, `lobbyregister`
(list[Lobbyregeintrag])

**BaWue-Besonderheiten:**
- `api_id` wird als UUID v5 generiert: `uuid5(NAMESPACE_URL, vorgangs_id)`
- `ids` enthält einen `VgIdent` mit `id=vorgangs_id` und `typ=VgIdentTyp.VORGNR`
- `verfassungsaendernd` wird immer auf `false` gesetzt (PARLIS liefert diese Information nicht)

### Station (Verfahrensstadium)

Jeder Vorgang durchläuft mehrere Stationen.

**Pflichtfelder:**

| Feld        | Typ                         | Beschreibung                      |
|-------------|-------------------------------|-----------------------------------|
| `typ`       | Stationstyp                   | Stationstyp (siehe Enumerationen) |
| `dokumente` | list[StationDokumenteInner]   | Zugehörige Dokumente (Wrapper)    |
| `zp_start`  | datetime                      | Zeitpunkt des Stationsbeginns     |
| `gremium`   | Gremium                       | Zuständiges Gremium/Ausschuss     |

**Optionale Felder:** `titel`, `link`, `schlagworte`, `stellungnahmen`, `trojanergefahr` (1-10)

**BaWue-Besonderheiten:**
- `dokumente` verwendet den `StationDokumenteInner`-Wrapper (Union-Typ aus der OpenAPI-Spec)
- Stationen werden aus PARLIS-Fundstellen extrahiert (siehe [architecture.md](architecture.md))

### Dokument

**Pflichtfelder:**

| Feld             | Typ        | Beschreibung                        |
|------------------|------------|-------------------------------------|
| `titel`          | string     | Dokumententitel                     |
| `volltext`       | string     | Extrahierter Volltext               |
| `hash`           | string     | Hash des Dokuments                  |
| `typ`            | Doktyp     | Dokumententyp (siehe Enumerationen) |
| `zp_modifiziert` | datetime   | Letzte Änderung                     |
| `zp_referenz`    | datetime   | Referenzdatum                       |
| `link`           | URI        | Download-Link                       |
| `autoren`        | list[Autor]| Autoren des Dokuments               |

**Optionale Felder:** `drucksnr`, `kurztitel`, `vorwort`, `zusammenfassung`, `meinung` (1-5), `schlagworte`

**BaWue-Besonderheiten:**
- `volltext` und `hash` werden initial leer übergeben — das Framework füllt diese über die Dokumentpipeline
- `zp_modifiziert` und `zp_referenz` werden auf das Fundstellen-Datum gesetzt
- `autoren` wird aus dem Fundstelle-Text extrahiert (Lücke zwischen Stationstyp und Datum). Wenn kein Autor im
  Fundstelle-Text vorhanden ist, wird auf das `Initiative`-Feld des Vorgangs zurückgefallen. Ausschuss- und
  Plenarprotokoll-Fundstellen werden ausgenommen (deren Gap-Text enthält keine Autoren).

### Sitzung (Parlamentssitzung)

**Pflichtfelder:**

| Feld      | Typ        | Beschreibung        |
|-----------|------------|---------------------|
| `termin`  | datetime   | Sitzungstermin      |
| `gremium` | Gremium    | Ausschuss/Plenum    |
| `nummer`  | integer    | Sitzungsnummer      |
| `tops`    | list[Top]  | Tagesordnungspunkte |
| `public`  | boolean    | Öffentliche Sitzung |

> **Phase 1+2 implementiert:** `BawueSitzungenScraper` parst den ICS-Kalender-Feed von landtag-bw.de und erzeugt
> `Sitzung`-Modelle. `nummer` wird für Plenarsitzungen direkt aus dem SUMMARY-Feld extrahiert
> (z.B. `"Plenarsitzung: 142. Sitzung"` → `nummer=142`); Ausschusssitzungen behalten `nummer=0`.
> `tops=[]` bleibt Platzhalter — TOPs via PDF-Scraping ist noch nicht implementiert.

### Gremium (Ausschuss/Plenum)

**Pflichtfelder:**

| Feld          | Typ       | Beschreibung           |
|---------------|-----------|------------------------|
| `parlament`   | Parlament | `Parlament.BW`         |
| `name`        | string    | Name des Gremiums      |
| `wahlperiode` | integer   | Wahlperiode            |

**BaWue-Besonderheiten:**
- `parlament` verwendet den `Parlament`-Enum (nicht mehr ein String `"BW"`)
- Gremium wird aus PARLIS-Fundstellen abgeleitet: Ausschussname, "Plenum" bei Plenarprotokollen, oder "Landtag" als
  Default

### Autor

**Pflichtfelder:**

| Feld           | Typ    | Beschreibung                              |
|----------------|--------|-------------------------------------------|
| `organisation` | string | Organisation (z.B. Fraktion, Ministerium) |

**Optionale Felder:** `person`, `fachgebiet`, `lobbyregister`

### Top (Tagesordnungspunkt)

**Pflichtfelder:**

| Feld     | Typ    | Beschreibung                  |
|----------|--------|-------------------------------|
| `nummer` | string | TOP-Nummer                    |
| `titel`  | string | Titel des Tagesordnungspunkts |

**Optionale Felder:** `vorgang_id` (Verknüpfung zu Vorgängen), `dokumente`

### Lobbyregistereintrag

**Pflichtfelder:**

| Feld                     | Typ           | Beschreibung                    |
|--------------------------|---------------|---------------------------------|
| `organisation`           | string        | Name der Organisation           |
| `interne_id`             | string        | Interne ID im Lobbyregister     |
| `intention`              | string        | Intention/Ziel der Organisation |
| `link`                   | URI           | Link zum Lobbyregister-Eintrag  |
| `betroffene_drucksachen` | list[string]  | Betroffene Drucksachennummern   |

## Enumerationen

Alle Enum-Werte werden aus der OpenAPI-Spezifikation automatisch generiert. Die generierten Python-Enum-Member
verwenden die `MINUS`-Namenskonvention (z.B. `Stationstyp.PARL_MINUS_VOLLVLSGN` für den Wert `parl-vollvlsgn`).

### Stationstypen

| Wert             | Python-Enum-Member                   | BaWue-Relevanz                          |
|------------------|--------------------------------------|-----------------------------------------|
| `preparl-regent` | `Stationstyp.PREPARL_MINUS_REGENT`   | Gesetzentwürfe der Landesregierung      |
| `preparl-regbsl` | `Stationstyp.PREPARL_MINUS_REGBSL`   | Kabinettsbeschlüsse                     |
| `parl-initiativ` | `Stationstyp.PARL_MINUS_INITIATIV`   | Gesetzentwürfe, Anträge aus dem Landtag |
| `parl-ausschber` | `Stationstyp.PARL_MINUS_AUSSCHBER`   | Beratung in Fachausschüssen             |
| `parl-vollvlsgn` | `Stationstyp.PARL_MINUS_VOLLVLSGN`   | Lesungen im Plenum                      |
| `parl-akzeptanz` | `Stationstyp.PARL_MINUS_AKZEPTANZ`   | Verabschiedung durch den Landtag        |
| `parl-ablehnung` | `Stationstyp.PARL_MINUS_ABLEHNUNG`   | Ablehnung durch den Landtag             |
| `postparl-vesja` | `Stationstyp.POSTPARL_MINUS_VESJA`   | Unterschrift durch Ministerpräsident    |
| `postparl-gsblt` | `Stationstyp.POSTPARL_MINUS_GSBLT`   | Verkündung im Gesetzblatt               |
| `postparl-kraft` | `Stationstyp.POSTPARL_MINUS_KRAFT`   | Gesetz tritt in Kraft                   |
| `sonstig`        | `Stationstyp.SONSTIG`                | Andere Stationen                        |

### Vorgangstypen

| Wert           | Python-Enum-Member                      | Beschreibung                   |
|----------------|-----------------------------------------|--------------------------------|
| `gg-land-parl` | `Vorgangstyp.GG_MINUS_LAND_MINUS_PARL` | Landesgesetz (parlamentarisch) |
| `bw-einsatz`   | `Vorgangstyp.BW_MINUS_EINSATZ`         | BaWue-spezifisch               |
| `sonstig`      | `Vorgangstyp.SONSTIG`                  | Sonstiges                      |

### Dokumententypen

| Wert              | Python-Enum-Member                | Beschreibung                 |
|-------------------|-----------------------------------|------------------------------|
| `preparl-entwurf` | `Doktyp.PREPARL_MINUS_ENTWURF`   | Vorparlamentarischer Entwurf |
| `entwurf`         | `Doktyp.ENTWURF`                 | Gesetzentwurf                |
| `antrag`          | `Doktyp.ANTRAG`                  | Antrag                       |
| `anfrage`         | `Doktyp.ANFRAGE`                 | Anfrage                      |
| `antwort`         | `Doktyp.ANTWORT`                 | Antwort                      |
| `mitteilung`      | `Doktyp.MITTEILUNG`             | Mitteilung                   |
| `beschlussempf`   | `Doktyp.BESCHLUSSEMPF`          | Beschlussempfehlung          |
| `stellungnahme`   | `Doktyp.STELLUNGNAHME`          | Stellungnahme                |
| `gutachten`       | `Doktyp.GUTACHTEN`              | Gutachten                    |
| `redeprotokoll`   | `Doktyp.REDEPROTOKOLL`          | Redeprotokoll                |
| `tops`            | `Doktyp.TOPS`                    | Tagesordnung                 |
| `tops-aend`       | `Doktyp.TOPS_MINUS_AEND`        | Tagesordnungsänderung        |
| `tops-ergz`       | `Doktyp.TOPS_MINUS_ERGZ`        | Tagesordnungsergänzung       |
| `sonstig`         | `Doktyp.SONSTIG`                 | Sonstiges                    |

## Datenquellen BaWue Landtag

> **Hinweis:** Der Landtag BaWue bietet **keine offizielle API** und **keine Open-Data-Schnittstelle** an.
> Baden-Württemberg liegt im [Open Data Ranking](https://opendataranking.de/laender/baden-wuerttemberg/) im unteren
> Drittel. Das Open-Data-Portal [daten.bw](https://www.daten-bw.de/) enthält keine parlamentarischen Daten.

### Übersicht Datenquellen

| Quelle                      | Typ                            | Empfehlung                                                             |
|-----------------------------|--------------------------------|------------------------------------------------------------------------|
| **PARLIS JSON-Endpunkt**    | Undokumentierte API (POST/GET) | **Primärquelle für Vorgänge** — strukturierter als reines Web-Scraping |
| **PARLIS Web-Oberfläche**   | Web-Scraping                   | Fallback / für Daten die der JSON-Endpunkt nicht liefert               |
| **landtag-bw.de PDFs**      | Download + Textextraktion      | Für Volltexte der Drucksachen (via Framework-Dokumentpipeline)         |
| **Beteiligungsportal**      | Web-Scraping                   | Ergänzend — vorparlamentarische Entwürfe & Stellungnahmen              |
| **Kabinettsberichte (STM)** | Web-Scraping (Fließtext)       | Optional — Signalquelle für neue Regierungsentwürfe                    |
| **Gesetzblatt BaWue**       | Web-Suche                      | Ergänzend — Verkündungen (postparlamentarisch)                         |

### 1. PARLIS — Undokumentierte JSON-API (Primärquelle)

Das PARLIS-System (`parlis.landtag-bw.de`) bietet neben der Web-Oberfläche einen JSON-Endpunkt, der nicht offiziell
dokumentiert ist, aber vom Open-Data-Projekt [dokukratie (OKF)](https://github.com/okfde/dokukratie) produktiv genutzt
wird.

**Endpunkte:**

| Endpunkt                                             | Methode     | Funktion                                                             |
|------------------------------------------------------|-------------|----------------------------------------------------------------------|
| `https://parlis.landtag-bw.de/parlis/browse.tt.json` | POST (JSON) | Suche nach Vorgängen, liefert `report_id` + `item_count`             |
| `https://parlis.landtag-bw.de/parlis/report.tt.html` | GET         | Ergebnisse abrufen (paginiert via `report_id`, `start`, `chunksize`) |

**Ablauf (implementiert in `ParlisClient`):**

1. Session aufbauen (Cookies/Referer von `parlis.landtag-bw.de/parlis/`)
2. POST an `browse.tt.json` → liefert `report_id` und `item_count`
3. GET auf `report.tt.html?report_id=X&start=0&chunksize=50` → HTML mit Ergebnissen
4. HTML parsen via `ParlisParser` (XPath: `.//div[contains(@class, "efxRecordRepeater")]`)
5. Paginierung: `start` inkrementieren bis alle Ergebnisse abgeholt
6. Bei zu großen Ergebnismengen: automatische Unterteilung in Monatsfenster

**Einschränkungen:**

- Nicht offiziell dokumentiert, kann sich jederzeit ändern
- Ergebnisse kommen als HTML (nicht JSON), müssen geparst werden
- Benötigt Session-Cookie (erst Startseite laden)
- Verfügbare Suchfelder/Dokumenttypen müssen experimentell ermittelt werden

**Technische Referenz:** [okfde/dokukratie BW config](https://github.com/okfde/dokukratie/blob/main/dokukratie/bw.yml)

### 2. PARLIS Web-Oberfläche (Fallback)

Die offizielle Web-Oberfläche: `https://parlis.landtag-bw.de/parlis/`

- Parlamentarische Vorgänge, Drucksachen und Plenarprotokolle ab der 9. Wahlperiode (1984)
- Suchfunktionen: Einfach, Erweitert, Expertenmodus (Bool-Operatoren), Thesaurus
- Ergebnisse als HTML, PDFs zum Download
- Kein Export in maschinenlesbare Formate

### 3. Landtag-Website (landtag-bw.de)

| Bereich            | URL                              | Inhalt                       |
|--------------------|----------------------------------|------------------------------|
| Drucksachen        | `/de/dokumente/drucksachen`      | Suchformular + PDF-Downloads |
| Plenarprotokolle   | `/de/dokumente/plenarprotokolle` | Protokolle als PDF           |
| Gesetzesbeschlüsse | `/de/Gesetzesbeschluesse.html`   | Verabschiedete Gesetze       |
| Sitzungskalender   | ICS-Download verfügbar           | `terminkalender.ics`         |

- PDFs mit Blob-IDs (`/resource/blob/{id}/...`)
- Kein REST-API, kein RSS/Atom-Feed

### 4. Drittanbieter-Quellen (ergänzend)

| Quelle                                                           | Relevanz        | Daten                                                   |
|------------------------------------------------------------------|-----------------|---------------------------------------------------------|
| [abgeordnetenwatch.de API](https://www.abgeordnetenwatch.de/api) | Begrenzt        | Abgeordnete, Abstimmungen — keine Drucksachen/Vorgänge  |
| [dokukratie (OKF)](https://github.com/okfde/dokukratie)          | Hoch (Referenz) | Funktionierender BW-Scraper, nutzt PARLIS JSON-Endpunkt |
| Beteiligungsportal BaWue                                         | Ergänzend       | Gesetzentwürfe im Anhörungsverfahren                    |

### 5. Staatsministerium & Regierungsquellen

> **Fazit:** Die Quellen des Staatsministeriums (STM) eignen sich **nicht** als Ersatz für PARLIS, da sie keine
> strukturierten Gesetzgebungsdaten, keine maschinenlesbaren Schnittstellen und keine Drucksachennummern bieten. Zwei
> Quellen sind jedoch als **Ergänzung** wertvoll.

#### 5a. Beteiligungsportal (ergänzend — vorparlamentarische Phase)

**URL:** [beteiligungsportal.baden-wuerttemberg.de](https://beteiligungsportal.baden-wuerttemberg.de/de/mitmachen/lp-17)

Das Beteiligungsportal deckt die **vorparlamentarische Phase** ab und enthält Informationen, die in PARLIS nicht
verfügbar sind:

- PDF-Downloads von Verordnungs- und Gesetzentwürfen vor der parlamentarischen Einbringung
- 3-Phasen-Prozess: Kommentierung → Ministeriums-Antwort → Beschluss
- Nachhaltigkeits- und Bürokratiebewertungen
- Stellungnahmen von Verbänden und Bürgern

**Einschränkungen:**

- Nur ausgewählte Vorhaben (keine vollständige Abdeckung aller Gesetzentwürfe)
- Kein maschinenlesbarer Zugang (HTML-Scraping erforderlich)

**Relevanz:** Stationstypen `preparl-regent` und `preparl-regbsl`

#### 5b. Kabinettsberichte (optionale Signalquelle)

**URL:**
[stm.baden-wuerttemberg.de/.../kabinettsberichte](https://stm.baden-wuerttemberg.de/de/themen/regierungskoordination/kabinettsberichte)

Wöchentliche Berichte über Kabinettsbeschlüsse. Können als **Trigger** dienen, um in PARLIS nach neuen Vorgängen zu
suchen.

**Einschränkungen:**

- PR-Texte ohne strukturierte Daten (Fließtext)
- Keine Drucksachennummern, keine Verknüpfung zu parlamentarischen Vorgängen
- Keine Volltexte der Gesetzentwürfe

**Relevanz:** Kann Hinweise auf neue Vorgänge vom Typ `preparl-regbsl` liefern

#### 5c. Gesetzblatt BaWue (postparlamentarische Phase)

**URL:**
[baden-wuerttemberg.de/.../gesetzblatt](https://www.baden-wuerttemberg.de/de/service/alle-meldungen/meldung/pid/gesetzblatt/)

Enthält verkündete Gesetze nach der parlamentarischen Verabschiedung. Web-Suche verfügbar, aber keine API.

**Relevanz:** Stationstyp `postparl-gsblt` — Verkündung im Gesetzblatt

### Zu scrapen

| Datenbereich                 | Quelle                               | Modell                                                                 | Priorität |
|------------------------------|--------------------------------------|------------------------------------------------------------------------|-----------|
| Gesetzgebungsvorgänge        | PARLIS JSON-Endpunkt + Detail-Seiten | `Vorgang` + `Station`                                                  | Primär    |
| Dokumente                    | PDFs der Drucksachen (landtag-bw.de) | `Dokument` (via Framework-Dokumentpipeline)                            | Primär    |
| Sitzungstermine              | ICS-Kalender, Plenarsitzungen        | `Sitzung` + `Top` (via `BawueSitzungenScraper`, Phase 1 implementiert)| Primär    |
| Ausschussarbeit              | PARLIS, Ausschussprotokolle          | `Station` (typ: `parl-ausschber`)                                      | Primär    |
| Vorparlamentarische Entwürfe | Beteiligungsportal                   | `Station` (typ: `preparl-regent`), `Dokument` (typ: `preparl-entwurf`) | Ergänzend |
| Kabinettsbeschlüsse          | Kabinettsberichte (STM)              | Signal für neue `Vorgang`-Suche in PARLIS                              | Optional  |
| Gesetzblatt-Verkündungen     | Gesetzblatt BaWue                    | `Station` (typ: `postparl-gsblt`)                                      | Ergänzend |

## Konfiguration

Die Konfiguration erfolgt über das 4-Tier-System des pazufa-collector Frameworks:
Defaults → TOML (`config.toml`) → Umgebungsvariablen → CLI-Argumente.

### Framework-Konfiguration

| Sektion      | Schlüssel        | Beschreibung                                     | Pflicht |
|--------------|------------------|--------------------------------------------------|---------|
| `[backend]`  | `ltzf-api-url`   | URL des PaZuFa-Backends                          | Ja      |
| `[backend]`  | `ltzf-api-key`   | API-Key (Scope: collector)                        | Ja      |
| `[main]`     | `collector-uuid`  | Eindeutige Collector-ID                          | Ja      |
| `[cache]`    | `redis-host`     | Redis-Host für Caching                            | Nein    |
| `[cache]`    | `redis-port`     | Redis-Port (Standard: 6379)                       | Nein    |
| `[scrapers]` | `scraper-dir`    | Verzeichnis mit Scraper-Modulen                   | Ja      |
| `[llm]`      | `openai-api-key` | API-Key für LLM-Zusammenfassungen                 | Nein    |

### BaWue-spezifische Konfiguration

| Sektion    | Schlüssel                | Standard          | Beschreibung                                             |
|------------|--------------------------|-------------------|----------------------------------------------------------|
| `[bawue]`  | `wahlperiode`            | 17                | Aktuelle Wahlperiode                                     |
| `[bawue]`  | `parlis-request-delay-s` | 1.0               | Verzögerung zwischen PARLIS-Anfragen (s)                 |
| `[bawue]`  | `wahlperiode-start-date` | `"2021-04-26"`    | Startdatum der aktuellen Wahlperiode (setzt Suchbereich) |
| `[bawue]`  | `ics-url`                | *(landtag-bw.de)* | URL des ICS-Kalender-Feeds für Sitzungen                 |

| Sektion         | Schlüssel           | Standard | Beschreibung                                        |
|-----------------|---------------------|----------|-----------------------------------------------------|
| `[beteiligung]` | `wahlperiode`       | 17       | Wahlperiode für Beteiligungsportal LP-Index         |
| `[beteiligung]` | `request-delay-s`   | 2.0      | Verzögerung zwischen Anfragen (s)                   |

## Referenz: pazufa-collector Framework

Der [pazufa-collector](https://codeberg.org/PaZuFa/pazufa-collector) ist das gemeinsame Framework für alle PaZuFa-
Collector-Plugins:

- **Sprache:** Python (Poetry)
- **Basisklassen:** `VorgangsScraper`, `SitzungsScraper`
- **Pattern:** Listing-Pages → Item-Extraktion → Caching → API-Einlieferung
- **Auto-Discovery:** Scraper-Module werden anhand des Dateinamens (`*_scraper.py`) automatisch entdeckt
- **Dokumentenverarbeitung:** PDF-Extraktion mit OCR-Fallback (Kreuzberg/EasyOCR) und LLM-Zusammenfassung
- **Caching:** Redis mit 2-Wochen-TTL (mehrstufig: Vorgang, Dokument, HTML)
- **Modelle:** Auto-generiert aus OpenAPI-Spezifikation

### Scraper-Ablauf (VorgangsScraper)

1. Framework ruft `listing_page_extractor(url)` für jede URL in `listing_urls`
2. Scraper gibt Liste von Item-Identifikatoren zurück
3. Framework ruft `item_extractor(item)` für jeden Identifikator
4. Scraper baut `Vorgang`-Objekt aus den auto-generierten Modellen
5. Framework prüft Redis-Cache auf Duplikate
6. Framework sendet via `PUT /api/v2/vorgang` an Backend
7. Framework wartet `cycle-time-s` Sekunden und wiederholt

## Generelle Anforderungen

1. **Idempotenz:** Wiederholtes Ausführen darf keine Duplikate erzeugen (Framework + Backend)
2. **Fehlertoleranz:** Einzelne fehlgeschlagene Vorgänge dürfen nicht den gesamten Scraper stoppen (Framework)
3. **Rate-Limiting:** Respektierung der Landtags-Website (konfigurierbare Verzögerung in `[bawue]`)
4. **Volltext-Extraktion:** PDFs werden über die Framework-Dokumentpipeline verarbeitet
5. **Korrekte Zuordnung:** Enum-Mapping via `enum_mapper.py` mit `sonstig` als Fallback
6. **Logging:** Nachvollziehbare Logs für Debugging und Monitoring
7. **Konfigurierbarkeit:** Alle Einstellungen über config.toml / Umgebungsvariablen (4-Tier)
