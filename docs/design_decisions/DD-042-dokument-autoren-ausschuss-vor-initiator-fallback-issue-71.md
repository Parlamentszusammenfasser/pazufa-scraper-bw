[← Index](../design_decisions.md)

# DD-042: `Dokument.autoren` — Ausschuss vor Initiator-Fallback (Issue #71)

**Datum:** 19.07.2026

**Kontext:** `Dokument.autoren` wurde aus genau zwei Quellen befüllt: dem
`autor_text` der Fundstelle, sonst — als Fallback — den `Initiative`-Autoren des
*Vorgangs*. Damit erbte jedes Dokument ohne eigenen Autortext den Initiator.
Auswertung des vollen WP17-Laufs (`locallogs`, 3258 Dokumente):

| Doktyp            | n    | bisheriger Autor            | korrekt? |
|-------------------|------|-----------------------------|----------|
| `preparl-entwurf` | 710  | Landesregierung             | ja       |
| `entwurf`/`antrag`| 270  | eigener `autor_text`        | ja       |
| `beschlussempf`   | 478  | Initiator (332× Landesreg.) | **nein** |
| `redeprotokoll`   | 1132 | Initiator (920× Landesreg.) | siehe unten |
| `mitteilung`      | 618  | Initiator (542× Landesreg.) | siehe unten |

Für **Beschlussempfehlungen** ist der tatsächliche Urheber bekannt: PARLIS nennt den
Ausschuss in der Fundstelle, und seit Issue #68 landet er zuverlässig in
`fund["ausschuss"]` (vorher scheiterte das an Genitiv-/Präfix-Namen). Die
OpenAPI-Spec erlaubt ein **Gremium** ausdrücklich als Autor:
„Kann z.B. eine Person, eine Organisation oder ein Gremium sein."

Hinweis: Vor Issue #68 trugen 68 Beschlussempfehlungen versehentlich „Ständiger
Ausschuss", weil Präfix-Ausschussnamen in den `autor_text` durchsickerten. Der
#68-Fix hat diese zufällige Teil-Abhilfe entfernt — sie wird hier bewusst ersetzt.

**Entscheidung:** `Dokument.autoren` wird in dieser Priorität bestimmt:

1. `autor_text` der Fundstelle (eigener Urheber, unverändert),
2. **`ausschuss` der Fundstelle** (das Gremium, das das Dokument erstellt hat) — neu,
3. `Initiative` des Vorgangs (Fallback).

Schritt 1 und 2 schließen sich seit Issue #68 gegenseitig aus: eine Fundstelle nennt
entweder einen Autor *oder* einen Ausschuss, entschieden von genau einer Prüfung in
`parse_fundstelle_text`.

**Bewusst nicht geändert** (Scope-Entscheidung, Issue #71 — *überholt durch die
Erweiterung unten, Issue #26*):

- **Redeprotokolle** behalten den Initiator. Ein Plenarprotokoll hat keinen
  einzelnen Urheber; weder Community-Wiki noch DoD noch OpenAPI-Spec definieren
  eine Konvention, und die Fundstelle nennt keine bessere Quelle. Redner aus dem
  PDF abzuleiten wäre nur mit aktiviertem LLM möglich (Default: aus) und damit
  nicht deterministisch.
- **`Gesetzesbeschluss` und Gesetzblatt-Dokumente** (beide `mitteilung`) behalten
  den Initiator. „Landtag" bzw. das herausgebende Organ wären neue frei erfundene
  Autor-Strings; DD-021 hält reservierte Namen (`plenum`) ausdrücklich für
  `Gremium.name` und **nicht** für `Autor.organisation` vor, und die kanonische
  Normalisierung der Autor-Strings ist als Roadmap #10 G2 offen.

Leitlinie: Der Initiator-Fallback bleibt überall dort, wo die Quelldaten nichts
Besseres hergeben. Geändert wird nur, was PARLIS tatsächlich benennt.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methode `_build_dokumente()`.
Ausschussnamen mit Komma („Ausschuss des Inneren, für Digitalisierung und
Kommunen") bleiben dank der Lookahead-Trennung in `_AUTOR_SPLIT_RE` **ein**
Autor und werden nicht aufgespalten.

**Tests:** `tests/unit/test_bawue_scraper.py::TestBuildStationAutoren` —
`test_ausschuss_is_document_autor_issue71` (reale Fundstelle Drucksache 17/2586 →
Autor ist der Ausschuss, nicht „Landesregierung"; Komma spaltet nicht).

## Erweiterung: Station-Schritt vor dem Initiator-Fallback (Issue #26, 05.08.2026)

**Kontext:** Die oben bewusst offen gelassene Lücke ist als Datenfehler
zurückgekommen. Weil PARLIS für die meisten Dokumente *keinen* Autor nennt, trug
faktisch **jedes** Dokument eines Vorgangs den Initiator — und ein Gesetzblatt, das
„Die Regierung des Landes Baden-Württemberg" unterzeichnet (GBl 2026 Nr. 12, S. 2),
stand als Dokument der **Fraktion der SPD** in der Datenbank, weil die SPD den
Entwurf eingebracht hatte (V-244180). Jedes Dokument widersprach damit seinem
eigenen `volltext`: „Der Landtag hat am 4. Februar 2026 das folgende Gesetz
beschlossen" (Drs. 17/10246, gespeichert als SPD).

Der Einwand von #71 — „Landtag" sei ein frei erfundener String — trägt nicht: die
Alternative ist nicht „kein Autor", sondern ein *nachweislich falscher*. `Autor`
ist laut OpenAPI-Spec ausdrücklich auch für Gremien vorgesehen, und die Kanonisierung
existiert seit DD-022; `Landtag` wird dort als sechster Wert aufgenommen, neben
`Landesregierung`. DD-021 bleibt unberührt: reservierte Namen wie `plenum` sind
weiterhin nur `Gremium.name`, nicht `Autor.organisation`.

**Entscheidung:** Vor den Initiator-Fallback tritt ein Schritt „ausstellendes Organ",
abgeleitet daraus, *wo* das Dokument entstanden ist. Die Priorität lautet damit:

1. `autor_text` der Fundstelle,
2. `ausschuss` der Fundstelle,
3. **ausstellendes Organ** (`_issuing_organ`) — neu,
4. `Initiative` des Vorgangs (Fallback).

`_issuing_organ` entscheidet in zwei Stufen:

- **Doktyp `redeprotokoll` → `Landtag`**, unabhängig von der Station. Ein
  Plenarprotokoll ist das Protokoll des Landtags über seine eigene Sitzung. Der
  Stationstyp allein genügt hier nicht: unbeschriftete Protokollzeilen mappen auf
  `sonstig` und werden erst in `_collect_stationen` — also *nach* `_build_dokumente` —
  auf `parl-vollvlsgn` umgeschrieben (im Sweep 13 Zeilen). Die Doktyp-Stufe greift
  vor dieser Umschreibung und ist damit unabhängig von ihr. Das löst zugleich die
  geteilte Protokoll-Zeile: eine über mehrere Vorgänge geteilte Zeile kann ohnehin
  keinen vorgangsspezifischen Autor tragen — „Landtag" ist für alle richtig.
- **sonst nach `Stationstyp`** (`_STATION_ORGAN`):
  `parl-vollvlsgn`, `parl-akzeptanz`, `parl-ablehnung` → `Landtag`;
  `postparl-gsblt` → `Landesregierung`.

Stationen, die dort **nicht** stehen, behalten den Initiator — und zwar mit Grund:
`parl-initiativ`, `parl-ggentwurf`, `preparl-*` und `parl-zurueckgz` tragen Dokumente,
die ein Akteur *einreicht*; sie gehören ihm, nicht dem Plenum. Änderungsanträge sind
davon der praktisch wichtigste Fall: PARLIS führt sie als eigene `parl-initiativ`-
Fundstelle und der Scraper hängt sie erst danach an die Lesung an (DD-019) — sie
werden also mit ihrem eigenen Stationstyp gebaut und bleiben beim Antragsteller.
`postparl-vesja`/`vesne` (Volksentscheid) fehlen bewusst: dort wäre das Volk das
handelnde Organ, wofür es keinen kanonischen Autor-String gibt und in WP17 kein
einziges Dokument.

**Grenze der Genauigkeit:** Für `postparl-gsblt` wird das ganze Gesetzblatt der
Landesregierung zugeschrieben. Für das Gesetz selbst ist das exakt die
Unterzeichnung („Die Regierung des Landes Baden-Württemberg: Kretschmann, Strobl,
…"). Feiner betrachtet gibt eine Bekanntmachung ein einzelnes Ressort heraus —
GBl. 2025 Nr. 1 etwa lautet „Bekanntmachung **des Staatsministeriums** über das
Inkrafttreten …", gezeichnet Dr. Florian Stegmann. Das steht nur im PDF; die
Fundstelle nennt es nicht (PARLIS kürzt den Genitiv weg), die Verfeinerung bliebe
also LLM-abhängig und damit nicht deterministisch. Das Ressort ist Teil der
Landesregierung — die Zuschreibung ist gröber, nicht falsch, und ersetzt einen
bisher falschen oder fehlenden Autor.

**Auswertung des WP17-Sweeps** (`Gesetzgebung`, 26.04.2021–05.08.2026, 173 Vorgänge,
1141 Fundstellen mit PDF-Link, Stand 05.08.2026): 507 Dokumente ändern ihren Autor,
634 bleiben unverändert. Kein Dokument mit `autor_text` oder `ausschuss` ist
betroffen (kein #71-Regress); von den geänderten gehen 483 auf `Landtag` (Protokolle,
Gesetzesbeschlüsse, Beschlüsse in X. Beratung) und 24 auf `Landesregierung`
(Gesetzblatt), darunter 5 zuvor **autorlose** Gesetzblatt-Dokumente. Die 116
Gesetzblatt-Dokumente von Regierungsentwürfen bleiben unverändert bei
`Landesregierung` — dort war der Initiator zufällig schon das ausstellende Organ.

**Implementierung:** `_issuing_organ()` + `_STATION_ORGAN` in
`bawue_vorgaenge_scraper.py`, eingehängt in `_build_dokumente()`;
`CanonicalOrganisation.LANDTAG` (+ Aliase) in `types.py`.

**Tests:** `tests/unit/test_issue26_dokument_autoren.py` — echte PARLIS-Fixtures für
V-244180, V-217223 und V-237492 (die drei Belege des Issues) durch die reale
Stationspipeline; dazu in `TestBuildStationAutoren`
`test_gesetzesbeschluss_is_landtag_issue26` und
`test_aenderungsantrag_attached_to_a_reading_keeps_the_initiator` (Abgrenzung:
angehängte Anträge bleiben beim Antragsteller).
