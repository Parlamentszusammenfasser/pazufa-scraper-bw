[← Index](../design_decisions.md)

# Gremium und Autoren

Welcher Name in `Station.gremium` und `Autor.organisation` steht. Das Backend vereinheitlicht
nichts: jeder abweichende Name ist eine neue Zeile. Es meldet nur Beinahe-Treffer
(pg_trgm-Ähnlichkeit > 0.66).

<a id="dd-021"></a>
## DD-021: Reservierte Gremium-Namen

**Kontext:** Spec (`plenum`, `regierung`, `volk`), DoD-Wiki (zusätzlich `gesetzesblatt`) und
der BY-Scraper nennen reservierte Namen. Die Spec validiert das Feld nicht; maßgeblich sind
Wiki und BY-Praxis. Vorher stand im Klartext „Landtag".

**Entscheidung:** `Gremium.name` ist immer ein Ausschuss oder ein reservierter Name
(`ReservedGremium`). Reihenfolge in `_determine_gremium`:

| Station | Gremium |
|---|---|
| Fundstelle nennt einen Ausschuss | Ausschuss-Name |
| `postparl-gsblt`, synthetische `postparl-kraft` (DD-071) | `gesetzesblatt` |
| jedes `preparl-*` (DD-045), Beteiligungsportal | `regierung` |
| alle übrigen, auch synthetische; ICS-Plenarsitzung | `plenum` |

**Konsequenz:** Reservierte Namen gelten nur für `Gremium.name`, nie für Autoren. `volk` ist
definiert, aber ungenutzt (keine Volksentscheid-Stationen).

<a id="dd-045"></a>
## DD-045: Jedes `preparl-*` routet auf `regierung`

**Kontext:** `preparl-regbsl` fiel in den `plenum`-Default, obwohl der Kabinettsbeschluss
keine Landtagshandlung ist (Issue #10).

**Entscheidung:** Die Regel prüft das Präfix `preparl-`, nicht einen einzelnen Wert. Damit
erben künftig gemappte Werte wie `preparl-eckpup` sie automatisch. Ein genannter Ausschuss hat
weiter Vorrang.

<a id="dd-022"></a>
## DD-022: Kanonische Namen für `Autor.organisation`

**Kontext:** Die DoD verlangt (SHOULD) kanonische Namen. In BY erscheint dieselbe Fraktion
unter drei Strings.

**Entscheidung:** `canonicalize_organisation()` bildet eine geschlossene Menge ab
(`CanonicalOrganisation`: 5 Fraktionen, `Landesregierung`, `Landtag`), Schreibweise wie auf
der Landtag-Website (`Fraktion GRÜNE` ohne „der"). Varianten löst eine Alias-Tabelle auf
(ohne Groß-/Kleinschreibung und Sonderzeichen). Alles andere passiert unverändert.

**Konsequenz:** Ministerien und Personen werden nicht normalisiert — Ministerien werden oft
umbenannt, Personennamen sind zu heterogen. Neue Varianten nach einem vollen Lauf aus dem
JSONL suchen und als Alias ergänzen (`OBSERVED_ORGANISATIONS` im Test pinnt sie).

<a id="dd-042"></a>
## DD-042: Autor eines Dokuments: wer es erstellt hat, nicht wer den Vorgang startete

**Kontext:** Ohne eigenen Autortext erbte jedes Dokument den Initiator des Vorgangs. So
stand eine Beschlussempfehlung als „Landesregierung" und ein von der Regierung
unterzeichnetes Gesetzblatt als „Fraktion der SPD" in der Datenbank (Issues #71, #26).

**Entscheidung:** `Dokument.autoren` in dieser Reihenfolge:
1. `autor_text` der Fundstelle,
2. `ausschuss` der Fundstelle (die Spec erlaubt Gremien als Autor),
3. ausstellendes Organ (`_issuing_organ`): Doktyp `redeprotokoll` → `Landtag`; sonst nach
   Station: Lesung, Annahme, Ablehnung → `Landtag`, `postparl-gsblt` → `Landesregierung`,
4. Initiator des Vorgangs.

**Konsequenz:** Eingereichte Dokumente (`parl-initiativ`, `preparl-*`, auch angehängte
Änderungsanträge) bleiben beim Einreicher. Bekanntmachungen gibt eigentlich ein einzelnes
Ressort heraus; das steht nur im PDF, „Landesregierung" ist gröber, nicht falsch.

<a id="dd-065"></a>
## DD-065: `gremium_federf` aus der Beschlussempfehlung

**Kontext:** In BW berichtet nur der federführende Ausschuss dem Plenum („Beschlussempfehlung
und Bericht"); mitberatende erscheinen nicht als Fundstelle (WP 17: nie mehr als ein
Ausschuss je Vorgang). Issue #64 wollte das aus dem Plenarprotokoll lesen.

**Entscheidung:** `gremium_federf = True` an jeder `parl-ausschber`-Station aus einer
„Beschlussempfehlung …" mit genanntem Ausschuss. Sonst `UNSET`; `False` entsteht nie. Kein
Plenarprotokoll-Parsing.

**Konsequenz:** Ob das Backend das Feld beim Merge übernimmt, ist ungeprüft (vgl. DD-060).
