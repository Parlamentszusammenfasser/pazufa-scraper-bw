[← Index](../design_decisions.md)

# DD-057: Neue Enum-Werte aus Spec 0.2.5 bleiben bewusst ungemappt (GitHub Issue #43)

**Datum:** 28.09.2026 | **Aktualisiert:** 30.09.2026 (corelib v0.3.0, DD-059)

**Kontext:** Spec 0.2.5 (corelib v0.2.1, DD-051) brachte vier neue `Stationstyp`-Werte
(`preparl-formvs`, `parl-antragsst`, `parl-vermittas`, `parl-verfgstop`) und den `Doktyp`
`eckpunktepapier`. Die Spec beschreibt die Werte nicht. Ihre Bedeutung ist aus den
Kommentaren der lokalen `deploy/tracks.toml` (v0.0.1) **abgeleitet**: dort sind `F` die
„Formulierungshilfe für Plenarinitiativen“ und `H` der Vermittlungsausschuss, und es gibt
ein TODO für einen Stationstyp „Antrag stellen“. Alle drei stehen nur beim Bundestag
(`BT`). Im maßgeblichen Upstream-`tracks.toml` (v0.0.7, DD-040) ist `V` =
`parl-vermittas`; einen Buchstaben für `formvs` oder `antragsst` gibt es dort nicht.

**Prüfung gegen echte Daten (28.09.2026):**

- **PARLIS WP 17 (235 Vorgänge) und WP 18 (3 Vorgänge):** Kein Eintrag nennt ein
  Eckpunktepapier, eine Formulierungshilfe oder einen Vermittlungsausschuss.
  „Verfassungsgerichtshof“ kommt nur in Vorgangstiteln vor (Änderung des
  VerfGHG, Haushalts-Einzelplan 16), nie als Fundstelle. PARLIS hat zwar einen eigenen
  Vorgangstyp „Schreiben des Verfassungsgerichtshofs“, der ist aber `sonstig` und wird
  nicht gescrapt.
- **Beteiligungsportal:** Das Portal listet nur die laufenden Anhörungen, 6 Verfahren
  mit 8 PDFs. Sie heißen „Verordnungsentwurf“, „Gesetz …“ oder „Anlage“; ein
  Eckpunktepapier ist nicht dabei. Das ist eine **Momentaufnahme**: früher gescrapte,
  inzwischen abgeschlossene Anhörungen wurden nicht geprüft.

**Entscheidung:** Keiner der fünf Werte wird gemappt.

| Wert | Grund |
|---|---|
| `preparl-formvs` | Formulierungshilfe der Regierung für Koalitionsfraktionen. Die gibt es auch im Land, PARLIS zeigt sie aber als gewöhnlichen Fraktions-Gesetzentwurf, sie ist also nicht erkennbar. Im BT-Track steht `F` **nach** dem Kabinettsbeschluss (`SF?I`), die Anhörung im Beteiligungsportal liegt davor. Das Portal ist also nicht `formvs` („formelles Vorverfahren“ laut Issue), sondern bleibt `preparl-regent` |
| `parl-antragsst` | Stationstyp für antragsbasierte Vorgänge wie den Bundeswehreinsatz. PARLIS-Vorgangstypen „Antrag …“ sind `sonstig` und nicht in `DEFAULT_ENABLED_VORGANGSTYPEN` (Gesetzgebung, Haushalt, Volksantrag). In diesen Vorgängen bleibt ein führendes „Antrag“ `parl-initiativ`; Änderungs-/Entschließungsanträge und ein „Antrag“ nach dem Ausschussbericht werden Dokumente (DD-001/DD-019) |
| `parl-vermittas` | Vermittlungsausschuss zwischen Bundestag und Bundesrat; der Landtag hat keine zweite Kammer |
| `parl-verfgstop` | Stopp durch ein Verfassungsgericht. PARLIS führt das nicht als Station. Ab Spec 0.2.7 (corelib 0.2.2) heißt der Wert `postparl-vgstp` |
| `eckpunktepapier` (Doktyp) | In den geprüften Daten kommt keines vor, die Portal-PDFs sind Entwürfe (`preparl-entwurf`). Ein Doktyp birgt kein Track-Risiko; taucht eines auf, reicht ein Titel-Mapping im Beteiligungs-Scraper |

Für die Stationstypen gilt zudem: Der BW-Track `gg-land-parl` (DD-040) hat für keinen
der vier einen Buchstaben. Eine solche Station im Vorgang führt zu HTTP 400.

**Absicherung:** Der Kanarienvogel-Test für `Stationstyp` prüft jetzt auf **Gleichheit**
statt Teilmenge, wie der Test für `Doktyp` schon (er schlug in DD-051 an). Neben den erzeugten
Werten stehen die bewusst nicht erzeugten in `UNPRODUCED_STATIONSTYPEN`. Ein neuer
Spec-Wert lässt den Test also scheitern, bis er gemappt oder dort eingetragen ist.
Beim Upgrade auf corelib 0.2.2 (Spec 0.2.7) meldet der Test die Umbenennung
`parl-verfgstop` → `postparl-vgstp`; mit DD-059 ist das geschehen, die Liste führt jetzt
`postparl-vgstp`. Ein zweiter Test stellt sicher, dass
`STATIONSTYP_MAP` keinen dieser Werte und `DOKUMENTENTYP_MAP` kein `eckpunktepapier`
liefert. Er prüft nur die Mapping-Tabellen, nicht die im Code direkt gesetzten
Stationen (synthetische Stationen, Beteiligungs-Scraper). `preparl-regbsl` fehlte in der alten Liste, obwohl BW den Wert erzeugt
(DD-003), und ist jetzt ergänzt.

**Neu bewerten, wenn:** PARLIS oder das Beteiligungsportal Eckpunkte oder
Formulierungshilfen ausweist, BW Antrags-Vorgangstypen oder „Schreiben des
Verfassungsgerichtshofs“ scrapt oder der BW-Track Buchstaben für diese Typen bekommt.
Einen automatischen Alarm dafür gibt es nicht.

**Code:** `enum_mapper.py` (Stationstyp-Referenzkommentar)

**Tests:** `tests/unit/test_enum_mapper.py::TestEnumValuesExistInFramework`
(`test_all_stationstyp_values_valid`, `test_unmapped_spec_values_not_in_mappings`,
`test_all_doktyp_values_valid`)
