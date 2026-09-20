[← Index](../design_decisions.md)

# DD-055: `Vorgang.ressort` aus einer kuratierten Ministeriumstabelle (GitHub Issue #39)

**Datum:** 20.09.2026

**Kontext:** Spec 0.2.5 (DD-051) kennt `Vorgang.ressort` mit dem Enum `Ressort`. Die Spec
beschreibt das Feld als „Ressort, which the Vorgang is associated with. **Usually the name of a
ministry**" (`openapi.yaml`) — es benennt also das **zuständige Haus**, nicht das Thema. Für
Themen ist `sachgebiete` vorgesehen (Issue #40, Parlamentsspiegel-Kategorien), für Stichworte
`schlagworte`.

Baden-Württemberg liefert kein Ressort-Feld; bekannt ist immer nur ein Ministeriumsname — im
Beteiligungsportal das federführende Ministerium, in PARLIS die `Initiative` oder der Autor einer
Fundstelle (z. B. das antwortende Ministerium einer Kleinen Anfrage). BW-Ministerien decken fast
immer mehrere Ressorts ab („Ministerium für Umwelt, Klima und Energiewirtschaft"), das Enum kennt
aber nur **einen** Wert. Jede Abbildung verliert also Information; die Frage ist nur, ob der
verbleibende Wert bewusst gewählt oder mechanisch abgeleitet wird.

**Entscheidung:**

1. `enum_mapper.RESSORT_BY_MINISTERIUM` ist eine **kuratierte Tabelle**: je Ministerium eine
   bewusst gewählte Zeile. Es wird **nichts** aus dem Wortlaut des Namens abgeleitet — eine
   frühere Variante nahm das zuerst genannte Stichwort („Umwelt, Klima und Energiewirtschaft"
   → `Umwelt`), was den Wert zum Zufallsprodukt der Wortstellung machte.
2. Abgedeckt sind die Kabinette, deren Vorgänge in PARLIS stehen (WP16–WP18), plus die in
   PARLIS-Autorenfeldern üblichen Kurzformen („Innenministerium", „Kultusministerium", …).
   Der Lookup ist schreibweisentolerant (klein, ohne Nicht-Wortzeichen — dieselbe Normalisierung
   wie `canonicalize_organisation`, DD-022).
3. `Staatsministerium` ist ausdrücklich `None`: Regierungszentrale ohne Fachressort.
4. Ein **unbekanntes** Ministerium — typischerweise nach einer Regierungsbildung umbenannt —
   liefert `None` und wird **einmal je Name geloggt** (`logger.warning`). Die Lücke wird damit
   sichtbar und kuratiert nachgetragen, statt durch eine Namensheuristik verdeckt zu werden.
5. Nur Namen mit „Ministerium" werden überhaupt geprüft; Fraktion, Landtag, Landesregierung,
   Ausschuss, Abgeordnete liefern still `None`.
6. PARLIS: `Initiative` entscheidet vor dem Fundstellen-Autor (spezifischer). Ohne Treffer bleibt
   `ressort` `UNSET` — das Feld wird dann gar nicht gesendet.

**Bewusste Einzelfälle (mehrere Ressorts, ein Wert):**

| Ministerium                                                | Gewählt                    | Begründung                                                              |
|------------------------------------------------------------|----------------------------|-------------------------------------------------------------------------|
| Ministerium für Umwelt, Klima und Energiewirtschaft          | `Umwelt`                   | Kernressort des Hauses; `Klimaschutz`/`Energie` sind Teilportfolios      |
| Ministerium für Ernährung, Ländlichen Raum und Verbraucherschutz (WP17) | `Landwirtschaft` | Das MLR ist das Landwirtschaftsministerium — bewusst **nicht** `Ernährung`, obwohl zuerst genannt |
| Ministerium für Ländlichen Raum, Landwirtschaft und Heimat (WP18) | `Landwirtschaft`      | Dieselbe Zuständigkeit, neuer Name                                      |
| Ministerium für Soziales, Gesundheit und Integration         | `Soziales`                 | Leitressort; `Gesundheit/Pflege/Prävention` ist Teilportfolio            |
| Ministerium für Kultus(, Jugend und Sport)                   | `Bildung`                  | Das Enum kennt kein „Kultus"; Bildung ist die Kernzuständigkeit          |
| Ministerium für Landesentwicklung und Wohnen                 | `Landes-/Stadtentwicklung` | Alternative wäre `Wohnen/Bau`; der Enum-Wert deckt den Namen direkt ab   |
| Ministerium des Inneren, für Digitalisierung und …           | `Inneres`                  | `Digitalisierung`/`Kommunales`/`Europa` sind angehängte Teilportfolios   |
| Ministerium für Wissenschaft, Forschung und Kunst            | `Wissenschaft`             | `Forschung`/`Kunst/Kultur` sind Teilportfolios                          |

**Konsequenz:** Alle Vorgänge eines Ministeriums tragen innerhalb einer Wahlperiode denselben
Wert — das ist die Aussage des Feldes („welches Haus"), nicht ein Themenindex. Thematische
Differenzierung kommt über `sachgebiete` (#40) und `schlagworte`. Bei Anfragen stammt das Ressort
vom **antwortenden** Ministerium, also der fachlich zuständigen Stelle.

**Reichweite in PARLIS:** Gesetzentwürfe der Landesregierung laufen unter der `Initiative`
„Landesregierung", und auch die Fundstellen nennen dort kein Ministerium — solche Vorgänge bleiben
ohne Ressort. Gefüllt wird es vor allem bei Anfragen (antwortendes Ministerium) und bei den
Vorgangstypen „… der Landesregierung/eines Ministeriums". Das Beteiligungsportal nennt das
federführende Ministerium dagegen immer. Bereits gesendete Vorgänge bekommen ihr Ressort erst,
wenn sich ihr PARLIS-Record ändert bzw. die `vg2:`-Einträge gelöscht werden (DD-052).

**Code:** `enum_mapper.RESSORT_BY_MINISTERIUM`, `enum_mapper.map_ressort`,
`types.org_lookup_key`, `bawue_vorgaenge_scraper._ressort` + `_build_vorgang`,
`bawue_beteiligung_scraper._build_vorgang`

**Tests:** `test_enum_mapper.py::TestRessortMapping`,
`test_bawue_scraper.py::TestIssue39Ressort`, `test_beteiligung_scraper.py::TestIssue39Ressort`
