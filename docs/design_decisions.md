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

## DD-004: Plenarlesungen werden nie zusammengeführt

**Datum:** 27.03.2026

**Kontext:** PARLIS liefert für einen Vorgang häufig mehrere Fundstellen mit dem
gleichen Stationstyp und Gremium hintereinander — z. B. zwei Ausschuss-Fundstellen
für denselben Ausschuss. Um Duplikate zu vermeiden, werden aufeinanderfolgende
Stationen gleichen Typs und Gremiums zu einer Station zusammengeführt (Merge).

**Entscheidung:** Stationen vom Typ `parl-vollvlsgn` (Plenarlesungen) werden **nie**
zusammengeführt. Jede Lesung (Erste, Zweite, Dritte Beratung) bleibt eine eigene
Station — auch wenn sie direkt aufeinander folgen und dasselbe Gremium `plenum`
(reservierter Name, s. DD-021) haben. Eine Zweite Beratung ist schlicht eine weitere Station vom Typ
`parl-vollvlsgn`, kein gesonderter Stationstyp.

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

