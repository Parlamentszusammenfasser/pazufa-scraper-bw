[← Index](../design_decisions.md)

# DD-057: Neue Enum-Werte aus Spec 0.2.5 bleiben bewusst ungemappt (GitHub Issue #43)

**Datum:** 28.09.2026

**Kontext:** Spec 0.2.5 (corelib v0.2.1, DD-051) brachte vier neue `Stationstyp`-Werte
(`preparl-formvs`, `parl-antragsst`, `parl-vermittas`, `parl-verfgstop`) und den `Doktyp`
`eckpunktepapier`. Die Spec beschreibt die Werte nicht. Ihre Bedeutung ergibt sich aus
den Kommentaren in `deploy/tracks.toml`: dort sind `F` die „Formulierungshilfe für
Plenarinitiativen“ und `H` der Vermittlungsausschuss, und es gibt ein TODO für einen
Stationstyp „Antrag stellen“. Alle drei stehen nur in den Bundes-Tracks (`BT`/`BR`).

**Prüfung gegen echte Daten (28.09.2026):**

- **PARLIS WP 17 (235 Vorgänge) und WP 18 (3 Vorgänge):** Kein Eintrag nennt ein
  Eckpunktepapier, eine Formulierungshilfe oder einen Vermittlungsausschuss.
  „Verfassungsgerichtshof“ kommt nur in Vorgangstiteln vor (Änderung des
  VerfGHG, Haushalts-Einzelplan 16), nie als Fundstelle.
- **Beteiligungsportal:** Das Portal listet nur die laufenden Anhörungen, 6 Verfahren
  mit 8 PDFs. Sie heißen „Verordnungsentwurf“, „Gesetz …“ oder „Anlage“; ein
  Eckpunktepapier ist nicht dabei.

**Entscheidung:** Keiner der fünf Werte wird gemappt.

| Wert | Grund |
|---|---|
| `preparl-formvs` | Formulierungshilfe der Bundesregierung für Koalitionsfraktionen (BT-Track `SF?`). Weder PARLIS noch das Beteiligungsportal weisen sie aus. Das Beteiligungsportal bleibt `preparl-regent` (Anhörung des Regierungsentwurfs), nicht „formelles Vorverfahren“ |
| `parl-antragsst` | Stationstyp für antragsbasierte Vorgänge wie den Bundeswehreinsatz. In BW sind Anträge Dokumente der folgenden Lesung (DD-001/DD-019); die produktiv gescrapten Vorgangstypen sind Gesetzgebung, Haushalt und Volksantrag |
| `parl-vermittas` | Vermittlungsausschuss zwischen Bundestag und Bundesrat; der Landtag hat keine zweite Kammer |
| `parl-verfgstop` | Stopp durch ein Verfassungsgericht. PARLIS führt das nicht als Station. In corelib 0.2.7 heißt der Wert `postparl-vgstp` |
| `eckpunktepapier` (Doktyp) | Keine Quelle liefert Eckpunktepapiere. Die Portal-PDFs sind Entwürfe (`preparl-entwurf`) |

Neue Stationstypen würden zudem Buchstaben im `gg-land-parl`-Track brauchen, den BW
unverändert vom BY-Track übernimmt (DD-016/DD-040). Eine Station mitten im Track führt
zu HTTP 400.

**Absicherung:** Der Kanarienvogel-Test für `Stationstyp` prüft jetzt auf **Gleichheit**
statt Teilmenge, wie der Test für `Doktyp` schon seit DD-051. Neben den erzeugten
Werten stehen die bewusst nicht erzeugten in `UNPRODUCED_STATIONSTYPEN`. Ein neuer
Spec-Wert lässt den Test also scheitern, bis er gemappt oder dort eingetragen ist.
Beim Upgrade auf corelib 0.2.7 meldet der Test die Umbenennung
`parl-verfgstop` → `postparl-vgstp`. Ein zweiter Test stellt sicher, dass
`STATIONSTYP_MAP` keinen dieser Werte und `DOKUMENTENTYP_MAP` kein `eckpunktepapier`
liefert. `preparl-regbsl` fehlte in der alten Liste, obwohl BW den Wert erzeugt
(DD-003), und ist jetzt ergänzt.

**Neu bewerten, wenn:** PARLIS oder das Beteiligungsportal Eckpunkte oder
Formulierungshilfen ausweist, BW Antrags-Vorgangstypen scrapt oder das Backend einen
eigenen BW-Track mit diesen Buchstaben definiert.

**Code:** `enum_mapper.py` (Stationstyp-Referenzkommentar)

**Tests:** `tests/unit/test_enum_mapper.py::TestEnumValuesExistInFramework`
(`test_all_stationstyp_values_valid`, `test_unmapped_spec_values_not_in_mappings`,
`test_all_doktyp_values_valid`)
