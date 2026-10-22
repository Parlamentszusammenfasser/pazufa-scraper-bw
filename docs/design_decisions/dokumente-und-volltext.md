[← Index](../design_decisions.md)

# Dokumente und Volltext

PDF-Download, Textextraktion und Dokumentdaten (`pdf_text.py`, Anreicherung in `bawue_dok.py`). Läuft nur mit aktivem LLM;
ohne LLM lädt der Scraper keine PDFs, `volltext` bleibt `TODO`, `hash_` ein Platzhalter
(DD-048).

<a id="dd-015"></a>
## DD-015: Kaputte PDF-Schriften: Erkennung, OCR-Retry, XSS-Schutz

**Kontext:** Manche Landtags-PDFs (vor allem angehängte Stellungnahmen) haben Schriften mit
falschen Unicode-Zuordnungen: Latin-Extended-Zeichen statt Buchstaben oder eine
ASCII-Verschiebung um +29 mit C1-Steuerzeichen. Falsche `<`/`>` und E-Mail-Header lösten die
XSS-Prüfung des Backends aus (HTTP 400).

**Entscheidung:** Drei Stufen:
1. **Erkennen und OCR:** `_is_garbled()` schlägt an bei > 5 % Latin-Extended- oder > 0,5 %
   C1-Zeichen (bezogen auf Buchstaben, ganzes Dokument). Dann Tesseract-OCR (`deu`). Das
   Ergebnis gilt nur, wenn mehr nutzbarer Text übrig bleibt als beim Original
   (`_usable_length`), denn OCR ersetzt den ganzen Text, auch saubere Seiten.
2. **Absätze filtern:** `normalize_volltext()` verwirft Absätze mit schlechter Qualität
   (C1, Latin-Extended-B, vokallose lange Wörter, > 60 % Großbuchstaben).
3. **Spitze Klammern** werden zu `‹`/`›`.

**Konsequenz:** Kalibriert an 925 WP-17-PDFs: C1 tritt nur bei echtem Garbling auf, keine
Fehlalarme. OCR kostet viel (17/1104: 121 s statt 0,2 s) und rettet bis Faktor 4,6 mehr
Text. kreuzberg 4.x braucht eine explizite `OcrConfig`; mit Seiten-Hint auch Seitenmarker
(`_OCR_CONFIG_PAGE_MARKERS`).

<a id="dd-038"></a>
## DD-038: Leeres Seitenfenster fällt auf den Volltext zurück

**Kontext:** Ein `#page=N`-Anker kann auf eine leere Seite zeigen (nur Seitenzahl oder
„TODO", Drs. 17/3840, 17/1201). Genau das wurde als `volltext` gespeichert (Issue #50).

**Entscheidung:** Hat das Fenster weniger als `MIN_TEXT_LENGTH` (64) Zeichen, gilt der ganze
Dokumenttext.

<a id="dd-050"></a>
## DD-050: Seitenmarker am Textanfang und -ende zählen

**Kontext:** Der Seitenmarker-Regex verlangte `\n\n` auf beiden Seiten. Der Marker der
ersten Seite hat davor nichts: Seite 1 ging verloren, gespeichert wurde die Signaturseite
(GBl2026012.pdf, Issue #27).

**Entscheidung:** `(?:\A|\n\n)<!-- PAGE (\d+) -->(?:\n\n|\Z)` — Textanfang und -ende ersetzen
das `\n\n`.

<a id="dd-061"></a>
## DD-061: `Dokument.zp_modifiziert` ist das aufgedruckte Ausgabedatum

**Kontext:** Beide Zeitstempel waren das PARLIS-Datum (Sitzungstag bzw. Eingang). Jede
Drucksache druckt „Ausgegeben: T.M.JJJJ" in die Fußzeile von Seite 1 (Issue #23).

**Entscheidung:**
- `zp_modifiziert` = aufgedrucktes Ausgabedatum; danach ändert sich eine Drucksache nicht mehr.
- `zp_referenz` bleibt das PARLIS-Datum (Sitzungsbezug laut Spec), `zp_erstellt` bleibt leer.
- Erkannt nur, wenn der Text den eigenen Kopf `Drucksache <WP> / <Nr>` enthält; dann gilt das
  erste „Ausgegeben:". Liegt das Datum außerhalb von PARLIS-Datum + 90 Tage, wird es mit
  WARNING verworfen.

**Konsequenz:** Station-Daten und Track sind nicht betroffen.

<a id="dd-067"></a>
## DD-067: Kein Platzhalter-Dokument ohne `drucksnr` nach einem Download-Fehler

**Kontext:** Ein noch unveröffentlichtes Plenarprotokoll (HTTP 404) ging mit Platzhalter
hoch. Ohne `drucksnr` kann das Backend den Platzhalter später nicht dem echten Dokument
zuordnen; beide bleiben an der Station (V-247045: drei Rows).

**Entscheidung:** Scheitert der Download und fehlt die `drucksnr`, wird das Dokument nicht
gesendet; der Vorgang bleibt ungecacht und der nächste Lauf versucht es erneut. Drucksachen
behalten den Platzhalter, der `drucksnr`-Match aktualisiert ihren Row.

**Verworfen:** eine Dokument-`api_id` je Fundstelle. Gemeinsam beratene Vorgänge teilen eine
Protokollseite; das ergäbe `AmbiguousMatch` (HTTP 409) in jedem Lauf.

**Konsequenz:** Bis zur Veröffentlichung hat die Station kein Dokument. Bei gemeinsamer
Beratung verlinkt das Backend das Protokoll nur beim ersten Vorgang (Backend-Fehler). Der
Beteiligungsportal-Scraper wertet `download_failed` nicht aus.
