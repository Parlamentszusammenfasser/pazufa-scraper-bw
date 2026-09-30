[← Index](../design_decisions.md)

# DD-060: `Vorgang.schlagworte` aus den PARLIS-Deskriptoren, nicht-strikt kanonisiert (GitHub Issue #33)

**Datum:** 30.09.2026

**Kontext:** Bisher kamen Schlagworte nur vom LLM, und zwar je Dokument. PARLIS vergibt
aber je Vorgang eigene **Deskriptoren**, ein vom Landtag gepflegtes Schlagwortverzeichnis.
Spec 0.2.7 (corelib v0.3.0, DD-059) führt dafür `Vorgang.schlagworte` ein;
`Station.schlagworte` ist seitdem veraltet.

**Befund (PARLIS WP 17 + WP 18, 239 Vorgänge, 30.09.2026):**

- **Zwei Felder:**
  - `WMV33` ist ein `;`-getrennter String mit HTML-Kursivmarkup für das Hauptschlagwort.
    Bei langen Listen ist er gekürzt: V-244535 hat dort 76 statt 103 Begriffe, und in
    V-222724 ist ein Begriff abgeschnitten („Prüfungso …“).
  - `EWBV34` ist die strukturierte Liste, vollständig und ohne Markup. Genau ein Eintrag
    je Vorgang ist als Hauptschlagwort (`"C": "HS"`) markiert.
- **Umfang:** Jeder Vorgang hat Deskriptoren, 2 bis 103 (Mittel 6,9), insgesamt 986
  verschiedene. Leere Einträge oder Duplikate kommen nicht vor.
- **Kanonisierung** gegen das corelib-Tag-Vokabular (`SchlagwortResolver.canonicalise_tags`,
  fuzzy nach Token-Sort-Ratio, rund 175 Tags aus Sachgebieten und globalen Tags):
  - Nicht-strikt ändert sie 8 der 986 Begriffe, alle zutreffend: Singular → Plural
    („Schule“ → „Schulen“, „Privatschule“, „Dienstleistung“ …) und
    „Öffentlicher Personennahverkehr“ → „Öffentlicher Personenverkehr“. Alle übrigen
    bleiben wörtlich.
  - Strikt blieben nur 32 Begriffe übrig, 954 fielen weg.

**Entscheidung:**

1. **Quelle ist `EWBV34`**, alle Begriffe in PARLIS-Reihenfolge. Das Hauptschlagwort wird
   nicht gesondert behandelt. Leere oder kaputte Einträge überspringt der Parser
   (`RawVorgang["Deskriptoren"]`).
2. **Kanonisierung nicht-strikt** (`enum_mapper.map_schlagworte`), wie vom Maintainer
   entschieden. Bekannte Begriffe bekommen die Schreibweise des Vokabulars, alle anderen
   bleiben wörtlich. Ein Begriff, der durch die Kanonisierung doppelt wird, fällt weg;
   die Reihenfolge bleibt.
3. **Leeres Ergebnis ⇒ `UNSET`**, wie bei `ressort`/`sachgebiete`.
4. **Die LLM-Schlagworte je Dokument bleiben unverändert.** Die Deskriptoren beschreiben
   den Vorgang, die LLM-Schlagworte das einzelne Dokument.

**Risiko:** Fuzzy-Matching kann einen Begriff auf ein falsches Tag ziehen. Bei den
Sachgebieten macht es aus „Öffentliche Schulen“ „Öffentliche Schulden“ (DD-058).
In den 986 echten Deskriptoren passiert das nicht. Ein Test über alle 986 Begriffe
(`tests/fixtures/parlis/deskriptoren_wp17_wp18.json`) pinnt genau die 8 erwarteten
Änderungen. Eine Vokabular-Änderung in corelib, die weitere Begriffe umschreibt, fällt
dort auf. Neue PARLIS-Begriffe deckt der Test nicht ab.

**Migrationshinweis:** Deskriptoren gehen nicht in den `vg2:`-Fingerprint ein (DD-052).
Bereits gecachte Vorgänge bekommen `schlagworte` erst beim nächsten Neubau. Ein Backfill
braucht das Löschen der `vg2:`-Einträge und ist eine bewusste Rollout-Entscheidung.

**Laufende Vorgänge:** Aus demselben Grund erreicht eine reine Deskriptor-Änderung in
PARLIS (Begriff ergänzt oder korrigiert, ohne neue Fundstelle und ohne neuen „Aktuellen
Stand“) das Backend nicht. `schlagworte` bleibt dann bis zum nächsten Neubau veraltet.
Das wird in Kauf genommen, wie bei Titel und Sachgebiet (DD-052, Punkt 3): Neue
Deskriptoren kommen vermutlich meist mit einer neuen Drucksache oder Sitzung, die ohnehin
einen Neubau auslöst, und ein fehlender Begriff ist unkritisch. Wie oft es vorkommt, ist
nicht gemessen (nur ein Dump vom 30.09.2026). Die Deskriptoren mitzuhashen würde jeden
Fingerprint ändern und alle Vorgänge einmal neu bauen, samt erneutem PDF-Download.

**Code:** `parlis_parser._deskriptoren`, `enum_mapper.map_schlagworte`,
`BawueVorgaengeScraper._build_vorgang`, `types.RawVorgang`

**Tests:**
- `tests/unit/test_parlis_parser.py::TestJsonCommentToRawVorgang` (`…deskriptoren…`)
- `tests/unit/test_enum_mapper.py::TestSchlagworteMapping`
- `tests/unit/test_bawue_scraper.py::TestIssue33SchlagworteFromParlis`
