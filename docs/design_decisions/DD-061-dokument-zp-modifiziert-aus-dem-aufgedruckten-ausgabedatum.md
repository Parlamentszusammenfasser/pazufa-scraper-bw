[← Index](../design_decisions.md)

# DD-061: `Dokument.zp_modifiziert` aus dem aufgedruckten Ausgabedatum der Drucksache (GitHub Issue #23)

**Datum:** 30.09.2026

**Kontext:** `zp_referenz` und `zp_modifiziert` waren beide das Fundstellen-Datum aus
PARLIS. Bei Beschlussempfehlungen ist das der Sitzungstag des Ausschusses, bei Entwürfen
und Anträgen das Eingangsdatum. Ausgegeben wird die Drucksache erst später: 17/1102 trägt
„Ausgegeben: 10.12.2021“, PARLIS nennt den 25.11.2021. Die Spec unterscheidet
`zp_referenz` (Sitzung, auf die sich das Dokument bezieht), `zp_erstellt` (erstellt) und
`zp_modifiziert` (zuletzt geändert).

**Befund (150 Staging-Dokumente, 30.09.2026):** Jede Landtags-Drucksache druckt das
Ausgabedatum genau einmal in die Fußzeile von Seite 1: `Ausgegeben: 10.12.2021`, bei
Entwürfen und Anträgen `Eingegangen: 19.10.2021/Ausgegeben: 20.10.2021`. Wo diese Zeile
im extrahierten Text landet, schwankt stark: direkt nach dem Kopf `Drucksache 17 / 1102`,
davor, nach dem Titelblock (OCR) oder erst nach Seite-2-Text. Mit der Regel unten erhalten
77 von 79 infrage kommenden Dokumenten ein Datum, alle 0 bis 42 Tage nach dem
PARLIS-Datum (Beschlussempfehlungen bis 42, Entwürfe bis 16, Mitteilungen bis 13).

**Entscheidung:**

1. **`zp_modifiziert` = aufgedrucktes Ausgabedatum** (`bawue_dok.ausgegeben_datum`). Eine
   ausgegebene Drucksache ändert sich nicht mehr. Mitternacht UTC wie beim Fundstellen-Datum.
2. **`zp_referenz` bleibt das Fundstellen-Datum**: genau der Sitzungsbezug der Spec.
3. **`zp_erstellt` bleibt leer.** Erstellt ist eine Drucksache mit dem Eingang (bei
   Entwürfen/Anträgen = PARLIS-Datum, 17/1218: Eingang 14.12.2021, ausgegeben 7.1.2022),
   nicht mit der Ausgabe. Für Beschlussempfehlungen ist das Erstelldatum unbekannt.
4. **Erkennung:** Der Text muss den eigenen Kopf `Drucksache <WP> / <Nr>` (exakt die
   `drucksnr`) enthalten, sonst ist es nicht das PDF dieser Drucksache (Plenarprotokoll,
   Gesetzblatt). Dann gilt das erste `Ausgegeben: T.M.JJJJ` im Text, unabhängig von der
   Position.
5. **Plausibilität:** Ein Datum außerhalb `[PARLIS-Datum, PARLIS-Datum + 90 Tage]` oder ein
   unmögliches Datum (OCR-Fehler) wird mit Warnung verworfen. Das PARLIS-Datum bleibt dann.

**Grenzen:**

- Nur bei aktivem LLM, denn nur dann lädt der Scraper das PDF (`enrich_dokument`). Ohne
  LLM, bei fehlgeschlagenem Download oder leerem Text bleibt das PARLIS-Datum.
- Fenster `#page=N` mit N > 1 (Sammeldrucksachen) enthalten Seite 1 nicht. Eine Fußzeile
  darin gehört zu einem anderen Dokument der Datei. Sie werden nicht ausgewertet.
- Station (`zp_start`) und damit die Track-Validierung sind nicht betroffen.
- Bereits gecachte Vorgänge (`vg2:`) erhalten das Datum erst, wenn sie sich in PARLIS
  ändern oder ihr Cache-Eintrag gelöscht wird.
