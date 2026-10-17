[← Index](../design_decisions.md)

# Reihenfolge, Zusammenführung und Track

Das Backend sortiert Stationen nach `zp_start` und prüft die Buchstabenfolge gegen den Track.
Jede Regel hier hält einen Vorgang in einer gültigen Folge, ohne den Track aufzuweichen.
Buchstaben nach DD-040: `L` = `parl-vollvlsgn`, `A` = `parl-ausschber`, `N` = Ablehnung.

<a id="dd-040"></a>
## DD-040: Maßgeblicher Track und Prefix-Match

**Kontext:** Ältere DDs schrieben `V` für die Lesung und `Z` für die Ablehnung. Maßgeblich ist
die Trackdatei, die das Backend lädt (`tracks.toml` v0.0.7, Tag `v0.2.3+v0.0.7`).

**Entscheidung:** Es gilt

```
gg-land-parl = "((E*R+)?S)?I((LA*(Z|LJGA*KA*|LN|LA*(Z|LJGA*KA*|LN)))|Z)"
```

mit `R` regent, `E` eckpup, `S` regbsl, `I` initiativ, `L` vollvlsgn, `A` ausschber,
`J` akzeptanz, `Z` zurückgezogen, `N` ablehnung, `G` gsblt, `K` kraft. Die Validierung ist ein
**Prefix-Match** (`validate.rs`: `matched && consumed == len`): spätere Stationen dürfen
fehlen, ein laufendes Verfahren wie `SILAL` ist gültig.

**Konsequenz:** Die Tests bauen die Buchstabenfolge und prüfen sie mit
`regex.fullmatch(track, partial=True)` gegen eine wörtliche Kopie des Tracks
(`_BW_GG_LAND_PARL_TRACK`).

<a id="dd-016"></a>
## DD-016: BW nutzt den BY-Track unverändert

**Kontext:** Seit Backend v0.2.7 werden Tracks validiert. Ein eigener BW-Track mit optionalen
Lesungen hätte PARLIS-Lücken kaschiert.

**Entscheidung:** Kein eigener Track. Tracks bilden Parlamentsrecht ab; Lücken in PARLIS sind
Scraper-Bugs. GO-LT BW §42 verlangt zwei Lesungen, §43 Abs. 4 verbietet die Abstimmung in
erster Lesung, also `ILALN`, nie `ILN`. Daraus folgen DD-003, DD-012, DD-025, DD-031.

**Konsequenz:** Kollidierende `zp_start` verschiedener Typen werden vorwärts verschoben
(`_enforce_total_ordering`), damit die Sortierung eindeutig ist. Volksanträge
(`gg-land-volk`) werden gescrapt, haben aber keinen BW-Track und scheitern am Backend, bis
upstream einer definiert ist (z. B. V-230205, V-232608).

<a id="dd-004"></a>
## DD-004: Verschiedene Lesungen werden nie zusammengeführt

**Entscheidung:** Aufeinanderfolgende Stationen gleichen Typs und Gremiums werden
zusammengeführt, außer Lesungen verschiedener Runden: Erste, Zweite und Dritte Beratung
bleiben eigene `parl-vollvlsgn`-Stationen. Ein Ausschussbericht sucht rückwärts über eine
Plenarstation hinweg nach seinem Ausschuss, nie vorwärts. Gleiche Runden fasst DD-024
zusammen.

<a id="dd-024"></a>
## DD-024: Fundstellen derselben Lesungsrunde werden eine Station

**Kontext:** Bei Haushaltsgesetzen listet PARLIS je Einzelplan eine eigene „Zweite Beratung".
Das ergab 8–9 `L`, der Track erlaubt drei: HTTP 400.

**Entscheidung:** Aufeinanderfolgende `parl-vollvlsgn` mit gleichem Rundenlabel (ohne
Groß-/Kleinschreibung, nicht leer) und gleichem Gremium werden eine Station:
`zp_start` = frühestes, `zp_modifiziert` = spätestes Datum, Dokumente vereinigt. Ein leeres
Label verhindert das Zusammenführen.

**Konsequenz:** Eine mehrtägige Lesung ist eine Station, wie es das Modell mit
`zp_start`/`zp_modifiziert` vorsieht.

<a id="dd-026"></a>
## DD-026: „Beschluss des Landtags in Zweiter Beratung" gehört zur Zweiten Beratung

**Kontext:** PARLIS führt je Runde zwei Fundstellen: die Aussprache und die Drucksache mit dem
Abstimmungsvermerk. Mit DD-024 allein blieben vier `L` (V-222745).

**Entscheidung:** Zwei Labels gelten als dieselbe Runde, wenn beide dasselbe Ordinal (Erste …
Fünfte) und „Beratung" enthalten (`_reading_round`). Labels ohne „Beratung" (z. B.
„Überweisung") brauchen weiter exakte Gleichheit.

**Verworfen:** Mapping auf `parl-akzeptanz` (zwei `J`), mehr `L` im Track (GO kennt höchstens
drei Lesungen), Fundstelle verwerfen (Drucksache ginge verloren).

<a id="dd-025"></a>
## DD-025: Ausschussbericht vor der ersten Lesung wird nachdatiert

**Kontext:** PARLIS datiert die Beschlussempfehlung auf ihr Ausgabedatum. Bei
Haushalts-Einzelplänen liegt das vor der ersten Lesung: `SIAL`, HTTP 400.

**Entscheidung:** Liegt ein `parl-ausschber` nicht nach der ersten Lesung, wird sein
`zp_start` auf erste Lesung + 1 h gesetzt; `zp_modifiziert` nur, falls es sonst davor läge.
Ohne Lesung bleibt er unverändert (aber siehe DD-068).

**Verworfen:** `IA` im Track erlauben (Ausschuss ohne Lesung widerspricht GO §42), Station
verwerfen (Drucksache verwaist).

<a id="dd-035"></a>
## DD-035: Reihenfolge-Helfer rechnen mit `zp_start`, nicht mit der Listenposition

**Kontext:** PARLIS liefert Fundstellen nicht chronologisch. Helfer, die an der Listenposition
ankerten, übersahen Fälle (Issue #48, 17/1000, 17/3500).

**Entscheidung:** Der Anker für DD-025 ist die **früheste** Lesung nach `zp_start`. Die
synthetische Initiative (DD-012) bekommt das kleinste `zp_start` aller Stationen nach dem
Kabinettsbeschluss. Genau auf die erste Lesung datieren wurde verworfen: das kollidiert, und
das Vorwärts-Verschieben würde die Lesung hinter den Ausschussbericht schieben.

**Konsequenz:** Reihenfolge der Aufrufe in `_build_vorgang`: synthetische Stationen,
Ausschussbericht nachdatieren, Ablehnung anhängen (DD-031), Kollisionen auflösen.

<a id="dd-039"></a>
## DD-039: Derselbe Ausschuss nach mehr als 60 Tagen ist eine neue Beratung

**Kontext:** Zwei Beschlussempfehlungen des Finanzausschusses (30.06.2022, 09.02.2023,
V-214597) wurden zu einer Station; das spätere Datum ging verloren.

**Entscheidung:** Ein Ausschussbericht wird nur mit einem desselben Gremiums zusammengeführt,
wenn beide höchstens 60 Tage auseinander liegen (`_AUSSCHBER_MERGE_MAX_GAP`).

<a id="dd-068"></a>
## DD-068: Eine Fundstelle wird erst ab ihrem Datum zur Station

**Kontext:** PARLIS führt geplante Sitzungen vorab als Fundstelle (V-248237). Die Website zeigte
sie als geschehen. `zp_start` ist laut Spec die erste Handlung; geplante Stationen gibt es
nicht (Issue #75).

**Entscheidung:**
1. Fundstellen nach dem heutigen Kalendertag (Europe/Berlin) werden übersprungen; heute gilt
   als geschehen.
2. Der Vorgang wird nicht gecacht und im nächsten Lauf neu gebaut. Nur INFO-Log, kein
   „dropped"-Eintrag.
3. Was an der zurückgehaltenen Lesung hängt, wartet mit: übrige Anträge (DD-001) und ein
   Ausschussbericht ohne veröffentlichte Lesung (sonst `SIA`).
4. Die synthetische Initiative fällt auf das Datum des Entwurfs zurück; ihre `api_id` ist
   datumsfrei (DD-046).

**Konsequenz:** Ein Vorgang mit geplanter Sitzung wird bis dahin in jedem Lauf hochgeladen.
Früher hochgeladene künftige Stationen bleiben im Backend. Geplante Lesungen gehören als TOP
in die Sitzung (DD-070).
