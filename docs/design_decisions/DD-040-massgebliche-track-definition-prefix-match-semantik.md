[← Index](../design_decisions.md)

# DD-040: Maßgebliche Track-Definition & Prefix-Match-Semantik (Korrektur zu DD-016/025/026/035)

**Datum:** 12.07.2026

**Kontext:** Frühere DDs (DD-016, DD-025, DD-026, DD-035) beschrieben den
BW-Track `gg-land-parl` mit dem Buchstaben **`V`** für `parl-vollvlsgn` und
nutzten `Z` lose für „Ablehnung". Ein Abgleich mit der **tatsächlich vom Backend
geladenen** Trackdatei zeigt, dass beide Angaben nicht der Konfiguration
entsprechen. Diese DD hält die maßgebliche Definition fest und korrigiert die
älteren Einträge (die als historischer Verlauf bestehen bleiben).

**Quelle (maßgeblich):** Das Backend lädt zur Laufzeit die in `variables.toml`
konfigurierte Trackdatei:

```
trackfile = "https://codeberg.org/PaZuFa/parlamentszusammenfasser/raw/tag/v0.2.3+v0.0.7/docs/specs/tracks.toml"
```

`tracks.toml` (version `0.0.7`) definiert die Stationsbuchstaben in `[stations]`
und den BW-Track in `[tracks.BW]`.

**Maßgebliches Stations→Buchstaben-Mapping** (Auszug der für BW relevanten):

| Buchstabe | Stationstyp          | | Buchstabe | Stationstyp      |
|-----------|----------------------|-|-----------|------------------|
| `R`       | `preparl-regent`     | | `J`       | `parl-akzeptanz` |
| `E`       | `preparl-eckpup`     | | `Z`       | `parl-zurueckgz` |
| `S`       | `preparl-regbsl`     | | `N`       | `parl-ablehnung` |
| `I`       | `parl-initiativ`     | | `G`       | `postparl-gsblt` |
| **`L`**   | **`parl-vollvlsgn`** | | `K`       | `postparl-kraft` |
| `A`       | `parl-ausschber`     | |           |                  |

→ **`parl-vollvlsgn` ist `L`, nicht `V`** (`V` = `parl-vermittas`, im
tracks.toml auskommentiert). **Ablehnung ist `N`**, `Z` ist „zurückgezogen".

**Maßgeblicher BW-Track** (`[tracks.BW]`, verbatim):

```
gg-land-parl = "((E*R+)?S)?I((LA*(Z|LJGA*KA*|LN|LA*(Z|LJGA*KA*|LN)))|Z)"
```

**Validierung ist ein PREFIX-Match, kein Full-Match.** Das Backend
(`pazufa-backend-lib/src/db/validate.rs`) bildet die nach `zp_start` sortierten
Stationen auf ihre Buchstaben ab und ruft `regex.match_regex(substr)` →
`(matched, consumed)`. Ein Vorgang ist gültig, wenn

```
matched == true  &&  consumed == len(substr)
```

d. h. die Buchstabenfolge muss vollständig als **Anfang (Prefix)** eines vom
Track akzeptierten Wortes konsumierbar sein. **Spätere Stationen dürfen fehlen** —
ein laufendes Verfahren (`S I L A L`, Haushalts-Einzelplan bis zur zweiten
Lesung) ist gültig, obwohl die abschließenden Stationen `J G K`
(`parl-akzeptanz`, Gesetzblatt, Inkrafttreten) noch nicht vorliegen. Deshalb
werden die fünf Issue-#48-Vorgänge trotz „unvollständigem" Track akzeptiert,
sobald die Reihenfolge stimmt.

**Konsequenz für die Tests:** `tests/unit/test_bawue_scraper.py` prüft die
Reihenfolge nicht mehr nur über Einzeldaten, sondern baut die Buchstabenfolge
(z. B. `SILAL`) und validiert sie gegen den echten BW-Track als Prefix-Match —
via `regex.fullmatch(track, partial=True)`, was die Backend-Semantik
(`matched && consumed == len`) exakt spiegelt. Buchstaben-Mapping und Regex sind
verbatim aus `tracks.toml` übernommen (mit Quell-URL/Tag im Code dokumentiert).

**Implementierung/Tests:** `_TRACKS_TOML_STATIONS`, `_BW_GG_LAND_PARL_TRACK`,
`_passes_bw_track_validation()` und
`TestIssue48OrderByZpStartNotListPosition::test_bw_track_prefix_validation_matches_backend`
(+ die fünf parametrisierten `test_affected_document_builds_valid_track`).

**Nicht geändert:** Die Ordering-Logik selbst (DD-035) ist korrekt; nur die
*Beschreibung* der Buchstaben/Semantik in den älteren DDs war ungenau. `V` wird
in DD-016/025/026/035 weiterhin als Lesbarkeits-Platzhalter verwendet — gemeint
ist stets `L` (`parl-vollvlsgn`).
