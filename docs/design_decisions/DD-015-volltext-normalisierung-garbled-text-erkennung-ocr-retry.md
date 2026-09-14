[← Index](../design_decisions.md)

# DD-015: Volltext-Normalisierung — Garbled-Text-Erkennung, OCR-Retry und XSS-Prävention

**Datum:** 02.04.2026 | **Aktualisiert:** 02.08.2026

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
   prüft `_is_garbled()` beide oben beschriebenen Muster:
    - **Muster 1:** mehr als 5 % der alphabetischen Zeichen im
      Latin-Extended-Bereich (U+0100–U+024F).
    - **Muster 2:** mehr als 0,5 % C1-Steuerzeichen (0x80–0x9F), bezogen auf
      die alphabetischen Zeichen.

   Bei positivem Befund wird die Extraktion mit Tesseract-OCR (Sprache: `deu`)
   wiederholt. OCR rendert jede Seite als Bild und erkennt die Zeichen visuell
   — die fehlerhaften Font-Mappings werden damit umgangen. Das OCR-Ergebnis
   wird nur übernommen, wenn es tatsächlich besser ist (nicht selbst garbled);
   andernfalls bleibt der Originaltext erhalten.

   **Wichtig:** `ExtractionConfig(force_ocr=True)` allein löst in kreuzberg
   4.x kein OCR aus — es muss zusätzlich `ocr=OcrConfig(backend="tesseract",
   language="deu")` gesetzt werden. Ohne explizite OCR-Konfiguration gibt
   kreuzberg identischen (garbled) Text zurück.

   Ebenso muss der OCR-Retry bei gesetztem `#page=N`-Hint
   `pages=PageConfig(insert_page_markers=True)` mitführen
   (`_OCR_CONFIG_PAGE_MARKERS`): OCR ersetzt den kompletten Text, und ohne
   Marker fällt `_extract_relevant_pages()` stillschweigend auf das
   Gesamtdokument zurück — alle Anträge einer Sammeldrucksache bekämen
   denselben Volltext.

   **Nachtrag (GitHub-Issue #20, 02.08.2026):** Die Muster-2-Prüfung fehlte
   ursprünglich, `_is_garbled()` maß nur die Latin-Extended-Ratio. Drucksache
   17/1201 (nur Muster 2 betroffen, Latin-Extended-Anteil 0 %) lief daher am
   OCR-Retry vorbei; Stufe 2 entfernte die garbled Absätze, und die Stationen
   landeten mit leerem Volltext (`TODO_MARKER`) statt mit dem per OCR
   rekonstruierbaren Inhalt.

   *Schwellenwert-Kalibrierung* an 925 realen Landtag-BW-PDFs (alle
   Dokument-URLs aus einem WP17-Lauf): 85,7 % der Dokumente enthalten
   **kein einziges** C1-Zeichen — C1 ist in diesem Korpus kein Rauschen,
   sondern markiert ausnahmslos echte Garbling-Stellen. Die Trefferquote
   steigt durch die neue Prüfung von 0,4 % auf 7,5 % (65 zusätzliche
   Dokumente). Alle 65 wurden nachgemessen: 84 % verlieren durch Stufe 2
   mehr als 60 % ihres Textes, 98 % mehr als 30 % — es sind keine
   False Positives, sondern echte Rettungsfälle. Referenzmessung an
   Drucksache 17/1104: nativ 204 780 Zeichen, davon nur 46 660 (22,8 %)
   nach Normalisierung übrig; per OCR 216 097 Zeichen, davon 213 871
   (99,0 %) — Faktor 4,6 mehr Inhalt, Kosten 120,8 s statt 0,2 s bei
   0,85 GB Peak-RSS.

   Beide Ratios werden über das *gesamte* Dokument gemessen. Das hat zwei
   Konsequenzen: ein langes, überwiegend sauberes PDF mit wenigen garbled
   Seiten bleibt unter der Schwelle (und wird weiterhin nur von Stufe 2
   abgefangen); umgekehrt schlägt die Prüfung auch bei nur *teilweise*
   kaputten PDFs an (gemessen ~15 % des betroffenen Korpus behalten
   40–97 % ihres Inhalts). Da OCR den **kompletten** Text ersetzt — auch
   die sauber extrahierten Seiten — und bei Tabellen selbst verlustbehaftet
   ist, reicht „nicht garbled" als Annahmekriterium nicht aus. Das
   OCR-Ergebnis wird deshalb zusätzlich nur übernommen, wenn
   `_usable_length()` (Zeichen, die `normalize_volltext()` überleben) auf
   der OCR-Seite *größer* ist als beim Originaltext.

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

- `_is_garbled()` — Latin-Extended-Ratio > 5 % **oder** C1-Ratio > 0,5 % der
  Alpha-Zeichen
- `extract_pdf_text()` — OCR-Retry mit `_OCR_CONFIG` bzw.
  `_OCR_CONFIG_PAGE_MARKERS` (bei `#page=N`-Hint) bei garbled Text
- `_usable_length()` — Annahmekriterium für das OCR-Ergebnis
- `_paragraph_quality_score()` — Multi-Signal-Bewertung pro Absatz
- `normalize_volltext()` — Absatzfilterung, NFKC, C1-Stripping,
  CRLF-Normalisierung, Angle-Bracket-Ersetzung
