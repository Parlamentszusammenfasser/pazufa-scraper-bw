[← Index](../design_decisions.md)

# DD-033: Initiativdrucksache als Anker für Abschnitts-Extraktion + im Cache-Schlüssel (Issue #35)

**Datum:** 10.07.2026

**Kontext:** Issue #35 meldete erneut das Muster aus Issue
#32/[DD-029](DD-029-keine-token-kuerzung-dokumentkontext-im-llm-prompt.md):
Bei einem von mehreren Vorgängen geteilten Plenarprotokoll-PDF beschreibt die
`zusammenfassung` das falsche Gesetz. Der Kern war zum Zeitpunkt des Reports
bereits durch DD-029 behoben (`narrow_to_relevant_section()` +
Titel/Drucksache im `_prompt_fingerprint()`); ein Re-Scrape (166 Vorgänge, WP17)
zeigte über 96 mehrfach referenzierte PDFs **keine** identischen Zusammenfassungen
mehr, und der kanonische #32-Fall (Fischerei- vs. Open-Data-Debatte im selben
Protokoll `17_0012_...`) war korrekt getrennt.

Die Analyse fand jedoch zwei Resthärtungslücken:

1. **Ungenutzter Drucksache-Anker.** `narrow_to_relevant_section()` reichte
   `vorgang_vnr=dok.drucksnr` an `extract_relevant_section()` durch — für
   `redeprotokoll`-Dokumente ist das jedoch **immer `None`** (das Protokoll trägt
   keine eigene Drucksache). Die *Initiativdrucksache* des Vorgangs (z. B. 17/529),
   der stärkste Disambiguierungsanker gerade bei titelähnlichen Vorlagen
   (Haushalts-Einzelpläne), wurde verworfen, obwohl `SECTION_EXTRACTION_PROMPT`
   einen `(Drucksache …)`-Zusatz unterstützt.
2. **Cache-Korrektheit hing allein an unterschiedlichen Titeln**, nicht an einem
   expliziten Pro-Vorgang-Schlüssel (wie Issue #35 wörtlich vorschlug).

**Entscheidung:**

- **Initiativdrucksache vorab ableiten und durchreichen.** `_build_vorgang()`
  ermittelt die Initiativdrucksache jetzt vor dem Stationenbau aus den rohen
  Fundstellen (`_initiativ_drucksnr_from_fundstellen()`, spiegelt
  `_initiativ_drucksnr()` auf Rohdaten), da die Anreicherung *während* des Baus
  läuft. Der Wert wird analog zum Vorgangstitel über `_collect_stationen` →
  `_build_station` → `_build_dokumente` bis `enrich_dokument()` als `vorgang_vnr`
  durchgereicht.
- **Anker für die Abschnitts-Extraktion.** `narrow_to_relevant_section()` erhält
  `vorgang_vnr or dok.drucksnr` — für Protokolle also die Initiativdrucksache
  statt `None`.
- **Pro-Vorgang-Cache-Schlüssel (Defense-in-Depth).** `_prompt_fingerprint()`
  faltet `vorgang_vnr` in den Hash. Da die Initiativdrucksache den Vorgang
  eindeutig identifiziert, bleiben zwei Gesetze desselben Protokolls auch bei
  identischem Titel auf getrennten Cache-Einträgen. Bestehende
  `llm-semantics:*`-Einträge werden dadurch invalidiert (unkritisch — sie bauen
  sich beim nächsten Lauf neu auf).

Bewusste Eingrenzung: Das ±30-Seiten-Fenster (DD-029) bleibt als Vorfilter
erhalten; diese Änderung entfernt es nicht.

**Implementierung:** `bawue_dok.py` — `_prompt_fingerprint()`, `enrich_dokument()`,
`narrow_to_relevant_section()`-Aufruf; `bawue_vorgaenge_scraper.py` —
`_initiativ_drucksnr_from_fundstellen()` und Durchreichen von `vorgang_vnr`.

**Tests:** `tests/unit/test_bawue_dok.py`
(`TestPromptFingerprintContext::test_same_title_different_vorgang_vnr_yield_different_fingerprints`,
`TestNarrowToRelevantSection::test_enrich_passes_vorgang_vnr_to_section_extractor`);
`tests/unit/test_bawue_scraper.py` (`TestInitiativDrucksnrFromFundstellen`).
