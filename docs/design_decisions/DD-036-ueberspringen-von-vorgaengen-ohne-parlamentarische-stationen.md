[← Index](../design_decisions.md)

# DD-036: Überspringen von Vorgängen ohne parlamentarische Stationen

**Datum:** 29.03.2026 · *umnummeriert von DD-012 (Nummernkollision mit „Synthetische
`parl-initiativ` nach `preparl-regbsl`") am 11.07.2026 — inhaltlich unverändert.*

**Kontext:** PARLIS listet unter dem Vorgangstyp „Gesetzgebung" auch Einträge auf,
die kein vollständiges parlamentarisches Gesetzgebungsverfahren durchlaufen haben.
Diese Einträge besitzen ausschließlich nachparlamentarische Stationen (`postparl-*`)
und keine parlamentarischen Stationen (`parl-*`). Es handelt sich um:

- **Bekanntmachungen** — z. B. Inkrafttreten von Staatsverträgen (V-212734, V-213657)
- **Neufassungen** — z. B. Geschäftsordnung der Landesregierung (V-212729, V-221160)
- **Berichtigungen** — Korrekturen veröffentlichter Gesetze im Gesetzblatt (V-222654)

Das Backend validiert Vorgänge gegen den Track `gg-land-parl`, der eine
parlamentarische Kette (`parl-initiativ → parl-vollvlsgn → …`) voraussetzt.
Einträge mit nur `postparl-gsblt` werden mit HTTP 400 (Track validation Failed)
abgelehnt.

**Entscheidung:** Vorgänge, bei denen alle Stationen nachparlamentarisch sind
(`postparl-gsblt`, `postparl-vesja`, `postparl-vesne`, `postparl-kraft`), werden
in `item_extractor()` übersprungen und mit einer Info-Lognachricht dokumentiert.
Dies ist korrekt, da diese Einträge keine eigenständigen Gesetzgebungsverfahren
des Landtags darstellen.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methode `item_extractor()`,
Konstantenmenge `_POSTPARL_TYPEN`.
