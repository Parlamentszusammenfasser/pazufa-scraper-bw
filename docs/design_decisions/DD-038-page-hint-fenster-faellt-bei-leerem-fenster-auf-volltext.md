[← Index](../design_decisions.md)

# DD-038: Page-Hint-Fenster fällt bei leerem Fenster auf Volltext zurück (Issue #50)

**Datum:** 12.07.2026

**Kontext:** Ein `#page=N`-Fragment in einem Dokumentlink kann auf eine faktisch
leere Seite zeigen (nur eine laufende Seitenzahl oder ein „TODO"-Platzhalter).
`_extract_relevant_pages` schnitt das Fenster ab Seite `N` heraus und gab exakt
diesen Inhalt zurück — der dann als `volltext` des Dokuments persistiert wurde.
Belegt an Drucksache 17/3840 (Seite 2 → „TODO") und Drucksache 17/1201
(Seite 2 → „6\n\n6"); beide PDFs extrahieren ohne Seiten-Fragment vollständig.

**Entscheidung:** Liefert das Page-Hint-Fenster nach dem Zusammenfügen weniger
als `MIN_TEXT_LENGTH` (64) nutzbare Zeichen (`len(windowed.strip())`), verwirft
`_extract_relevant_pages` das Fenster und gibt den **vollständigen** Dokumenttext
zurück (mit `logger.info`-Hinweis). Damit reiht sich der Fall bei den bereits
bestehenden Rückfällen von `_extract_relevant_pages` ein (keine Seitenmarker,
Seite jenseits des Dokumentendes). Der Schwellwert ist dieselbe Konstante, die
`extract_pdf_text` als „hier ist zu wenig Text"-Signal (OCR-Retry) nutzt.

Bewusste Eingrenzung: Das ±30-Seiten-Fenster (DD-029) bleibt der Normalfall; der
Rückfall greift nur, wenn das gesamte Fenster unter der Mindestlänge liegt — ein
kurzes Startseiten-Fragment mit Folgeseiten voller Inhalt bleibt unberührt.

**Implementierung:** `bawue_dok.py` — `_extract_relevant_pages()`.

**Tests:** Unit (`tests/unit/test_bawue_dok.py::TestPageHintExtraction`):
`test_extract_relevant_pages_near_empty_window_falls_back` („6\n\n6"-Seite),
`test_extract_relevant_pages_todo_only_window_falls_back` („TODO"-Seite),
`test_extract_relevant_pages_substantial_window_kept` (ausreichend gefülltes
Fenster wird unverändert eingegrenzt).
