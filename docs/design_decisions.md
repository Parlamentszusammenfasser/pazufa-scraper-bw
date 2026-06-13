# Designentscheidungen

Dieses Dokument hält Architektur- und Datenmodellierungsentscheidungen fest, die von
den PaZuFa-Standardkonventionen abweichen oder einer Erklärung bedürfen.

---

## DD-001: Änderungsanträge als Dokumente, nicht als Stationen

**Datum:** 27.03.2026

**Kontext:** PARLIS listet Änderungsanträge als separate Fundstellen auf,
wodurch der Scraper einzelne Stationen mit dem Typ `parl-initiativ` erzeugt.
Das ist irreführend, da ein Änderungsantrag keine neue parlamentarische Initiative
darstellt -- er ist ein Änderungsvorschlag, der im Rahmen einer Plenarlesung
(Beratung) eingebracht wird.

Andere PaZuFa-Scraper (z. B. RLP) bilden Änderungsanträge auf `parl-initiativ` ab
und folgen damit dem generischen Substring-Match auf "Antrag". Für BaWue überschreiben
wir diese Konvention, da die PARLIS-Daten ausreichend Kontext liefern, um
Änderungsanträge genauer zu modellieren.

**Entscheidung:** Änderungsanträge werden als Dokumente an die `parl-vollvlsgn`-Station
(Plenarlesung) angehängt, in der sie behandelt wurden. Sie erzeugen keine eigene
Station. Falls eine nachfolgende `parl-vollvlsgn`-Station existiert, werden die
Dokumente dort angehängt; andernfalls wird auf die vorhergehende zurückgegriffen.
Falls überhaupt keine `parl-vollvlsgn`-Station existiert, wird der Änderungsantrag
mit einer Warnung verworfen.

**Entscheidung:** Entschließungsanträge werden vollständig verworfen und erscheinen
nicht in der Ausgabe. Sie sind prozedurale Anträge ohne Bezug zum legislativen
Inhalt des Vorgangs.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methode `_collect_stationen()`.
Die Erkennung basiert auf dem Feld `station_typ`, das aus dem Fundstellentext
extrahiert wird (z. B. "Änderungsanträge", "Entschließungsantrag"), und wird
geprüft, bevor das generische Enum-Mapping greift.

---

## DD-002: Mitteilungen werden bewusst als `sonstig` klassifiziert

**Datum:** 27.03.2026

**Kontext:** PARLIS listet "Mitteilung"-Fundstellen als eigenständige Schritte im
Gesetzgebungsverfahren auf. Diese Mitteilungen sind generische prozedurale
Benachrichtigungen, deren Bedeutung vom Kontext abhängt — insbesondere vom
Absender und vom Zeitpunkt im Verfahren. Beispiele:

- **Mitteilung der Präsidentin des Landtags** (zwischen Erster Beratung und
  Ausschussberatung): Typischerweise die formale Überweisungsmitteilung an den
  zuständigen Ausschuss.
- **Mitteilung der Landesregierung** (nach Verkündung im Gesetzblatt): Häufig ein
  Umsetzungsbericht, eine Rechtsverordnung oder eine Statusmitteilung zum
  Inkrafttreten — oft Monate nach der Verkündung.

Das Wort "Mitteilung" ist absichtlich kein Schlüssel in `STATIONSTYP_MAP`. Da keine
einzelne Stationstyp-Zuordnung alle Varianten korrekt abbilden kann, fällt
`map_stationstyp()` auf den Default `Stationstyp.SONSTIG` zurück.

**Entscheidung:** Mitteilungen werden als `sonstig`-Stationen belassen. Eine
pauschale Zuordnung zu einem spezifischen Stationstyp (z. B. `postparl-kraft`)
wäre in vielen Fällen falsch. Der Dokumententyp `Doktyp.MITTEILUNG` wird weiterhin
korrekt über `DOKUMENTENTYP_MAP` zugeordnet.

**Implementierung:** `enum_mapper.py`, Funktion `map_stationstyp()` — der
Fallback `return Stationstyp.SONSTIG` greift, wenn kein Schlüssel in
`STATIONSTYP_MAP` matched. Die Zuordnung `"Mitteilung" → Doktyp.MITTEILUNG`
in `DOKUMENTENTYP_MAP` bleibt davon unberührt.

---

## DD-003: Gesetzentwurf — kontextabhängiges Mapping auf Stationstyp

**Datum:** 27.03.2026 | **Aktualisiert:** 05.04.2026

**Kontext:** PARLIS verwendet den gleichen Fundstellentext „Gesetzentwurf" sowohl für
Regierungsentwürfe als auch für parlamentarische Initiativen (z. B. Fraktionsentwürfe).
Ohne zusätzlichen Kontext würde jeder Gesetzentwurf einheitlich als `parl-initiativ`
klassifiziert.

**Entscheidung:** Das Mapping von „Gesetzentwurf" hängt vom Initiator ab:

- Initiator enthält „Landesregierung" → `preparl-regbsl` (Kabinettsbeschluss)
- Sonst → `parl-initiativ` (parlamentarische Initiative)

Analog wird der Dokumententyp kontextabhängig zugeordnet:

- Station ist `preparl-regbsl` → `preparl-entwurf`
- Sonst → `entwurf`

Dies ist das einzige Enum-Mapping, das externen Kontext (den Initiator) benötigt.

**Aktualisierung (05.04.2026):** Ursprünglich wurde „Gesetzentwurf Landesregierung"
als `preparl-regent` (Regierungsentwurf, R) klassifiziert. Nach Review durch den
Backend-Entwickler (Issue #26) umklassifiziert zu `preparl-regbsl`
(Kabinettsbeschluss, S). Begründung: PARLIS zeigt den Entwurf **nach**
Kabinettsbeschluss — den parlamentarischen Eingang, nicht die Entwurfsphase.
Die vorparlamentarische Entwurfsphase (`preparl-regent`, R) wird vom
Beteiligungsportal-Scraper abgedeckt (`bawue_beteiligung_scraper.py`).
Diese Umklassifizierung ermöglicht die Nutzung des BY-Tracks
(`((E*R+)?S)?I...`) ohne BW-spezifischen Präfix.

**Implementierung:** `enum_mapper.py`, Funktionen `map_stationstyp()` (Parameter
`initiator`) und `map_dokumententyp()` (Parameter `is_vorparlamentarisch`).

---

## DD-004: ~~Plenarlesungen werden nie zusammengeführt~~

**Datum:** 27.03.2026 | **Aktualisiert:** 23.04.2026

> **Status:** Teilweise abgelöst durch [DD-024](#dd-024-plenarlesungen-gleicher-runde-werden-konsolidiert)
> (Runden-Konsolidierung für `parl-vollvlsgn`). Der ursprüngliche Leitsatz — unterschiedliche
> Lesungen (Erste/Zweite/Dritte) bleiben getrennte Stationen — bleibt gültig.

**Kontext:** PARLIS liefert für einen Vorgang häufig mehrere Fundstellen mit dem
gleichen Stationstyp und Gremium hintereinander — z. B. zwei Ausschuss-Fundstellen
für denselben Ausschuss. Um Duplikate zu vermeiden, werden aufeinanderfolgende
Stationen gleichen Typs und Gremiums zu einer Station zusammengeführt (Merge).

**Entscheidung (ursprünglich):** Stationen vom Typ `parl-vollvlsgn` (Plenarlesungen) werden **nie**
zusammengeführt. Jede Lesung (Erste, Zweite, Dritte Beratung) bleibt eine eigene
Station — auch wenn sie direkt aufeinander folgen und dasselbe Gremium `plenum`
(reservierter Name, s. DD-021) haben. Eine Zweite Beratung ist schlicht eine weitere Station vom Typ
`parl-vollvlsgn`, kein gesonderter Stationstyp.

**Aktualisierung (DD-024):** Fundstellen derselben Runde (identischer roh-`station_typ`-Text, z. B.
beide "Zweite Beratung" für Staatshaushaltsgesetz-Einzelpläne) werden nun **zu einer Station
konsolidiert**. Unterschiedliche Rundentexte ("Erste" vs. "Zweite" vs. "Überweisung") bleiben wie
ursprünglich getrennt. Siehe DD-024 für Kontext und Begründung.

Für Ausschussberatungen (`parl-ausschber`) wird zusätzlich rückwärts über die
Plenarstationsgrenze hinaus nach einem passenden Ausschuss gesucht, aber nicht
vorwärts über eine Plenarstation hinweg.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methoden `_try_merge_station()`
und `_find_matching_ausschuss()`.

---

## DD-005: Stellungnahmen als Kinder der vorhergehenden Station

**Datum:** 27.03.2026

**Kontext:** PARLIS listet Stellungnahmen (z. B. Antworten auf Kleine Anfragen) als
eigenständige Fundstellen. Sie stellen jedoch keinen eigenen Verfahrensschritt dar,
sondern sind Reaktionen auf eine vorhergehende Station.

**Entscheidung:** Stellungnahmen erzeugen keine eigene Station. Stattdessen werden
ihre Dokumente als Kinder an die unmittelbar vorhergehende Station angehängt. Die
Erkennung erfolgt auf zwei Wegen:

1. Alle Dokumente der Fundstelle haben den Typ `Doktyp.STELLUNGNAHME`.
2. Die Fundstelle hat keine PDF-Dokumente, aber der Fundstellentext enthält
   „Stellungnahme" oder „Antwort".

Falls keine vorhergehende Station existiert, wird die Stellungnahme mit einer
Warnung verworfen.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methoden `_is_stellungnahme()`
und `_attach_stellungnahme()`.

---

## DD-006: ICS-Kalender — nur parlamentarische Sitzungstypen

**Datum:** 27.03.2026

**Kontext:** Der ICS-Feed des Landtags enthält ca. 8 Eventkategorien, die anhand
des SUMMARY-Präfix unterschieden werden. Nicht alle sind parlamentarische Sitzungen
im Sinne des PaZuFa-Datenmodells.

**Entscheidung:** Nur folgende Eventtypen werden übernommen:

| SUMMARY-Präfix                                   | Gremium                  |
|--------------------------------------------------|--------------------------|
| `Plenarsitzung:`                                 | `plenum` (reserviert)    |
| `Fraktions- und Ausschusssitzungen: FinA`        | `Finanzausschuss`        |
| `Haushaltsberatungen:`                           | (aus Suffix extrahiert)  |

Ausgeschlossen werden: **Fraktionen** (parteiinterne Sitzungen), **Ausschuesse**
(Sammel-Event ohne Ausschuss-Namen — DoD-Regel "Namen MÜSSEN so spezifisch wie
möglich sein", s. DD-022), **Präsidium** (Verwaltung), **Wahl**
(Verfassungsereignis). Diese Events werden still übersprungen.

**Implementierung:** `ics_parser.py`, Funktion `_classify_event()`.

---

## DD-007: Beteiligungsportal — nur Prozesse mit Entwurf-PDFs

**Datum:** 27.03.2026

**Kontext:** Das Beteiligungsportal Baden-Württemberg enthält neben
Gesetzgebungsverfahren auch rein informatorische Inhalte (z. B.
„Klima-Maßnahmen-Register 2026"), die keine vorparlamentarischen Initiativen
darstellen.

**Entscheidung:** Es werden nur Beteiligungsprozesse übernommen, die mindestens
einen PDF-Link enthalten. Das Vorhandensein eines PDFs ist ein hinreichendes
Indiz für einen tatsächlichen Gesetzentwurf. Prozesse ohne PDFs werden mit
Info-Log übersprungen.

**Implementierung:** `bawue_beteiligung_scraper.py`, Methode `item_extractor()` —
Prüfung auf `detail.pdf_links`.

---

## DD-008: Platzhalterdaten aus PARLIS (00.00.JJJJ)

**Datum:** 27.03.2026

**Kontext:** PARLIS liefert gelegentlich Platzhalterdaten im Format „00.00.2028",
wenn nur das Jahr bekannt ist. Standardmäßiges Datumsparsen schlägt hier fehl.

**Entscheidung:** Platzhalterdaten werden über einen dreistufigen Fallback behandelt:

1. Reguläres Parsing als TT.MM.JJJJ
2. Regex-Extraktion einer vierstelligen Jahreszahl (ab „20") → 1. Januar des Jahres
3. Falls beides scheitert → aktueller Zeitstempel als Fallback

Alle resultierenden Datumsangaben sind zeitzonen-bewusst (UTC), da die API naive
Datumsangaben mit HTTP 422 ablehnt.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Funktionen
`_parse_fundstelle_date()` und `_fallback_date_from_year()`.

---

## DD-009: Initiative-Fallback aus Fundstellen-Autor

**Datum:** 27.03.2026

**Kontext:** Für bestimmte Vorgangstypen (insb. Haushaltsgesetzgebung) fehlt in
PARLIS das Feld „Initiative" vollständig. Ohne diese Information kann das
kontextabhängige Gesetzentwurf-Mapping (DD-003) nicht zwischen Regierungsentwurf
und parlamentarischer Initiative unterscheiden.

**Entscheidung:** Wenn das Feld „Initiative" fehlt, wird auf das `autor_text`-Feld
der ersten Fundstelle zurückgegriffen, um den Initiator zu bestimmen.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methode `_build_vorgang()`.

---

## DD-010: Synthetische Ablehnung aus „Aktueller Stand"

**Datum:** 27.03.2026

**Kontext:** PARLIS behandelt Annahme und Ablehnung asymmetrisch. Angenommene
Vorgänge erhalten eine explizite Fundstelle (z. B. „Zustimmung", „Annahme",
„Gesetzesbeschluss"), die vom Scraper als `parl-akzeptanz`-Station erfasst wird.
Abgelehnte Vorgänge erhalten **keine** solche Fundstelle — der Vorgang endet
einfach nach der letzten Plenarlesung. Das Abstimmungsergebnis ist nur im
Plenarprotokoll-PDF und im Metadatenfeld „Aktueller Stand" (Wert: „Abgelehnt")
der PARLIS-Suchergebnisse dokumentiert.

**Evidenz:** In einem ersten Scrape-Lauf (236 Vorgänge, WP 17) wurden
462 `parl-akzeptanz`-Stationen, aber **null** `parl-ablehnung`-Stationen
extrahiert. Ein späterer vollständiger Staging-Lauf (235 Vorgänge, 13.04.2026)
zeigte jedoch, dass PARLIS bei einigen Vorgängen durchaus „Ablehnung"-Fundstellen
liefert — teils sogar doppelt. Die synthetische Station wird daher nur benötigt,
wenn keine Fundstellen-basierte `parl-ablehnung` vorhanden ist.

**Entscheidung:** Wenn das Metadatenfeld „Aktueller Stand" den Wert „Abgelehnt"
enthält und noch keine `parl-ablehnung`-Station existiert, wird eine synthetische
Station angehängt. Das Datum wird von der letzten vorhandenen Station übernommen
(i.d.R. die finale Plenarlesung, in der die Abstimmung stattfand). Falls keine
Stationen vorhanden sind, wird keine synthetische Station erzeugt.

Das Feld „Aktueller Stand" wird bereits durch den generischen `<dl>`-Metadaten-
Parser in `parlis_parser.py` erfasst und als Schlüssel im `RawVorgang`-Dict
bereitgestellt — es war lediglich nicht ausgewertet.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methoden `_build_vorgang()`
und `_ensure_ablehnung_station()`.

---

## DD-011: Whitespace-Normalisierung und Raw-Text-Gegenprüfung beim Enum-Mapping

**Datum:** 27.03.2026

**Kontext:** PARLIS formatiert Fundstellentexte mit doppelten Leerzeichen als
Trennzeichen zwischen Feldern (z. B. Stationstyp, Autor, Datum). Der Parser
(`parlis_parser.py`) nutzt ein Non-Greedy-Regex, das am *ersten* Doppelleerzeichen
trennt, um den `station_typ` zu extrahieren.

Problematisch wird dies bei mehrteiligen Stationstypen wie
„Beschluss des Landtags  in Zweiter Beratung", wenn PARLIS ein Doppelleerzeichen
*innerhalb* des Stationstyps einfügt. Der Parser schneidet dann nach „Landtags"
ab und extrahiert `station_typ = "Beschluss des Landtags"` — ohne den
qualifizierenden Zusatz „in Zweiter Beratung".

**Auswirkung:** „Beschluss des Landtags" mappt auf `parl-akzeptanz` (Annahme),
obwohl „Beschluss des Landtags in Zweiter Beratung" korrekt auf
`parl-vollvlsgn` (Plenarlesung) mappt. Betroffene Vorgänge (insb.
Haushaltsgesetzgebung) zeigten dadurch mehrere falsche Annahme-Stationen.

**Entscheidung:** Zweistufige Absicherung:

1. **Whitespace-Normalisierung:** `map_stationstyp()` und `map_dokumententyp()`
   kollabieren vor dem Substring-Matching alle Whitespace-Sequenzen zu einfachen
   Leerzeichen. Damit matcht auch „Landtags  in" den Schlüssel
   „Beschluss des Landtags in".

2. **Raw-Text-Gegenprüfung:** `_build_station()` mappt zunächst den extrahierten
   `station_typ`. Anschließend wird auch der vollständige Fundstellentext (`raw`)
   gemappt. Falls das Raw-Mapping ein *anderes*, nicht-`sonstig` Ergebnis liefert,
   wird dieses bevorzugt — es hat einen spezifischeren (längeren) Schlüssel
   gematcht.

Die Gegenprüfung überschreibt nur, wenn das Raw-Ergebnis nicht `sonstig` ist.
Dadurch bleiben Fälle unberührt, in denen `station_typ` manuell gesetzt oder
präziser als der Rohtext ist (z. B. „Beschlussempfehlung und Bericht" im
`station_typ`, während der Rohtext nur „Beschlussempfehlung" enthält).

**Implementierung:** `enum_mapper.py`, Funktion `_normalize_whitespace()`,
aufgerufen in `map_stationstyp()` und `map_dokumententyp()`.
`bawue_vorgaenge_scraper.py`, Methode `_build_station()` — Gegenprüfung nach
dem primären Mapping.

---

## DD-012: Synthetische `parl-initiativ` nach `preparl-regbsl`

**Datum:** 29.03.2026 | **Aktualisiert:** 05.04.2026

**Kontext:** Die Backend-Track-Definition für BaWue-Gesetzgebung verlangt nach
der vorparlamentarischen Phase (`((E*R+)?S)?`) zwingend eine `parl-initiativ`-Station
(`I`), bevor die parlamentarische Bearbeitung beginnt (`VA*...`). Bei
Fraktionsentwürfen ist dies unproblematisch — der Fundstellentext „Gesetzentwurf"
wird direkt als `parl-initiativ` klassifiziert (s. DD-003).

Bei Regierungsentwürfen entsteht eine Lücke: PARLIS verwendet **eine einzige
Fundstelle** „Gesetzentwurf" für den gesamten Vorgang der Einbringung. Diese
wird als `preparl-regbsl` (Kabinettsbeschluss) klassifiziert — PARLIS zeigt den
Entwurf **nach** dem Kabinettsbeschluss, also den parlamentarischen Eingang.
Die vorparlamentarische Entwurfsphase (`preparl-regent`, R) wird bereits vom
Beteiligungsportal-Scraper abgedeckt. PARLIS liefert keine separate Fundstelle
für die parlamentarische Einbringung; die nächste Fundstelle ist direkt
„Erste Beratung" (`parl-vollvlsgn`).

**Evidenz:** Überprüfung auf der PARLIS-Website (4 Vorgänge, WP 16 + WP 17)
bestätigt, dass Regierungsentwürfe durchgängig von „Gesetzentwurf Landesregierung"
direkt zu „Erste Beratung" springen — ohne Zwischeneintrag. Das Feld „Initiative"
auf der Detailseite ist ein Metadatenfeld am Vorgang, keine eigene Fundstelle.

**Entscheidung:** Analog zu DD-010 (synthetische Ablehnung) wird eine synthetische
`parl-initiativ`-Station eingefügt, wenn auf `preparl-regbsl` nicht bereits eine
`parl-initiativ` folgt. Die synthetische Station übernimmt die Dokumente der
`preparl-regbsl`-Station (der Gesetzentwurf *ist* die parlamentarische Initiative)
und erhält als Datum den Zeitpunkt der nächsten Station (typischerweise die Erste
Beratung). Dies ist semantisch korrekt: Die Einbringung eines Regierungsentwurfs
in den Landtag stellt gleichzeitig die parlamentarische Initiative dar — PARLIS
bildet lediglich beide Schritte in einer Fundstelle ab.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methoden `_build_vorgang()`
und `_ensure_initiativ_after_regbsl()`.

---

## DD-012: Überspringen von Vorgängen ohne parlamentarische Stationen

**Datum:** 29.03.2026

**Kontext:** PARLIS listet unter dem Vorgangstyp „Gesetzgebung" auch Einträge auf,
die kein vollständiges parlamentarisches Gesetzgebungsverfahren durchlaufen haben.
Diese Einträge besitzen ausschließlich nachparlamentarische Stationen (`postparl-*`)
und keine parlamentarischen Stationen (`parl-*`). Es handelt sich um:

- **Bekanntmachungen** — z. B. Inkrafttreten von Staatsverträgen (V-212734, V-213657)
- **Neufassungen** — z. B. Geschäftsordnung der Landesregierung (V-212729, V-221160)
- **Berichtigungen** — Korrekturen veröffentlichter Gesetze im Gesetzblatt (V-222654)

Das Backend validiert Vorgänge gegen den Track `gg-land-parl`, der eine
parlamentarische Kette (`parl-initiativ → parl-vollvlsgn → …`) voraussetzt.
Einträge mit nur `postparl-gsblt` werden mit HTTP 400 (Track validation Failed)
abgelehnt.

**Entscheidung:** Vorgänge, bei denen alle Stationen nachparlamentarisch sind
(`postparl-gsblt`, `postparl-vesja`, `postparl-vesne`, `postparl-kraft`), werden
in `item_extractor()` übersprungen und mit einer Info-Lognachricht dokumentiert.
Dies ist korrekt, da diese Einträge keine eigenständigen Gesetzgebungsverfahren
des Landtags darstellen.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methode `item_extractor()`,
Konstantenmenge `_POSTPARL_TYPEN`.

---

## DD-013: Optionale Token-Kürzung vor LLM-Aufruf

**Datum:** 01.04.2026

**Kontext:** Der Scraper extrahiert Volltext aus PDFs (via OCR oder direkter
Textextraktion) und sendet diesen an ein LLM, um Metadaten wie den Dokumententyp
zu bestimmen. Gesetzgebungsdokumente können mehrere zehntausend Tokens umfassen,
was die Kosten pro Aufruf erheblich erhöht. Da Titel, Typ und Zweck eines
Dokuments in der Regel im Kopfbereich stehen, ist der vollständige Textkörper für
die Klassifikation meist nicht erforderlich.

**Entscheidung:** Das `[llm]`-Konfigurationsabschnitt unterstützt einen optionalen
Parameter `truncate-tokens`. Ist er auf einen Wert > 0 gesetzt, wird der Volltext
vor dem LLM-Aufruf auf maximal diese Anzahl Tokens gekürzt. Der Wert 0 deaktiviert
die Kürzung (kein Limit). Standardwert: 12 000 Tokens.

Die Kürzung erfolgt durch echtes Token-Encoding (via `litellm`) für das jeweilige
Modell, nicht durch zeichenbasiertes Abschneiden — der Grenzwert ist damit
modellgenau.

Auf der Staging-Umgebung und in Entwicklungsläufen ist die Kürzung aktiviert
(12 000 Tokens), um Kosten zu begrenzen. Für die Produktionsumgebung ist vorgesehen,
die Kürzung zu deaktivieren (`truncate-tokens = 0`), um die volle Textqualität
zu nutzen.

**Implementierung:** `bawue_dok.py`, Funktion `truncate_text()`. Aufgerufen in
`enrich_document()` vor der Prompt-Zusammenstellung. Konfiguration über
`llm_config.get("truncate-tokens", 12000)` in `bawue_vorgaenge_scraper.py` und
`bawue_beteiligung_scraper.py`.

---

## DD-014: PARLIS-Suchergebnisse — primär JSON-Kommentare, HTML als Fallback

**Datum:** 01.04.2026

**Kontext:** Die PARLIS-Such-API liefert Antworten als HTML-Seiten. Bei der
Analyse der Antworten wurde festgestellt, dass jede Ergebniszeile zusätzlich
als JSON-Objekt in einem HTML-Kommentar (`<!-- {...} -->`) eingebettet ist.
Diese JSON-Objekte enthalten strukturierte Felder mit stabilen Feldcodes
(z. B. `EWBV10` für Titel, `EWBV02` für Vorgangs-ID), während das HTML
fragmentiertes, XPath-abhängiges Parsen erfordert.

Das JSON-Format ist nicht dokumentiert und erscheint als Debug- oder
Integrationsfeature der PARLIS-Oberfläche. Es kann daher in einer zukünftigen
PARLIS-Version entfernt werden.

**Entscheidung:** Die Funktion `parse_results()` versucht zunächst, alle
JSON-Kommentare aus dem HTML zu extrahieren und daraus `RawVorgang`-Objekte
zu bauen. Nur wenn keine JSON-Kommentare gefunden werden oder diese keine
verwertbaren Vorgänge liefern, wird auf das HTML/XPath-Parsing zurückgefallen.

Das HTML-Parsing bleibt vollständig erhalten und dient als Fallback, um
Regressionssicherheit zu gewährleisten, falls das JSON-Format künftig entfernt
wird.

**Implementierung:** `parlis_parser.py`, Funktion `parse_results()` —
ruft `_extract_json_comments()` und `_parse_results_from_json()` auf,
fällt bei leerem Ergebnis auf `_parse_results_from_html()` zurück.

---

## DD-015: Volltext-Normalisierung — Garbled-Text-Erkennung, OCR-Retry und XSS-Prävention

**Datum:** 02.04.2026

**Kontext:** PDFs aus dem Baden-Württemberger Landtag — insbesondere
Anhörungsdokumente (Drucksachen mit angehängten Stellungnahmen) — enthalten
Fonts mit fehlerhaften oder fehlenden ToUnicode-CMaps. Die visuelle Darstellung
im PDF-Viewer ist korrekt (Glyph-Outlines), aber die programmatische
Textextraktion durch kreuzberg liefert falsche Unicode-Zeichen. Zwei Muster
treten auf:

1. **Latin-Extended-Substitution:** Fonts mappen Glyph-IDs auf Unicode-Zeichen
   im Bereich U+0100–U+024F. Beispiel: `ĚĞƌ&ƌĂŬƚŝŽŶ` → `der Fraktion`,
   `ǁćŚƌůĞŝƐƚƵŶŐ` → `währleistung`. Die Zuordnung ist font-spezifisch und
   nicht durch eine einheitliche Verschiebung erklärbar.

2. **ASCII-Verschiebung (+29):** Ein anderer Font verschiebt jedes Byte um
   einen konstanten Offset. Beispiel: `6WlGWHWDJ` → `Städtetag`
   (chr(ord('6')+29) = 'S'). Zusätzlich enthält der Text C1-Steuerzeichen
   (0x80–0x9F).

Beide Muster treten in derselben PDF vor, typischerweise ab Seite 2 (die
Stellungnahmen externer Organisationen). Die erste Seite (Deckblatt des
Landtags) ist korrekt.

**Auswirkung:** Vier Vorgänge (Drucksachen 17/4244, 17/826, 17/8633, 17/5482)
schlugen mit HTTP 400 (`xss detected`) fehl. Zwei Ursachen:

- **Garbled Angle Brackets:** Die Font-Substitution mappt die Buchstaben K→`<`
  und L→`>`. Diese falschen Angle Brackets lösen die XSS-Validierung des
  Backends aus.
- **Echte E-Mail-Header:** Stellungnahmen, die als E-Mail weitergeleitet
  wurden, enthalten `<poststelle@lfdi.bwl.de>` im PDF-Text — ebenfalls
  XSS-positiv, obwohl harmlos.

**Entscheidung:** Dreistufige Absicherung:

1. **Garbled-Text-Erkennung + OCR-Retry:** Nach der normalen Textextraktion
   prüft `_is_garbled()`, ob mehr als 5 % der alphabetischen Zeichen im
   Latin-Extended-Bereich (U+0100–U+024F) liegen. Bei positivem Befund wird
   die Extraktion mit Tesseract-OCR (Sprache: `deu`) wiederholt. OCR rendert
   jede Seite als Bild und erkennt die Zeichen visuell — die fehlerhaften
   Font-Mappings werden damit umgangen. Das OCR-Ergebnis wird nur übernommen,
   wenn es tatsächlich besser ist (nicht selbst garbled); andernfalls bleibt
   der Originaltext erhalten.

   **Wichtig:** `ExtractionConfig(force_ocr=True)` allein löst in kreuzberg
   4.x kein OCR aus — es muss zusätzlich `ocr=OcrConfig(backend="tesseract",
   language="deu")` gesetzt werden. Ohne explizite OCR-Konfiguration gibt
   kreuzberg identischen (garbled) Text zurück.

2. **Paragraph-Quality-Scoring:** `normalize_volltext()` teilt den Text in
   Absätze und bewertet jeden mit `_paragraph_quality_score()`. Die Bewertung
   kombiniert vier Signale:
   - C1-Steuerzeichen (0x80–0x9F)
   - Latin-Extended-B-Zeichen (0x0180–0x024F)
   - Lange Wörter ohne deutsche Vokale
   - Übermäßiger Großbuchstabenanteil (>60 %)

   Absätze mit Score < 0,5 werden entfernt. Bei Drucksache 17/4244 überlebten
   2 von 24 Absätzen (1 738 von 38 168 Zeichen).

3. **Angle-Bracket-Neutralisierung:** Verbleibende `<` und `>` (aus
   E-Mail-Headern oder nicht vollständig gefiltertem Garbled-Text) werden
   durch Guillemets `‹` (U+2039) und `›` (U+203A) ersetzt.

Stufe 1 rettet den Inhalt (OCR produziert korrekten Text: 37 117 Zeichen,
1 Latin-Extended-Zeichen). Stufen 2 und 3 sind Defense-in-Depth für Fälle,
in denen OCR nicht verfügbar ist oder fehlschlägt.

**Implementierung:** `bawue_dok.py`:
- `_is_garbled()` — Latin-Extended-Ratio > 5 % der Alpha-Zeichen
- `extract_pdf_text()` — OCR-Retry mit `_OCR_CONFIG` bei garbled Text
- `_paragraph_quality_score()` — Multi-Signal-Bewertung pro Absatz
- `normalize_volltext()` — Absatzfilterung, NFKC, C1-Stripping,
  CRLF-Normalisierung, Angle-Bracket-Ersetzung

---

## DD-016: Track-Validierung — BW verwendet den BY-Track unverändert

**Datum:** 05.04.2026 | **Aktualisiert:** 06.04.2026

**Kontext:** Mit Backend v0.2.7 werden Vorgänge gegen Track-Definitionen (DFA/Regex)
validiert. Die Erstanalyse schlug einen eigenen BW-Track vor, der PARLIS-Abweichungen
durch optionale Elemente (`V?J`, `V?N`) kompensiert. Nach Review durch den
Backend-Entwickler (Crystalkey) und Analyse der Geschäftsordnung des Landtags BW
(17. WP, §42–§49) zeigte sich: Alle Abweichungen lassen sich scraper-seitig lösen.

**Entscheidung:** BW verwendet den BY-Track unverändert:

```
gg-land-parl = "((E*R+)?S)?I((VA*(Z|VJGK|VN|VA*(Z|VJGK|VN)))|Z)"
```

**Begründung:**

1. **Tracks bilden parlamentarisches Recht ab**, nicht was der Scraper liefern kann.
   Abweichungen in PARLIS-Daten sind Scraper-Bugs, kein Grund den Track abzuschwächen.
2. **R→S Umklassifizierung** (DD-003): PARLIS „Gesetzentwurf Landesregierung" wird
   als `preparl-regbsl` (S) statt `preparl-regent` (R) klassifiziert. Damit matcht
   der BY-Präfix `((E*R+)?S)?` korrekt.
3. **GO §42 schreibt mindestens zwei Lesungen vor** — `VJ` statt `V?J` ist korrekt.
   126/128 Annahme-Vorgänge bestätigen zwei explizite Lesungen vor Annahme.
4. **GO §43 Abs. 4 verbietet jede Abstimmung in der 1. Lesung** — `IVN` (Ablehnung
   nach nur einer Lesung) ist rechtlich unmöglich. Alle 31 Ablehnungen folgen `IVAVN`.
5. **Prefix-Matching** akzeptiert unvollständige Vorgänge ohnehin — `IVAVJG` ist ein
   gültiger Präfix von `IVAVJGK`, auch wenn K (Inkrafttreten) nicht gescrapt wurde.
6. **Y = Volksentscheid** (`postparl-vesja`), nicht Ausfertigung. Irrelevant für den
   `gg-land-parl`-Track. Volksanträge benötigen ggf. einen eigenen `gg-land-volk`-Track.

**Validierte Sequenzen** (Dev-Lauf 171 Vorgänge, WP 17):

| Sequenz   | Anzahl | Beschreibung                                      |
|-----------|--------|---------------------------------------------------|
| `SIVAVJG` | 112    | Regierungsentwurf (nach R→S) + Annahme            |
| `IVAVJG`  | 14     | Fraktionsentwurf + Annahme                        |
| `IVAVN`   | 31     | Ablehnung nach Ausschuss und 2. Lesung            |
| `S`, `SI` | 3      | Unvollständig — gültige Präfixe                   |

Quelle: [Issue #26](https://codeberg.org/PaZuFa/pazufa-backend/issues/26),
Crystalkey-Review 05.04.2026.

---

## DD-017: Konfigurierbare Filterung von `sonstig`-Stationen

**Datum:** 06.04.2026

**Kontext:** PARLIS liefert Fundstellen wie „Mitteilung" oder „Dokument", die keinem
spezifischen Stationstyp zugeordnet werden können (s. DD-002). Der `enum_mapper`
klassifiziert diese als `Stationstyp.SONSTIG`. Das Backend (v0.2.7) löst bei
`sonstig`-Stationen einen Panic aus (`validate.rs:310`, `.get()` statt `[]`).
28 Vorgänge im Dev-Lauf waren betroffen.

Das Backend wird diesen Bug voraussichtlich in einer kommenden Version beheben und
`sonstig`-Stationen akzeptieren.

**Entscheidung:** `sonstig`-Stationen werden in `_collect_stationen()` herausgefiltert,
gesteuert durch den Konfigurationsparameter `filter-sonstig-stations` (Default: `true`).
Sobald das Backend `sonstig` akzeptiert, kann der Filter durch Setzen auf `false`
deaktiviert werden.

Der Filter greift **nach** den Sonderbehandlungen für Stellungnahmen,
Änderungsanträge und Entschließungsanträge (DD-001, DD-005), da diese ebenfalls
als `sonstig` klassifiziert werden können, aber als Dokumente an vorhergehende
Stationen angehängt werden sollen.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methode `_collect_stationen()`.
Konfiguration über `[bawue]` → `filter-sonstig-stations` in `config.toml`.

---

## DD-018: ~~DD-018: WORKAROUND — Nachparlamentarische Ausschussberichte werden gefiltert~~

> Removed

---

## DD-019: Positionsheuristik — „Antrag" nach Ausschussbericht als Änderungsantrag

**Datum:** 13.04.2026

**Kontext:** PARLIS kennzeichnet Änderungsanträge in der Fundstelle-Textzeile als
schlichtes „Antrag" — nicht als „Änderungsantrag". Der Enum-Mapper bildet „Antrag"
auf `parl-initiativ` ab, was korrekt ist, wenn der Antrag am Anfang eines Vorgangs
steht (z. B. ein parlamentarischer Antrag einer Fraktion). Erscheint „Antrag" jedoch
**nach** einem Ausschussbericht (`parl-ausschber`), handelt es sich ausnahmslos um
einen Änderungsantrag zur Beschlussempfehlung — kein neuer Initiativantrag. Der
Dokumenteninhalt bestätigt dies (z. B. „Änderungsantrag der Fraktion GRÜNE und der
Fraktion der CDU zu der Beschlussempfehlung des Ausschusses …"), dieser Inhalt steht
jedoch beim Fundstelle-Parsing nicht zur Verfügung (Issue 1B, betroffen: V-214623).

**Entscheidung:** Positionsheuristik in `_collect_stationen()`: Wenn eine Station den
Typ `parl-initiativ` hat, der Fundstelle-Text „Antrag" oder „Anträge" lautet (die
mehrdeutigen Labels, nicht „Gesetzentwurf", „Kleine Anfrage" etc.) **und** bereits
ein `parl-ausschber` in der Stationsliste aufgetreten ist, wird die Station als
Änderungsantrag umklassifiziert. Ihre Dokumente werden an die nächste
`parl-vollvlsgn`-Station angehängt — identisch zum bestehenden
Änderungsantrag-Handling (s. DD-001).

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methode `_collect_stationen()`,
Klassen-Konstante `_AMBIGUOUS_ANTRAG_TYPEN`, Flag `seen_ausschber`.

---

## DD-020: LLM-Cache-Schlüssel ist ein Kompositum aus Inhalt und Prompt

**Datum:** 21.04.2026

**Kontext:** Der LLM-Cache in `bawue_dok.py` speichert die Anreicherungsergebnisse
(Semantik, Dokumententyp) unter dem Schlüssel `llm-semantics:{doc_hash}`, wobei
`doc_hash` ein SHA-256-Hash des Volltexts ist. Da identische PDFs im PARLIS-System
in unterschiedlichen Kontexten auftauchen können (z. B. ein Gesetzentwurf, der
sowohl als `ENTWURF` als auch als `STELLUNGNAHME` klassifiziert werden soll),
führte ein rein inhaltsbasierter Cache-Schlüssel zu falschen Cache-Treffern: Das
LLM wurde nur einmal aufgerufen (erster `Doktyp`-Kontext), und alle nachfolgenden
Aufrufe für dasselbe PDF lieferten das gecachte Ergebnis des ersten Prompts —
unabhängig davon, welcher Prompt tatsächlich gesendet wurde.

**Entscheidung:** Der Cache-Schlüssel wird zu einem Kompositum:

```
cache_key = "{doc_hash}:{prompt_hash}"
```

Dabei ist `prompt_hash` ein SHA-256-Hash (erste 16 Zeichen) über die
Konkatenation von System-Prompt und Body-Prompt, wie sie für den jeweiligen
`Doktyp` generiert werden. Jede Kombination aus Dokument und Prompt-Vorlage
erhält damit einen eigenen Cache-Eintrag. Gleiche Dokumente mit gleichem Prompt
treffen weiterhin den Cache.

**Auswirkung auf bestehende Cache-Einträge:** Alte Redis-Einträge im Format
`llm-semantics:{doc_hash}` werden nicht mehr gelesen. Sie bleiben als Waisen
gespeichert, bis der TTL (2 Wochen) abläuft, und verursachen keine Fehler.

**Implementierung:** `bawue_dok.py`:
- `_prompt_fingerprint(doktyp)` — SHA-256-Hash über System- und Body-Prompt des jeweiligen `Doktyp`
- `_cache_key(doc_hash, prompt_hash)` — Konkatenation zu `{doc_hash}:{prompt_hash}`
- `enrich_dokument()` — berechnet beide Hashes vor dem Cache-Lookup

---

## DD-021: Reservierte Gremium-Namen (`plenum`, `regierung`, `gesetzesblatt`)

**Datum:** 22.04.2026 | **Aktualisiert:** 22.04.2026 (Gap G1.4 + G4 aufgelöst)

**Kontext:** Drei Quellen definieren reservierte `Gremium.name`-Werte:

1. **OpenAPI-Spezifikation** (`vendor/pazufa-collector-core/openapi.yaml:1517`):
   > "Name des betreffenden Gremiums. `'plenum'`, `'regierung'`, `'volk'` sind
   > reservierte namen"
2. **Community-DoD-Wiki**: `regierung`, `plenum` (als Default, "wenn etwas
   'irgendwie passiert'"), **und `gesetzesblatt`** für die Veröffentlichung im
   Gesetzblatt.
3. **BY-Referenz-Scraper** (`vendor/pazufa-collector/collector/scrapers/bylt_scraper.py`):
   emittiert `gesetzesblatt` literal für `postparl-gsblt`-Stationen (Zeile 440)
   und `plenum` für alle anderen nicht-Ausschuss-Stationen — inkl. synthetisch
   erzeugte. BY hat **keinen** generischen Fallback wie `"Landtag"`.

**Diskrepanz Spec vs. Wiki**: Die Spec listet `volk`, aber nicht `gesetzesblatt`;
das Wiki listet `gesetzesblatt`, aber nicht `volk`. Die Spec-Beschreibung
validiert das Feld nicht schema-seitig — jeder String ist erlaubt. Der BY-Scraper
nutzt `gesetzesblatt` produktiv ohne Backend-Fehler. Damit wird die
Spec-Beschreibung als **unvollständig** betrachtet, nicht als exklusiv — das
Wiki + BY-Convention sind maßgeblich.

**Backend-Verhalten** (`pazufa-backend/src/db/insert.rs:545-609`,
`migrations/20250302145212_vorgang_setup.sql:9`):

- `gremium` hat `UNIQUE (parl, name, wp)` — jeder unterschiedliche Name erzeugt
  eine separate Zeile.
- Beim Insert läuft ein pg_trgm `SIMILARITY(name, $1) > 0.66`-Check, der bei
  Near-Misses `notify_new_enum_entry(...)` triggert — eine eingebaute Canary
  für schleichende Namens-Drift.
- `SIMILARITY('Landtag', 'plenum') ≈ 0` → kein Alert. Ein Wechsel
  `Landtag → plenum` ist für das Backend unsichtbar (erzeugt eine neue
  Gremium-Zeile; die alte verwaist, ohne Datenverlust).

**Entscheidung:** Alle Stationen bekommen einen kanonischen Namen — kein
deutschsprachiger Klartext-Fallback mehr. Das Routing erfolgt im Scraper
typ-gewahr:

| Kontext / Station-Typ                                    | Gremium-Name       |
|----------------------------------------------------------|--------------------|
| Fundstelle mit Ausschuss-Angabe                          | Ausschuss-Name     |
| `postparl-gsblt` (Gesetz, Bekanntmachung, Gesetzblatt)   | `gesetzesblatt`    |
| Alle übrigen (parl-*, preparl-regent, synthetische)      | `plenum` (Default) |
| Beteiligungsportal-Station (`preparl-regent`)            | `regierung`        |
| ICS-Plenarsitzung                                        | `plenum`           |

**Bewusst nicht geändert:**

- **Initiator-Strings** (`Autor.organisation = "Landesregierung"` etc.). Die
  reservierten Namen gelten ausschließlich für `Gremium.name`, nicht für
  `Autor.organisation`. Der Autor-String wird unverändert aus PARLIS
  übernommen (Roadmap #10 G2: kanonische Normalisierung der Autor-Strings
  steht noch aus).
- **`volk`** ist im `ReservedGremium`-Enum definiert, aber nicht eingesetzt —
  BW kennt derzeit keine `postparl-vesja`/`postparl-vesne`-Stationen im aktiven
  Vorgangstyp-Filter. Der Wert bleibt für zukünftige Volksantrag-Pfade
  verfügbar.

**Backend-Koordination für Daten vor dem Roll-out:** Durch den Wechsel
`"Landtag" → "plenum"` verwaist die bestehende `(BW, Landtag, 17)`-Gremium-Zeile.
Bereinigung per einmaliger SQL-Operation nach vollem Re-Scrape-Zyklus:

```sql
UPDATE station SET gr_id =
  (SELECT id FROM gremium WHERE parl=... AND name='plenum' AND wp=17)
WHERE gr_id =
  (SELECT id FROM gremium WHERE parl=... AND name='Landtag' AND wp=17);
DELETE FROM gremium WHERE parl=... AND name='Landtag' AND wp=17;
```

Auf Dev/Staging durch DB-Reset trivial. Produktions-Koordination mit
Backend-Team erforderlich.

**Implementierung:** `bawue/types.py` definiert die `StrEnum` `ReservedGremium`
mit den vier Werten (`PLENUM`, `REGIERUNG`, `VOLK`, `GESETZESBLATT`). Da
`StrEnum`-Member echte `str`-Instanzen sind, passieren sie die
`StrictStr`-Validierung auf `Gremium.name` ohne Konvertierung.

Verwendung:

- `bawue_vorgaenge_scraper.py::_determine_gremium(fund, station_typ)` — typ-
  abhängige Auswahl: Ausschuss-Name, `gesetzesblatt` bei `postparl-gsblt`,
  sonst `plenum`.
- `bawue_vorgaenge_scraper.py::_ensure_ablehnung_station` und
  `_ensure_initiativ_after_regbsl` — synthetische Stationen nutzen
  `ReservedGremium.PLENUM` (vorher hartcodiert `"Landtag"`).
- `bawue_beteiligung_scraper.py` (Station-Erstellung) — `ReservedGremium.REGIERUNG`.
- `ics_parser.py::extract_gremium_name` — `ReservedGremium.PLENUM` für
  Plenarsitzungen.

`tests/unit/test_enum_mapper.py::TestReservedGremiumNames` lockt die literalen
Werte gegen Spec + Wiki + BY-Convention.
`tests/unit/test_bawue_scraper.py::TestVorgangBuild::test_default_gremium_is_plenum`
und `test_gsblt_station_uses_gesetzesblatt_gremium` verifizieren das neue
Routing.

---

## DD-022: Kanonische Namen für `Autor.organisation`

**Datum:** 22.04.2026

**Kontext:** Die Community-DoD fordert (SHOULD):

> "Ihr SOLLTET einen Mechanismus haben um kanonische Namen zu mappen. Der
> 'Verband der Podologen', 'Podologieverband e.V.', 'Podologie-Verband' und der
> 'VdPod' könnten zum Beispiel dasselbe meinen"

Hintergrund: Das Backend hat zwar einen pg_trgm-Canary
(`SIMILARITY(organisation, $2) > 0.66`, s. DD-021), der Near-Misses loggt, aber
**keine** aktive Namensvereinheitlichung. Zwei Scraper, die dasselbe Gremium
unterschiedlich benennen, erzeugen zwei `autor`-Zeilen. Produktionsdaten aus
BY (`vendor/pazufa-collector/collector/tests/bylt_scraper/*.json`) zeigen das
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

---

## DD-023: `verfassungsaendernd` — Titel-Heuristik statt „omit object"

**Datum:** 22.04.2026

**Kontext:** Das Pflichtfeld `Vorgang.verfassungsaendernd` existiert im PaZuFa-
Datenmodell, aber die BaWue-Quellen exponieren es nicht:

- Die PARLIS-Suchmaske kennt keine entsprechende Facette (geprüft am
  22.04.2026: Wahlperiode, Zeitraum, Stand, Urheber, Deskriptor, Suchbegriff —
  kein Verfassungsänderungs-Flag).
- Die Detail-Metadaten liefern das Attribut ebenfalls nicht.
- Das Beteiligungsportal und das Gesetzblatt führen es nicht in ihren
  Process-Listen.

Die Community-DoD verlangt, dass Objekte ausgelassen werden müssen, wenn ein
Pflichtfeld nicht sinnvoll gefüllt werden kann („Wenn nicht-optionale Felder
aus euren Quellen nicht mit einem sinnvollen Wert gefüllt werden können, muss
das gesamte Objekt ausgelassen werden"). Wörtlich angewandt würde das für BaWue
bedeuten, **100 %** aller Vorgänge zu verwerfen, weil PARLIS das Attribut für
*keinen* Vorgang liefert. Das steht offensichtlich im Widerspruch zum
Projektziel.

**Entscheidung:** Der Wert wird aus dem `titel` **heuristisch abgeleitet**. Die
deutsche Gesetzgebungssprache nennt Verfassungsänderungen sehr stringent — Art.
64 der Landesverfassung BW verlangt eine 2/3-Mehrheit, und die entsprechenden
Gesetze tragen das im Titel:

| Phrasing                                   | Behandlung |
|--------------------------------------------|------------|
| `Änderung der Verfassung` / `Änderung der Landesverfassung` | `True` |
| `Verfassungsänderung` (nominal compound)   | `True` |
| alles Übrige                               | `False` |

Die Heuristik ist mit Absicht konservativ: Titel wie „Gesetz zur Stärkung des
Verfassungsschutzes" oder „Landesverfassungsschutzgesetz" matchen nicht (eine
Wortgrenze-Guard verhindert das Matching im Inneren von Komposita).

**Implementierung:** `src/bawue/types.py`, Funktion `is_verfassungsaendernd()`.
Eingebunden in:

- `bawue_vorgaenge_scraper.py` (Vorgang aus PARLIS-Titel)
- `bawue_beteiligung_scraper.py` (Vorgang aus Beteiligungsportal-Titel)

Ein zukünftiger `bawue_gesetzblatt_scraper.py` sollte die Funktion analog
anwenden (Gesetzblatt-Titel folgen derselben Konvention).

**Empirische Basis:** WP 17 (Stand 22.04.2026) enthält nach dem vollen
Scrape keine Titel, die auf die Heuristik treffen — Verfassungsänderungen sind
in BaWue historisch extrem selten (letzte reguläre Änderung der
Landesverfassung: 2015, WP 15). Der Default-Pfad bleibt also praktisch
`False`, und die Heuristik schützt lediglich gegen künftige Fehlklassifikation,
sollte doch ein Verfassungsänderungs-Vorgang eingebracht werden.

**DoD-Konflikt:** Die Abweichung von der „omit object"-Regel ist hiermit
dokumentiert (DoD-Regel „Abweichungen MÜSSEN dokumentiert sein"). Wenn das
Backend-Schema das Feld künftig als optional deklariert oder eine
`unknown`-Semantik bekommt, ist DD-023 zurückzunehmen und die Heuristik zu
entfernen.

**Tests:** `tests/unit/test_enum_mapper.py::TestIsVerfassungsaendernd`:

- Positive: kanonische Titel inkl. Groß-/Kleinschreibungs- und
  Whitespace-Toleranz sowie Nominal-Kompositum.
- Negative: `Verfassungsschutz`-Kompositum, reguläre Änderungsgesetze
  ohne Verfassungsbezug, leere/whitespace-only-Strings.
- `test_returns_bool_not_truthy` — Rückgabewert ist ein echter `bool`
  (JSON `true`/`false`), kein Match-Object-Proxy.

---

## DD-024: Plenarlesungen gleicher Runde werden konsolidiert

**Datum:** 23.04.2026

**Kontext:** Beim Staging-Lauf am 22.04.2026 wurden drei *Staatshaushaltsgesetz*-
Elternvorgänge (StHG 2022, 2023/2024, 2025/2026) vom Backend mit HTTP 400
*Track validation Failed* abgelehnt. Grund: Das Regex für `BW.gg-land-parl` in
`deploy/tracks.toml` erlaubt höchstens drei Plenarlesungen (`V A* V A* V`), die
Elternvorgänge emittierten aber 8–9 `parl-vollvlsgn`-Stationen, weil PARLIS für
jeden Einzelplan (Ministerium) eine eigene Fundstelle „Zweite Beratung" listet
und `enum_mapper.py` alle „Erste/Zweite/Dritte Beratung"-Texte gleich auf
`Stationstyp.PARL_MINUS_VOLLVLSGN` abbildet. DD-004 verbot bis dahin jegliche
Zusammenführung von Plenarstationen — das bewahrte zwar zu Recht die
Unterscheidung zwischen 1., 2. und 3. Lesung, erzwang aber pro Einzelplan-
Sitzungstag eine eigene Station.

Das OpenAPI-`Station`-Modell hat kein Feld für Kind-Stationen, bietet aber mit
`zp_start` (erste Aktion) + `zp_modifiziert` (letzte Aktion) + `dokumente: List[...]`
genau die Felder, um eine über mehrere Sitzungstage gespannte Phase in einer
einzigen Station abzubilden — inkl. aller pro-Tag-PDFs als Dokumente.

**Entscheidung:** Aufeinanderfolgende `parl-vollvlsgn`-Stationen mit
**identischem roh-`station_typ`-Text** (case-insensitive, getrimmt,
nicht-leer) und identischem Gremium werden zu einer Station zusammengeführt:

- `zp_start` = Minimum der beteiligten Fundstellendaten,
- `zp_modifiziert` = Maximum der beteiligten Fundstellendaten,
- `dokumente` = Vereinigung (die nachgelagerte Drucksachen-Deduplizierung in
  `_dedup_drucks` bleibt greifbar).

Unterschiedliche Rundentexte ("Erste Beratung" ≠ "Zweite Beratung" ≠
"Überweisung" ≠ "Dritte Beratung") bleiben **weiterhin getrennte Stationen**,
damit die semantische Unterscheidung zwischen 1., 2. und 3. Lesung erhalten
bleibt — die vom Track-Regex zwingend gefordert wird: Für eine erfolgreiche
Gesetzgebung verlangt die Regex mindestens zwei `V`-Stationen vor `J G K`.

**Defensive Vorgabe:** Ein leerer `station_typ`-Text (PARLIS-Parser konnte den
Rundenlabel nicht extrahieren) verhindert die Zusammenführung — im Zweifelsfall
bleibt es bei zwei getrennten Stationen.

**Begründung:**

- Semantisch korrekt: Mehrere Einzelplan-Debatten am 15./16./17. Dezember
  gehören zur selben 2. Lesung der Haushaltsberatung, nicht zu drei Lesungen.
- Modell-konform: Das Station-Schema sieht `zp_start`/`zp_modifiziert` genau
  für solche mehrtägigen Phasen vor.
- Regel-konform: Die resultierende Station-Liste (`V A V V J G …`) bleibt
  innerhalb der vom Track-Regex erlaubten drei `V`-Stationen.
- Null-Risiko für Bestandsfälle: Unterschiedliche Rundentexte werden weiterhin
  getrennt, womit alle bisher validen Vorgänge unverändert durchlaufen.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methoden `_try_merge_station()`
(neuer `PARL_MINUS_VOLLVLSGN`-Zweig vor der generischen Merge-Logik) und
`_collect_stationen()` (trackt `last_station_typ_str` über die Schleife).

**Tests:** `tests/unit/test_bawue_scraper.py::TestStationMerging` —
`test_vollvlsgn_same_typ_text_different_days_merged`,
`test_vollvlsgn_same_typ_text_same_day_merged`,
`test_vollvlsgn_three_same_typ_merged`,
`test_staatshaushaltsgesetz_consolidation_end_to_end` (End-to-End StHG-Muster
mit 9 Plenarfundstellen → 3 konsolidierte V-Stationen),
`test_vollvlsgn_empty_station_typ_not_merged` (defensive Vorgabe).
Die bestehenden Regressionstests
`test_consecutive_vollvlsgn_not_merged_even_with_documents` (Erste + Zweite,
unterschiedlicher Text) und `test_consecutive_vollvlsgn_ueberweisung_not_merged`
(Erste + Überweisung) bleiben unverändert und schützen davor, die
Rundenunterscheidung versehentlich zu verlieren.

**Offene Folgearbeiten (nicht in DD-024):**

- 51 weitere Staging-Ablehnungen mit „committee on initiativ day" (Stationstyp
  `parl-ausschber` unmittelbar nach `parl-initiativ` am selben Tag) benötigen
  eine Lockerung im Backend-Regex (`IA?`-Präfix analog zu `BB` in
  `deploy/tracks.toml`).
- Zwei Ablehnungen vom Typ `gg-land-volk` (Volksantrag) benötigen eine eigene
  Track-Definition für BW im Backend.

Beide Punkte sind Backend-seitig und werden separat adressiert.

---

## DD-025: `parl-ausschber` vor erster Plenarlesung wird nachgelagert

**Datum:** 10.05.2026

**Kontext:** Beim Staging-Lauf am 09.05.2026 lehnte das Backend 54 Vorgänge mit
HTTP 400 *Track validation Failed* ab. Der überwiegende Teil betraf
**Haushaltsgesetzgebung-Einzelpläne** über drei Haushaltsperioden (2022,
2023/2024, 2025/2026). Die Stationsreihenfolge sah jeweils so aus:

```
preparl-regbsl (Okt) → parl-initiativ (Nov, synthetisch nach DD-012)
                    → parl-ausschber (Nov, +1h nach DD-012-Bumps)
                    → parl-vollvlsgn (Dez, mehrfach)
```

Der Track `gg-land-parl = "((E*R+)?S)?I((VA*(Z|VJGK|VN|...))|Z)"` (s. DD-016)
verlangt **vor** `A` (`parl-ausschber`) zwingend mindestens ein `V`
(`parl-vollvlsgn`). Die Backend-Validierung rejektierte daher den
Ausschussbericht-Eintrag.

**Ursache in den Quelldaten:** PARLIS datiert die Fundstelle
„Beschlussempfehlung und Bericht" auf das Veröffentlichungsdatum der
Drucksache. Bei Haushalts-Einzelplänen erscheint diese Drucksache typischerweise
Mitte November, während die ersten Plenarberatungen erst Mitte Dezember
beginnen. Die Datierung ist sachlogisch: Der Finanzausschuss berät die
Einzelpläne **vor** der Schlussberatung im Plenum. Im Track-Modell wird
`parl-ausschber` aber als Zwischenphase **zwischen** Lesungen geführt — DD-024
hatte diese Folgearbeit bereits unter „committee on initiativ day" ausgewiesen
und auf eine Backend-Regex-Lockerung gehofft (`IA?`-Präfix analog zu `BB`).

**Entscheidung:** Die Korrektur erfolgt scraper-seitig, **nicht** im
Backend-Track. Wenn ein `parl-ausschber` chronologisch vor der ersten
`parl-vollvlsgn` desselben Vorgangs liegt, wird sein `zp_start` auf eine
Stunde nach der ersten `parl-vollvlsgn` gesetzt. Damit landet er kanonisch
zwischen erster und zweiter Lesung — der Position, die der Track für `A`
vorsieht.

- Die Listenposition der Station bleibt unverändert; das Backend sortiert
  Stationen vor der Track-Validierung nach `zp_start`, daher genügt die
  Anpassung des Zeitstempels.
- Mehrere `parl-ausschber` vor der ersten Lesung erhalten alle dasselbe
  Anker-Datum (`first_vollvlsgn.zp_start + 1h`). Gleiche Stationstypen
  dürfen `zp_start` teilen (s. `_enforce_total_ordering`).
- `zp_modifiziert` wird nur dann mitangehoben, wenn es sonst kleiner als der
  neue `zp_start` würde — die für mehrtägige Ausschussphasen relevante
  Obergrenze (DD-024) bleibt erhalten.
- Wenn keine `parl-vollvlsgn` existiert (selten, abgebrochene Vorgänge),
  bleibt der Ausschussbericht unverändert — es gibt keinen Anker.

**Begründung gegen Alternativen:**

1. **Backend-Lockerung (`IA?`-Präfix):** Würde das parlamentarische Recht
   verletzen — `IA` ohne dazwischenliegendes `V` impliziert eine
   Ausschussberatung **ohne** vorherige Plenarlesung, was die GO-LT BW
   §42 nicht vorsieht. DD-016 hält fest, dass Tracks parlamentarisches Recht
   abbilden, nicht Quelldaten-Eigenheiten.
2. **Station verwerfen:** Verlust eines belegten Verfahrensschritts inkl.
   der zugehörigen Drucksache (Dokument bleibt erhalten, aber ohne Trägerstation
   verwaist).
3. **Auf das nächste Plenardatum springen:** Weniger generisch als der
   `+1h`-Anker und schwerer zu testen — ohne klare Reihenfolgeregel zwischen
   `V`, `A` und einem ggf. zweiten `V` am selben Tag.

**Implementierung:** `bawue_vorgaenge_scraper.py`,
Methode `_ensure_ausschber_after_vollvlsgn()`. Aufgerufen in
`_build_vorgang()` zwischen `_ensure_ablehnung_station()` und
`_enforce_total_ordering()`. Die Reihenfolge ist wichtig: Erst alle
synthetischen Stationen (DD-010, DD-012) einfügen, dann den Ausschussbericht
neu datieren, dann die Tie-Breaker-Bumps (DD-016) — so werden eventuelle
neue Kollisionen am Anker-Slot (`first_vollvlsgn + 1h`) regulär aufgelöst.

**Tests:** `tests/unit/test_bawue_scraper.py::TestEnsureAusschberAfterVollvlsgn`:

- `test_haushalt_einzelplan_ausschber_retimed_after_first_vollvlsgn` —
  End-to-End-Muster eines Haushalts-Einzelplans (Gesetzentwurf →
  Beschlussempfehlung → drei Lesungen).
- `test_canonical_order_ausschber_after_vollvlsgn_unchanged` —
  Ausschussbericht **nach** Erster Lesung bleibt unverändert.
- `test_no_vollvlsgn_means_ausschber_left_alone` — kein Anker, keine
  Verschiebung.
- `test_multiple_ausschber_before_vollvlsgn_all_retimed` — mehrere Berichte
  werden auf denselben Anker gepinnt.
- `test_retimed_ausschber_does_not_collide_with_other_types` —
  Zusammenspiel mit `_enforce_total_ordering`.
- Vier `test_unit_*`-Tests gegen die statische Methode direkt, inkl.
  `zp_modifiziert`-Invariante und Leerlisten-Verträglichkeit.

**Verbleibende Folgearbeiten** (nicht in DD-025):

- 2 Volksantrag-Vorgänge vom Typ `gg-land-volk` (DD-024-Folgearbeit) —
  weiterhin Backend-seitig.
- Doppelte `parl-vollvlsgn` am selben Zeitstempel in den Haushaltsgesetz-
  Elternvorgängen 2023/2024 und 2025/2026 — eigene Untersuchung, vermutlich
  Konsolidierungs-Lücke aus DD-024 für Schlussabstimmungen.
  → **adressiert in DD-026**.
- 1 XSS-False-Positive in einer LLM-`zusammenfassung` (V-243670, Artefakt
  `</narrow>` aus dem Modell-Output) — getrennt zu behandeln.
  → **adressiert in DD-027**.

---

## DD-026: „Beschluss des Landtags in <Ordinal>er Beratung" gehört zur selben Lesungsrunde

**Datum:** 10.05.2026

**Kontext:** Der Staging-Lauf vom 10.05.2026 lehnte 22 Haushaltsgesetzgebung-
Einzelpläne (Staatshaushaltsplan 2023/2024, Einzelpläne 07/08/…) mit HTTP 400
*Track validation Failed* ab. Beispielhafte Stationsreihenfolge (V-222745,
sortiert nach `zp_start`):

```
preparl-regbsl (25.10.2022)
parl-initiativ (17.11.2022, synthetisch nach DD-012)
parl-vollvlsgn (14.12.2022, „Zweite Beratung")
parl-ausschber (14.12.2022 +1h, retimed nach DD-025)
parl-vollvlsgn (16.12.2022, „Beschluss des Landtags in Zweiter Beratung")
parl-vollvlsgn (21.12.2022, „Dritte Beratung")
parl-vollvlsgn (21.12.2022, „Beschluss des Landtags in Dritter Beratung")
```

Vier `V`-Stationen — der Track `BW.gg-land-parl =
"((E*R+)?S)?I((VA*(Z|VJGA*KA*|VN|VA*(Z|VJGA*KA*|VN)))|Z)"` erlaubt nach `I`
aber höchstens drei `V` (Pfad `V A* V A* V J G A* K A*`). Backend rejektierte
das vierte V („Beschluss des Landtags in Dritter Beratung").

**Ursache in den Quelldaten:** Bei BW-Haushalts-Einzelplänen listet PARLIS für
jede Lesungsrunde **zwei** Fundstellen — das Plenarprotokoll der Aussprache
(„Zweite Beratung", „Dritte Beratung") und die Drucksache mit dem formalen
Abstimmungsvermerk („Beschluss des Landtags in Zweiter/Dritter Beratung").
Beide bilden parlamentarisch denselben Verfahrensschritt ab (eine Lesung mit
ihrer Schlussabstimmung), unterscheiden sich nur in Dokumenttyp und Datum.
Das Mapping `Beschluss des Landtags in …` → `parl-vollvlsgn` ist absichtlich
und durch `TestBeschlussDesLandtagsInBeratung` regressionsgesichert
(siehe `enum_mapper.py`); DD-024 verschmolz aber nur Stationen mit
**zeichengleichem** `station_typ`-Text und ließ daher beide Drucksachen-
Varianten als separate `V`-Stationen stehen.

**Entscheidung:** `_same_round_label` in `bawue_vorgaenge_scraper.py` wird um
eine Lesungsrunden-Äquivalenz erweitert. Zwei Labels werden auch dann als
gleich behandelt, wenn beide ein gemeinsames Lesungs-Ordinal („Erste",
„Zweite", „Dritte", „Vierte", „Fünfte" — case-insensitive Stamm-Match) und das
Wort „Beratung" enthalten. Damit verschmelzen:

- `"Erste Beratung"` ≡ `"Beschluss des Landtags in Erster Beratung"`,
- `"Zweite Beratung"` ≡ `"Beschluss des Landtags in Zweiter Beratung"`,
- `"Dritte Beratung"` ≡ `"Beschluss des Landtags in Dritter Beratung"`.

Die existierende DD-024-Konsolidierungslogik (`_try_merge_station` /
`_collect_stationen`) übernimmt dann automatisch `zp_start` = frühestes
Datum, `zp_modifiziert` = spätestes Datum und konkateniert die `dokumente`-
Listen.

**Defensive Vorgaben:**

- Labels ohne `"Beratung"` (z. B. `"Überweisung"`, `"Schlussabstimmung"`,
  `"Gesetzesbeschluss des Landtags"`) liefern `_reading_round` → `None` und
  fallen damit auf den DD-024-Exact-Match zurück — die seit DD-024 gültigen
  Negativtests (`test_consecutive_vollvlsgn_ueberweisung_not_merged` etc.)
  bleiben unverändert grün.
- Leere Labels werden weiterhin defensiv als „nicht vergleichbar" behandelt
  (`return False`).

**Begründung gegen Alternativen:**

1. **Re-Mapping `Beschluss des Landtags in …` → `parl-akzeptanz`:** würde die
   `TestBeschlussDesLandtagsInBeratung`-Regression brechen und das Track-
   Regex weiterhin sprengen (zwei `J` ohne dazwischenliegendes `V J G K`-
   Abschluss-Muster). Verworfen.
2. **Backend-Regex-Lockerung (mehr `V`):** Würde das parlamentarische Modell
   verfälschen — die BW-Geschäftsordnung kennt für reguläre Gesetzgebung max.
   drei Lesungen, das Regex bildet das korrekt ab. DD-024 hatte denselben
   Trade-off bereits zugunsten Scraper-seitiger Konsolidierung entschieden.
3. **„Beschluss in …"-Fundstellen verwerfen:** Verlust der Drucksache mit
   dem formalen Abstimmungstext.

**Implementierung:** `bawue_vorgaenge_scraper.py`, neuer Helper
`_reading_round()` und Erweiterung von `_same_round_label()`. Aufrufkette
unverändert: `_collect_stationen` → `_try_merge_station` → `_find_merge_target`
→ `_same_round_label`.

**Tests:** `tests/unit/test_bawue_scraper.py::TestReadingRoundEquivalence`:

- `test_zweite_and_beschluss_in_zweiter_merged` — End-to-End mit zwei V-
  Fundstellen, Ergebnis: 1 V mit `zp_start`/`zp_modifiziert`-Spanne und 2
  Dokumenten.
- `test_haushalt_einzelplan_collapses_to_two_v_stations` — End-to-End-
  Reproduktion des V-222745-Musters: aus 4 V-Fundstellen werden 2 V-
  Stationen, sortierte Sequenz `S I V A V` passt in das Track-Regex.
- `test_different_rounds_still_separate` — `Erste Beratung` ≠ `Zweite
  Beratung`, weiterhin 2 separate V.
- `test_ueberweisung_not_merged_with_erste_beratung` — Defensive: kein
  „Beratung" → Exact-Match-Fallback → keine Verschmelzung.
- Sechs `test_unit_*`-Tests direkt gegen `_reading_round` und
  `_same_round_label` (Ordinal-Extraktion plain & „Beschluss in …", Negativ-
  Fälle, Empty-Label-Defensivverhalten).

Die DD-024-Tests (`TestStationMerging`,
`test_staatshaushaltsgesetz_consolidation_end_to_end`) und die
`TestBeschlussDesLandtagsInBeratung`-Mapping-Regressionen bleiben unverändert
grün.

---

## DD-027: LLM-Output-Sanitisierung gegen XSS-Validator des Backends

**Datum:** 11.05.2026

**Kontext:** Im Staging-Lauf 2026-05-10 (Container 22:45 → 06:06 CEST,
Cycle-Loop nach DD-026-Fix) lehnte das Backend V-243670 („Gesetz zur
Änderung des Universitätsklinika-Gesetzes (UKG) und anderer Gesetze") über
229 Cycles hinweg mit HTTP 400 ab:

```
body.stationen[4].dokumente[0].zusammenfassung:
  Validation error: xss detected
  [{"value": String("… mit Artikel 4 ab dem 1. Januar 2026.</narrow>")}]
```

DD-025 hatte den Artefakt bereits beobachtet und als Folgearbeit
ausgewiesen. Eine Inventur des aktuellen JSONL bestätigt: Genau **ein**
Vorgang ist betroffen, **kein** anderes LLM-Stringfeld
(`kurztitel`/`vorwort`/`schlagworte`) enthält im Lauf HTML-ähnliche Tokens.

**Ursache:** `gpt-5-nano` emittiert sporadisch ein abschließendes
`</narrow>`-Token am Ende längerer Antworten, obwohl der System-Prompt
ausdrücklich Formatierungen verbietet (`Füge keine Formatierungen oder
Hervorhebungen hinzu.`). Der Backend-XSS-Validator weist jeden String mit
`<` oder `>` ab. `normalize_volltext` schützt das `volltext`-Feld bereits
durch Guillemet-Substitution (DD-014), die LLM-Semantik-Felder
(`zusammenfassung`, `kurztitel`, `vorwort`, `schlagworte`) wurden bisher
**ungeprüft** in das `Dokument` übernommen.

**Entscheidung:** Zwei Sanitiser-Helper in `bawue_dok.py`
(`_sanitize_llm_text`, `_sanitize_llm_strings`) reinigen jeden
LLM-emittierten String an der Datenmodellgrenze in `enrich_dokument`:

1. Entferne wohlgeformte HTML-Tags via Regex `</?[a-zA-Z][^<>]{0,80}>` —
   trifft `</narrow>`, `<strong>`, `<br/>` usw., **nicht** aber
   mathematische Vergleiche wie `x < 5 und y > 3` (das Tag muss mit einem
   Buchstaben beginnen).
2. Verbleibende Einzel-Brackets werden auf Guillemets `‹` / `›` abgebildet
   (Defense-in-Depth, gleicher Mechanismus wie `normalize_volltext`).
3. Whitespace wird getrimmt; ein nach Reinigung leerer String wird zu
   `None` (das API-Modell lässt `None`-Felder weg statt Leerstrings zu
   senden, die das Backend ebenfalls ablehnt).

Sanitisiert werden im Erfolgspfad von `enrich_dokument`:
`zusammenfassung`, `kurztitel`, `vorwort` (Strings) sowie jedes Element
von `schlagworte` (Liste, leer-nach-Reinigung wird verworfen). Numerische
Felder (`meinung`, `trojanergefahr`) bleiben unangetastet — sie laufen
durch `_validate_scores`.

**Anwendungsort:** Die Sanitisierung passiert **nach** dem Cache-Lookup,
also direkt vor der `Dokument`-Konstruktion. So werden auch alte
Redis-/In-Memory-Cache-Einträge mit `</narrow>` (z. B. der seit Tagen
vergiftete V-243670-Eintrag) bei jedem Read transparent gereinigt — eine
manuelle Cache-Invalidierung ist nicht nötig.

**Begründung gegen Alternativen:**

1. **System-Prompt verschärfen / Re-Prompting:** würde nur das
   Wahrscheinlichkeitsmuster verschieben, nicht garantieren. Modelle wie
   gpt-5-nano emittieren weiterhin gelegentlich Formatierungs-Token.
2. **Sanitisierung in `extract_semantics` vor dem Caching:** Alte
   Cache-Einträge blieben vergiftet, manuelle Redis-Bereinigung wäre
   nötig.
3. **Pauschale Guillemet-Substitution wie in `normalize_volltext`:** Würde
   `‹/narrow›` als sichtbares Artefakt in der Zusammenfassung lassen.
   Tag-Stripping vorab ergibt sauberere Ausgabe; Guillemet-Pass bleibt als
   zweite Verteidigungslinie.

**Tests:** `tests/unit/test_bawue_dok.py`:

- `TestSanitizeLlmText` (10 Tests) — direkter Helper-Test:
  `</narrow>`-Trailing-Stripping, Inline-Tag-Stripping, selbstschließendes
  `<br/>`, Pass-through für sauberen Text, Guillemet-Substitution für
  echte Vergleichsoperatoren, leere/None-Eingabe → None,
  pathologische Lang-Tags (>80 Zeichen) fallen auf Bracket-Substitution
  zurück, Listen-Helper droppt Leer-Ergebnisse.
- `TestEnrichDokumentSanitization::test_narrow_artefact_stripped_from_zusammenfassung` —
  End-to-End: LLM-Mock liefert `</narrow>` und `<hr/>` zurück, das
  resultierende `Dokument` enthält keine Brackets in
  `zusammenfassung`/`vorwort`.

**Verifikation in Produktion:** Beim nächsten Staging-Cycle wird V-243670
ohne `Track validation Failed` für `zusammenfassung` durchlaufen. Die
beiden verbleibenden bekannten Fehler (`gg-land-volk` für V-230205 und
V-232608, DD-024-Folgearbeit) bleiben Backend-seitig.

---

## DD-028: Stabile `api_id` für dokumentlose Stationen

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

