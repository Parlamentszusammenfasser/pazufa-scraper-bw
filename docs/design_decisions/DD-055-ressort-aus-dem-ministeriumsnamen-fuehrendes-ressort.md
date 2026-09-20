[← Index](../design_decisions.md)

# DD-055: `Vorgang.ressort` aus dem Ministeriumsnamen, führendes Ressort gewinnt (GitHub Issue #39)

**Datum:** 20.09.2026

**Kontext:** Spec 0.2.5 (DD-051) kennt `Vorgang.ressort` mit dem Enum `Ressort`
(`Arbeit`, `Finanzen`, `Inneres`, …). Baden-Württemberg liefert kein Ressort-Feld; bekannt ist
immer nur ein Ministeriumsname — im Beteiligungsportal das federführende Ministerium, in PARLIS
die `Initiative` (Regierungsentwurf, Mitteilung eines Ministeriums) oder der Autor einer
Fundstelle (das antwortende Ministerium einer Kleinen Anfrage). BW-Ministerien decken fast immer
mehrere Ressorts ab („Ministerium für Umwelt, Klima und Energiewirtschaft"), und jedes Kabinett
schneidet sie neu zu (WP18 seit 05/2026, z. B. „Ministerium für Soziales, Arbeit und Gesundheit").

**Entscheidung:**

1. `enum_mapper.map_ressort(organisation)` bildet **Stichwörter im Namen** auf `Ressort` ab
   (`RESSORT_MAP`), nicht den vollständigen Ministeriumsnamen. Eine Tabelle voller Namen wäre
   nach jedem Kabinettswechsel veraltet; die Stichwörter überleben Umbenennungen.
2. **Das zuerst genannte Ressort gewinnt** (Leftmost-Match). BW-Ministerien führen ihr
   Schwerpunktressort im Namen zuerst: „Umwelt, Klima und Energiewirtschaft" → `Umwelt`,
   „Soziales, Gesundheit und Integration" → `Soziales`.
3. Nur Namen, die `Ministerium` enthalten, liefern überhaupt ein Ressort. Fraktion, Landtag,
   Landesregierung, Ausschuss oder Abgeordnete ergeben `None` — es wird nicht geraten.
4. Ein Stichwort muss am Wortanfang stehen (`(?<![a-zäöüß])`): „Energiewirtschaft" ist ein
   Energie-, kein Wirtschaftsministerium.
5. PARLIS: `Initiative` entscheidet vor dem Fundstellen-Autor (spezifischer); greift nichts,
   bleibt `ressort` `UNSET` statt `null` — das Feld wird dann gar nicht gesendet.
6. Unbekannte Ministeriumsnamen ohne bekanntes Stichwort bleiben bewusst ohne Ressort.

**Reichweite in PARLIS:** Gesetzentwürfe der Landesregierung laufen in PARLIS unter der
`Initiative` „Landesregierung", und auch die Fundstellen nennen dort kein Ministerium — solche
Vorgänge bleiben ohne Ressort. Gefüllt wird es in der Praxis vor allem bei Anfragen (antwortendes
Ministerium in der Antwort-Fundstelle) und bei den Vorgangstypen „… der Landesregierung/eines
Ministeriums". Das Beteiligungsportal nennt das federführende Ministerium dagegen immer.

**Konsequenz:** Bei Kleinen/Großen Anfragen stammt das Ressort vom **antwortenden** Ministerium —
das ist die fachlich zuständige Stelle und damit die gewünschte Aussage, auch wenn der Initiator
ein Abgeordneter ist. Bereits gesendete Vorgänge bekommen ihr Ressort erst, wenn sich ihr
PARLIS-Record ändert bzw. die `vg2:`-Einträge gelöscht werden (DD-052).

**Code:** `enum_mapper.RESSORT_MAP`, `enum_mapper.map_ressort`,
`bawue_vorgaenge_scraper._ressort` + `_build_vorgang`, `bawue_beteiligung_scraper._build_vorgang`,
`types.Ressort`

**Tests:** `test_enum_mapper.py::TestRessortMapping`,
`test_bawue_scraper.py::TestIssue39Ressort`, `test_beteiligung_scraper.py::TestIssue39Ressort`
