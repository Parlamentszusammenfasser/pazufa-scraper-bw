[← Index](../design_decisions.md)

# DD-069: Sitzungskalender — der ICS-Feed bleibt die Quelle, Ausbau statt Ersatz (GitHub Issue #85)

**Datum:** 16.10.2026

**Kontext:** Issue #85 verlangt eine Bewertung der Kette ICS-Feed → `ics_parser` →
`BawueSitzungenScraper` → `PUT /api/v2/kalender`: behalten, umbauen oder ersetzen. Geprüft am
16.10.2026 gegen den Feed (176 Termine, 20.10.2026 bis 15.12.2027), den Backend-Code
(`pazufa-backend` `main`, `f87fd72`) und die Website des Landtags.

**Befunde:**

1. **Collector-Keys schreiben nur Tage ab gestern.** `kal_date_put` antwortet bei `datum` vor
   gestern (UTC) mit Erfolg, schreibt aber nichts. Sitzungen mit älterem `termin` verwirft es still.
   `PUT` und `DELETE /sitzung/{api_id}` verlangen Admin-Scope. Vergangene Sitzungen kann also keine
   Quelle nachträglich ergänzen, weder um TOPs noch um Protokolle. Dass der Feed nur vorausschaut,
   ist deshalb keine Lücke.
2. **Keine andere Quelle reicht so weit voraus.** PARLIS führt künftige Sitzungen nicht mehr als
   Station (DD-068). Die Tagesordnungen-Seite zeigt nur die nächste Plenarsitzung, das
   Sitzungsplan-PDF hat keine Nummern (DD-064).
3. **Erledigt:** Geänderte Tage werden neu hochgeladen (DD-066). Der Run-Report zählt neue und
   gecachte Tage getrennt (#83). `PUT kalender` mit `X-Scraper-Id` lief am 11.10.2026 auf Staging
   und Prod ohne Fehler (47 von 47 Tagen).
4. **Der Feed nennt die Ausschüsse jetzt.** Statt des Sammel-Eintrags `Ausschuesse` stehen Kürzel
   im SUMMARY, oft mehrere je Termin (`FinA, BildungsA, VerkA`). DD-006 schloss Ausschüsse nur
   wegen des fehlenden Namens aus. `extract_gremium_name` verglich das ganze Suffix mit `FinA` und
   verwarf so 104 Ausschusssitzungen, darunter 8 des Finanzausschusses (behoben, #117).
5. **Kalender und Vorgänge benannten denselben Ausschuss verschieden** (behoben, #117):
   `Finanzausschuss` im Kalender, `Ausschuss für Finanzen` (PARLIS) in den Stationen. Das Backend
   sucht Gremien über den exakten Namen (`insert_or_retrieve_gremium`) und legte deshalb zwei an.
6. **Eine auf einen anderen Tag verlegte Sitzung scheiterte** (behoben, #116). Die `api_id` war
   `uuid5(UID)`, im Backend ist `sitzung.api_id` `UNIQUE`. `PUT` löscht nur die Sitzungen des
   Zieltags. Behielt der Landtag die UID beim Verlegen, kollidierte der Insert mit der Zeile am
   alten Tag. Der Tag blieb ungecacht und scheiterte in jedem Lauf, bis der alte Tag neu hochgeladen
   wurde. Fiel der alte Tag ganz weg, wurde er nie neu hochgeladen. DD-066 hatte angenommen, der
   neue Tag werde hochgeladen.
   Ob das CMS die UID beim Verlegen behält, ist nicht geprüft.
7. **Ein ganz entfallener Tag lässt sich nicht leeren.** Bei leerem Body bricht `kal_date_put` vor
   dem `DELETE` ab. Der Scraper kann die letzte Sitzung eines Tages also nicht entfernen
   (DD-066, „Nicht abgedeckt“).
8. **Für TOPs (#66):** Trifft ein Dokument im TOP genau ein bestehendes Dokument (über den Hash,
   die `api_id` oder `drucksnr` + `typ` + `zp_referenz`), verknüpft das Backend es und überschreibt
   nichts (`insert_dokument`, `MatchState::ExactlyOne`). Ohne Treffer oder bei mehreren legt es ein
   neues Dokument an. Auf Staging und Prod ist die LLM-Anreicherung aktiv, Drucksachen tragen dort
   also den echten PDF-Hash.

**Entscheidung:**

1. **Behalten und ausbauen, nicht ersetzen.** Der ICS-Feed bleibt die Quelle der Termine.
   Geplantes steht im Kalender, Geschehenes in den Stationen (DD-068).
2. **Die Tagesordnung ergänzt die Plenarsitzung.** TOPs und `nummer` kommen aus dem PDF der
   nächsten Sitzung (#66). Sie gehen im selben `PUT` wie der ICS-Termin, weil `PUT` den ganzen Tag
   ersetzt.
3. **Alle benannten Ausschüsse:** je Kürzel eine Sitzung mit dem PARLIS-Namen des Ausschusses
   (#117). DD-006 wird dabei überarbeitet. Weiter ausgeschlossen bleiben `Fraktionen`, das leere
   Suffix, `Präsidium` und `Gedenktag`.
4. **Die `api_id` enthält das Datum**, damit eine verlegte Sitzung nicht mit ihrer alten Zeile
   kollidiert (#116). Die alte Zeile bleibt am alten Tag, bis er neu hochgeladen oder geleert wird
   (Entscheidung 5).
5. **Entfallene Tage leeren** braucht eine Backend-Änderung: Ein leerer Body soll den Tag leeren.
   Bis dahin bleibt die Lücke aus DD-066.

**Verworfen:**

- *PARLIS oder die Website als Hauptquelle:* Keine reicht so weit voraus (Befund 2).
- *Vergangene Sitzungen nachträglich vervollständigen:* Collector-Keys dürfen das nicht (Befund 1).
- *Einzelne Sitzungen per `PUT /sitzung/{api_id}` setzen:* Der Endpunkt verlangt Admin-Scope.

**Nicht abgedeckt:** Das Backend leert einen Tag nach UTC (`kal_date_put`), der Scraper gruppiert
nach dem Berliner Tag. Ein ganztägiger Termin oder einer vor 01:00 (MEZ) bzw. 02:00 (MESZ) liegt in
UTC am Vortag. Ein erneuter Upload seines Tages löscht ihn dann nicht und kollidiert mit der
eigenen `api_id`; ein Upload des Vortags löscht ihn. Der Feed vom 16.10.2026 hat keinen solchen
Termin.

**Code:** Entscheidung 3: `ics_parser.extract_gremium_names` (#117, DD-006). Entscheidung 4:
`BawueSitzungenScraper.item_extractor` (`api_id` aus UID und Datum, #116, seit #117 auch Gremium).
Sonst kein Code.
Korrigiert: das Zeitfenster von `PUT kalender` in `anforderungen.md`, die Verlegung in DD-066.

**Tests:** `tests/unit/test_bawue_sitzungen_scraper.py::TestIssue116MovedSession`,
`TestIssue117NamedCommittees` dort und in `tests/unit/test_ics_parser.py`
