[← Index](../design_decisions.md)

# DD-058: `Vorgang.sachgebiete` aus dem PARLIS-Feld „Sachgebiet“, nicht aus den Deskriptoren (GitHub Issue #40)

**Datum:** 28.09.2026

**Kontext:** Spec 0.2.5 (DD-051) führt `Vorgang.sachgebiete` ein, eine Liste von
Sachgebiet-Nummern nach der Parlamentsspiegel-Systematik (`Sachgebiet`-IntEnum, 1000–9999).
Übertragen werden nur die Nummern. Issue #40 schlug die PARLIS-Deskriptoren (#33) als
Quelle vor. PARLIS liefert je Vorgang aber schon ein eigenes Feld **Sachgebiet** (WMV32).
`parlis_parser` las es bereits in `raw["Sachgebiet"]` ein, der Scraper nutzte es nicht.
Die Deskriptoren (WMV33/EWBV34) sind freie Schlagworte, etwa „Architektengesetz“ oder
„Ingenieurkammer“, und keine Sachgebiete.

**Befund (PARLIS WP 17 + WP 18, 238 Vorgänge, 28.09.2026):** Jeder Vorgang hat das Feld.
Es enthält 1–6 durch `;` getrennte Begriffe, insgesamt 92 verschiedene. 81 davon sind
wörtlich eine Sachgebiet-ID des corelib-Vokabulars (`sachgebiete.yaml`, gleiche 165 IDs
in corelib 0.2.1 und 0.2.2). Die übrigen 11 sind Parlamentsspiegel-Paare wie
„Jagd, Fischerei“ oder „Rundfunk, Fernsehen“. corelib führt sie nur unter dem ersten
Namen („Jagd“, „Rundfunk“). Die Beschreibungen in corelib nennen jeweils beide Teile, es
ist also dasselbe Sachgebiet. Der Fuzzy-Resolver von corelib findet diese Paare nicht.

**Entscheidung:**

1. **Quelle ist WMV32**, nicht die Deskriptoren. Das Feld stammt aus derselben
   Systematik, ein Mapping über freie Schlagworte ist nicht nötig. Die Deskriptoren
   bleiben Thema von #33 (`schlagworte`).
2. **Auflösung je Begriff** (`enum_mapper.map_sachgebiete`):
   - zuerst exakt;
   - dann exakt der Teil vor dem ersten Komma (Paare);
   - zuletzt `SchlagwortResolver.canonicalise_sachgebiet` (fuzzy, fängt Groß-/Kleinschreibung
     und Leerraum ab).

   Die exakten Prüfungen kommen zuerst, damit nur echte Fehltreffer eine Warnung loggen.
3. **Nicht auflösbare Begriffe werden verworfen**, nicht auf 9900 „Unbekannt“ gemappt.
   Doppelte Nummern fallen weg, die Reihenfolge von PARLIS bleibt erhalten.
4. **Leeres Ergebnis ⇒ Feld `UNSET`** und damit nicht im Payload, wie bei `ressort` (DD-055).

**Ergebnis:** Alle 238 Vorgänge erhalten mindestens ein Sachgebiet (147 × 1, 70 × 2,
15 × 3, 5 × 4, 1 × 6), ohne eine einzige Warnung.

**Migrationshinweis:** Der `vg2:`-Fingerprint hasht das Sachgebiet bewusst nicht (DD-052).
Bereits gecachte Vorgänge bekommen `sachgebiete` erst beim nächsten Neubau, also wenn sich
ihr PARLIS-Record ändert. Für einen sofortigen Backfill die `vg2:`-Einträge löschen
(DD-052, Punkt 4).

**Code:** `enum_mapper.map_sachgebiete`, `BawueVorgaengeScraper._build_vorgang`,
`types.Sachgebiet`

**Tests:**
- `tests/unit/test_enum_mapper.py::TestSachgebieteMapping`: Paare, Mehrfachwerte, Fuzzy,
  Verwerfen, Duplikate, leere Eingaben und alle 92 beobachteten PARLIS-Begriffe
  (`OBSERVED_PARLIS_SACHGEBIETE`)
- `tests/unit/test_bawue_scraper.py::TestIssue40SachgebieteFromParlis`
