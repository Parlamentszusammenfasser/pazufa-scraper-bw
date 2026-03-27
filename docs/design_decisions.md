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

**Datum:** 27.03.2026

**Kontext:** PARLIS verwendet den gleichen Fundstellentext „Gesetzentwurf" sowohl für
Regierungsentwürfe (vorparlamentarisch) als auch für parlamentarische Initiativen
(z. B. Fraktionsentwürfe). Ohne zusätzlichen Kontext würde jeder Gesetzentwurf
einheitlich als `parl-initiativ` klassifiziert — Regierungsentwürfe gingen als
vorparlamentarische Phase verloren.

**Entscheidung:** Das Mapping von „Gesetzentwurf" hängt vom Initiator ab:

- Initiator enthält „Landesregierung" → `preparl-regent` (Regierungsentwurf)
- Sonst → `parl-initiativ` (parlamentarische Initiative)

Analog wird der Dokumententyp kontextabhängig zugeordnet:

- Station ist `preparl-regent` → `preparl-entwurf`
- Sonst → `entwurf`

Dies ist das einzige Enum-Mapping, das externen Kontext (den Initiator) benötigt.

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
Station — auch wenn sie direkt aufeinander folgen und dasselbe Gremium „Plenum"
haben. Eine Zweite Beratung ist schlicht eine weitere Station vom Typ
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

| SUMMARY-Präfix                                   | Gremium                 |
|--------------------------------------------------|-------------------------|
| `Plenarsitzung:`                                 | Plenum                  |
| `Fraktions- und Ausschusssitzungen: Ausschuesse` | Ausschusssitzungen      |
| `Fraktions- und Ausschusssitzungen: FinA`        | Finanzausschuss         |
| `Haushaltsberatungen:`                           | (aus Suffix extrahiert) |

Ausgeschlossen werden: **Fraktionen** (parteiinterne Sitzungen),
**Präsidium** (Verwaltung), **Wahl** (Verfassungsereignis). Diese Events werden
still übersprungen.

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
