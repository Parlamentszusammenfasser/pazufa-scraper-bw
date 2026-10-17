[← Index](../design_decisions.md)

# DD-071: `postparl-kraft` aus der Inkrafttreten-Klausel (GitHub Issue #98)

**Datum:** 17.10.2026

**Kontext:** PARLIS nennt das Inkrafttreten fast nie. Es steht im Schlussartikel des Gesetzes im
Gesetzblatt-PDF (DD-047). Wie gestuftes Inkrafttreten modelliert wird, ist upstream offen
([parlamentszusammenfasser#66](https://codeberg.org/PaZuFa/parlamentszusammenfasser/issues/66)).

**Entscheidung:**

1. Gelesen wird der letzte Artikel/§ vor der Unterschrift („Stuttgart, den“), nicht zitierte
   Fremdtexte oder ein nachgestellter Staatsvertrag.
2. Das Datum ist das **späteste** der Klausel, wie beim BU-Scraper.
3. Hat ein „in Kraft“ im selben Satz kein Datum (z. B. eine Bedingung): keine Station, WARNING.
4. Rückwirkend (vor der G-Station): keine Station, sonst scheitert der Track.
5. In der Zukunft: zurückgehalten wie DD-068.
6. Die `api_id` ist datumsfrei (wie DD-046), weil der Track nur ein K erlaubt.
7. Gecachte Gesetze werden einmal neu gebaut (Fingerprint, DD-052).
8. Ist das Gesetzblatt nicht abrufbar (kein 404), wird der Vorgang im nächsten Lauf neu versucht.

Stichprobe 2024–2026: 80 von 82 Gesetzen gelesen, die zwei übrigen sind bedingt.

**Nicht abgedeckt:** Ein künftiges Datum lässt das Gesetz bis dahin täglich neu bauen. Zitate vor
2024 und eine Berichtigung werden nicht gelesen. Nennt PARLIS später selbst ein Inkrafttreten,
entstünde ein zweites K (bisher nie beobachtet).

**Code:** `parse_inkrafttreten`, `GesetzblattDateLookup.inkrafttreten`, `_ensure_kraft_station`,
`_vorgang_fingerprint`

**Tests:** `TestParseInkrafttretenIssue98` (echte WP17-Gesetze), `TestInkrafttretenIssue98`,
`TestIssue98KraftStation`
