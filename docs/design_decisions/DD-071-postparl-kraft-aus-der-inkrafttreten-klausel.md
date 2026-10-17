[← Index](../design_decisions.md)

# DD-071: `postparl-kraft` aus der Inkrafttreten-Klausel im Gesetzblatt (GitHub Issue #98)

**Datum:** 17.10.2026

**Kontext:** Ein verkündetes Gesetz endete bei `postparl-gsblt`; PARLIS nennt das Inkrafttreten
praktisch nie. Es steht im Schlussartikel des Gesetzes, dessen PDF die Gesetzblatt-Detailseite
verlinkt (DD-047). Upstream ist offen, wie gestuftes Inkrafttreten modelliert wird
([parlamentszusammenfasser#66](https://codeberg.org/PaZuFa/parlamentszusammenfasser/issues/66));
der BU-Scraper nimmt bis dahin das späteste Datum.

**Entscheidung:**

1. **Klausel:** der letzte Artikel/§ vor der ersten Unterschrift („Stuttgart, den“). So zählen
   zitierte Fremdtexte und ein nachgestellter Staatsvertrag nie. Erkannt werden „am Tag (nach)
   seiner Verkündung“, „am/mit Wirkung vom <Datum>“, „am ersten Tag des (übernächsten | N-ten)
   auf die Verkündung folgenden Monats“ und „N Monate nach seiner Verkündung“.
2. **Kein Raten:** Hat ein „in Kraft“ kein auflösbares Datum (Bedingung, Datum eines
   Staatsvertrags), gibt es keine Station, nur eine WARNING.
3. **Datum:** das späteste der Klausel (BU-Konvention), also wann das ganze Gesetz gilt.
4. **Station:** nach der G-Station, Gremium `gesetzesblatt`, ohne Dokumente. Die `api_id` ist
   datumsfrei (wie DD-046): Der Track erlaubt nur ein K, ein korrigiertes Datum muss die Zeile
   ändern statt eine zweite anzulegen.
5. **Rückwirkend** (Datum vor der G-Station): keine Station, nur INFO; sonst HTTP 400 beim Track.
6. **Zukünftig:** zurückgehalten und nicht gecacht wie DD-068.
7. **Cache (DD-052):** Vorgänge mit Gesetzblatt-Fundstelle bekommen einmalig einen neuen
   Fingerprint und werden neu gebaut.

Stichprobe Gesetzblatt 2024–2026: 80 von 82 Gesetzen aufgelöst, die zwei übrigen sind bedingt.
6 rückwirkend, 3 mit künftigem Datum.

**Nicht abgedeckt:**

- Ein Gesetz mit künftigem Datum wird bis dahin täglich neu gebaut (bis zu Jahren bei Staffelung).
- Zitate vor 2024 (kein elektronisches Gesetzblatt) und eine zweite Gesetzblatt-Fundstelle
  (Berichtigung) werden nicht gelesen.
- Ein nicht ladbares PDF wird erst beim nächsten Neubau des Vorgangs erneut versucht.

**Code:** `gesetzblatt_parser.parse_inkrafttreten`, `GesetzblattDateLookup.inkrafttreten`,
`BawueVorgaengeScraper._ensure_kraft_station`, `_vorgang_fingerprint`

**Tests:** `test_gesetzblatt_parser.py::TestParseInkrafttretenIssue98` (echte WP17-Gesetze),
`test_gesetzblatt_lookup.py::TestInkrafttretenIssue98`, `test_bawue_scraper.py::TestIssue98KraftStation`
