[← Index](../design_decisions.md)

# DD-019: Positionsheuristik — „Antrag" nach Ausschussbericht als Änderungsantrag

**Datum:** 13.04.2026

**Kontext:** PARLIS kennzeichnet Änderungsanträge in der Fundstelle-Textzeile als
schlichtes „Antrag" — nicht als „Änderungsantrag". Der Enum-Mapper bildet „Antrag"
auf `parl-initiativ` ab, was korrekt ist, wenn der Antrag am Anfang eines Vorgangs
steht (z. B. ein parlamentarischer Antrag einer Fraktion). Erscheint „Antrag" jedoch
**nach** einem Ausschussbericht (`parl-ausschber`), handelt es sich ausnahmslos um
einen Änderungsantrag zur Beschlussempfehlung — kein neuer Initiativantrag. Der
Dokumenteninhalt bestätigt dies (z. B. „Änderungsantrag der Fraktion GRÜNE und der
Fraktion der CDU zu der Beschlussempfehlung des Ausschusses …"), dieser Inhalt steht
jedoch beim Fundstelle-Parsing nicht zur Verfügung (Issue 1B, betroffen: V-214623).

**Entscheidung:** Positionsheuristik in `_collect_stationen()`: Wenn eine Station den
Typ `parl-initiativ` hat, der Fundstelle-Text „Antrag" oder „Anträge" lautet (die
mehrdeutigen Labels, nicht „Gesetzentwurf", „Kleine Anfrage" etc.) **und** bereits
ein `parl-ausschber` in der Stationsliste aufgetreten ist, wird die Station als
Änderungsantrag umklassifiziert. Ihre Dokumente werden an die nächste
`parl-vollvlsgn`-Station angehängt — identisch zum bestehenden
Änderungsantrag-Handling (s. DD-001).

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methode `_collect_stationen()`,
Klassen-Konstante `_AMBIGUOUS_ANTRAG_TYPEN`, Flag `seen_ausschber`.
