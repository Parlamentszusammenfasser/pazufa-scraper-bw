[← Index](../design_decisions.md)

# DD-061: `Dokument.zp_erstellt`/`zp_modifiziert` aus dem aufgedruckten Ausgabedatum der Drucksache (GitHub Issue #23)

**Datum:** 30.09.2026

**Kontext:** Beide Dokument-Zeitpunkte `zp_referenz` und `zp_modifiziert` waren das
Fundstellen-Datum aus PARLIS. Bei Beschlussempfehlungen ist das der Sitzungstag des
Ausschusses. Die Drucksache selbst wird erst später ausgegeben: 17/1102 trägt auf Seite 1
„Ausgegeben: 10.12.2021“, PARLIS nennt den 25.11.2021 (15 Tage), 17/1104 „Ausgegeben:
9.12.2021“ gegenüber dem 26.11.2021 (13 Tage). Die Spec unterscheidet drei Zeitpunkte:
`zp_referenz` = Sitzung, auf die sich das Dokument bezieht; `zp_erstellt` = erstellt;
`zp_modifiziert` = zuletzt geändert.

**Entscheidung:**

1. **`zp_referenz` bleibt das Fundstellen-Datum.** Das ist genau der Sitzungsbezug der Spec.
2. **`zp_erstellt` und `zp_modifiziert` = aufgedrucktes Ausgabedatum**, wenn der
   extrahierte Volltext den Kopf `Drucksache <WP> / <Nr> Ausgegeben: T.M.JJJJ` enthält
   (`bawue_dok.ausgegeben_datum`). Eine ausgegebene Drucksache wird nicht mehr geändert,
   also ist der Änderungs- gleich dem Ausgabezeitpunkt. Mitternacht UTC wie beim
   Fundstellen-Datum.
3. **Nur der eigene Kopf zählt:** WP und Nummer müssen exakt der `drucksnr` des Dokuments
   entsprechen. So wird nie der zitierte Kopf einer anderen Drucksache übernommen.
4. **Für alle Doktypen**, nicht nur Beschlussempfehlungen. Jede Landtags-Drucksache druckt
   diesen Kopf, Plenarprotokolle und Gesetzblätter haben keine `drucksnr` bzw. keinen
   solchen Kopf und behalten die PARLIS-Daten.

**Grenzen:**

- Nur bei aktivem LLM, denn nur dann lädt der Scraper das PDF (`enrich_dokument`). Ohne
  LLM, bei fehlgeschlagenem Download oder leerem Text bleiben die PARLIS-Daten.
- Bei `#page=N`-Ankern (N > 1) enthält das Textfenster Seite 1 nicht, dann bleibt es beim
  Fundstellen-Datum.
- Ein unmögliches Datum (OCR-Fehler) wird mit Warnung verworfen.
- Die Station (`zp_start`) ist nicht betroffen, die Track-Validierung also auch nicht.
- Bereits gecachte Vorgänge (`vg2:`) bekommen das Datum erst, wenn sie sich in PARLIS
  ändern oder ihr Cache-Eintrag gelöscht wird.
