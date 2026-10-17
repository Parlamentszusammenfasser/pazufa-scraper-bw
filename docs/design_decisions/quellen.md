[← Index](../design_decisions.md)

# Quellen

Welche Quellen der Scraper nutzt und wofür. Der Landtag hat keine offizielle API.

<a id="dd-014"></a>
## DD-014: PARLIS: JSON-Kommentare zuerst, HTML als Fallback

**Kontext:** PARLIS liefert HTML. Jede Ergebniszeile steckt zusätzlich als JSON in einem
HTML-Kommentar (`<!--{...}-->`) mit stabilen Feldcodes (`EWBV10` Titel, `WMV35`
Fundstellen …). Das JSON ist undokumentiert und kann wegfallen.

**Entscheidung:** `parse_results()` liest zuerst die JSON-Kommentare; nur wenn sie fehlen oder
nichts liefern, das HTML per XPath. Der HTML-Pfad bleibt vollständig erhalten.

<a id="dd-007"></a>
## DD-007: Beteiligungsportal: nur Prozesse mit eigenem Entwurf-PDF, ohne Verordnungen

**Kontext:** Das Portal enthält auch Informationsseiten und Verordnungen. Verordnungen gehen
nie durch den Landtag, wurden aber als `gg-land-parl` hochgeladen (Issue #71).

**Entscheidung:** Übernommen wird ein Prozess nur, wenn er mindestens ein **auf dem Portal
gehostetes** PDF hat (`a.link-download-block`, `a.link-list__link`; externe PDFs sind
Hintergrund, Issue #45) und seine Phasen-Timeline nicht „Beschluss der geltenden
Verordnung(en)" enthält. Auf 135 Seiten traf das alle 17 Verordnungen und keinen
Gesetzentwurf; PDF-Titel taugen dafür nicht.

<a id="dd-047"></a>
## DD-047: Gesetzblatt als Datums-Lookup, nicht als eigene Vorgangsquelle

**Kontext:** PARLIS datiert die `postparl-gsblt`-Station auf die Ausfertigung, nicht auf die
Ausgabe des Gesetzblatts (Issue #9). Der erste Ansatz (DD-044) scheiterte: ein Vorgang nur
aus `G` ist nie track-gültig (DD-040), und das Gesetzblatt nennt keine Drucksache. Ein
`vg_ident` aus (Jahr, Nr.) wäre schädlich: eine Ausgabe enthält oft mehrere Gesetze (WP 17:
18 Schlüssel mehrfach), das Backend würde sie verschmelzen (vgl. DD-041).

**Entscheidung:** Das Gesetzblatt ist ein Lookup für den PARLIS-Scraper:
- `parse_fundstelle_text` hält (Jahr, Nr.) fest; `GesetzblattDateLookup` löst sie über die
  Detailseite auf und cacht Treffer **und** Fehlschläge.
- Die Station bekommt das Ausgabedatum; das Dokument behält die Ausfertigung als
  `zp_referenz`.
- Das Dokument verlinkt das offizielle PDF samt `#page`-Anker; der Landtag spiegelt es nur
  lückenhaft. Der Link ist signiert (`dl=1`): Ändert die Seite ihren Schlüssel, liefern alte
  Links 403.
- Das PDF liefert auch das Inkrafttreten (DD-071).

**Konsequenz:** Digital gibt es das Gesetzblatt erst ab 2024; ältere Zitate behalten das
PARLIS-Datum und den PARLIS-Link. Gesetzblatt-Einträge ohne PARLIS-Vorgang (Verordnungen)
erscheinen nicht. Die Station erbt die Wahlperiode ihres Vorgangs.

<a id="dd-044"></a>
## DD-044: Eigener Gesetzblatt-Scraper — abgelöst durch DD-047

Ein eigener Scraper erzeugte Vorgänge nur aus einer `postparl-gsblt`-Station. Alle wurden
vom Track abgelehnt; der geplante Merge über `initdrucks` war nicht möglich. Siehe DD-047.
