[← Index](../design_decisions.md)

# Identität und Backend-Merge

Das Backend erkennt beim Re-Upload wieder: Vorgänge über `api_id` **oder** einen geteilten
`vg_ident`, Stationen über `api_id` **oder** einen geteilten Dokument-Hash, Dokumente über
`hash_`. Es fügt beim Merge nur hinzu und löscht nie; der Collector-Key darf nicht löschen.
Jede instabile oder geteilte Identität erzeugt daher Duplikate (HTTP 400 Track) oder
Kollisionen (HTTP 500 `rel_station_dokument_pkey`). Altlasten bereinigt nur ein Admin.

<a id="dd-028"></a>
## DD-028: Stabile `api_id` für Stationen

**Kontext:** Eine dokumentlose Station hat weder `api_id` noch Hash. Das Backend fügte sie
bei jedem Lauf neu ein; `II` scheiterte am Track (V-246637).

**Entscheidung:** Stationen bekommen eine deterministische `api_id`:
`uuid5(NAMESPACE_URL, "bawue-station-<vid>-<typ>-<iso-zp_start>")`, vergeben **nach** dem
Auflösen der Zeitstempel-Kollisionen. Für alle Stationen gilt das seit DD-034.

<a id="dd-034"></a>
## DD-034: Die Stations-`api_id` gilt für alle Stationen und hängt nicht an Dokumenten

**Kontext:** Haushalts-Einzelpläne zitieren dieselbe Drucksache (17/1000). Über den
geteilten Hash führte das Backend Stationen fremder Vorgänge zusammen: HTTP 500 bei 45
Vorgängen (Issue #47). Ein Schlüssel mit Dokumenten änderte sich, sobald ein PDF
nachgeliefert wurde: Duplikat, `II` (Issue #66).

**Entscheidung:**
1. **Jede** Station ohne eigene `api_id` bekommt den DD-028-Schlüssel, unabhängig von ihren
   Dokumenten.
2. Gleicher Typ und gleiches `zp_start` werden über die Listenposition unterschieden
   (`-tie2`, `-tie3`, …); die erste behält das DD-028-Format, damit gespeicherte Zeilen
   weiter passen.
3. Die synthetische Initiative kopiert die Dokumente des Entwurfs tief
   (`_deepcopy_preserving_unset`, damit das `UNSET`-Sentinel identisch bleibt).

<a id="dd-046"></a>
## DD-046: Datumsfreie `api_id` für synthetische Stationen

**Kontext:** Das Datum der synthetischen Initiative hängt davon ab, welche Fundstellen schon
existieren (DD-035). Mit der Ersten Beratung wanderte ihre `api_id`: `SSIL` (V-247045).

**Entscheidung:** Synthetische Stationen tragen eine datumsfreie `api_id`, gesetzt bei der
Erzeugung: `uuid5(NAMESPACE_URL, "bawue-synth-initiativ-<vid>")`, ebenso Ablehnung (DD-010)
und Inkrafttreten (DD-071). Je Vorgang gibt es höchstens eine. Reale Stationen behalten
DD-028, ihr Datum stammt aus der Fundstelle und ist stabil.

<a id="dd-041"></a>
## DD-041: Initiativdrucksache als `vg_ident`, außer bei Haushaltsgesetzgebung

**Kontext:** Das Backend hält zwei Vorgänge für einen, sobald **ein** `vg_ident`
übereinstimmt (`vorgang_merge_candidates`). `initdrucks` ist bei Haushalten n:1: Alle
Einzelpläne zitieren das Staatshaushaltsgesetz. Folge: stille Überschreibung von Titeln und
HTTP 500 ([pazufa-backend#150](https://codeberg.org/PaZuFa/pazufa-backend/issues/150)). Eine
korrekte `api_id` hilft nicht, der `vg_ident`-Zweig ist ein ODER.

**Entscheidung:** `initdrucks` wird gesendet, außer bei Haushaltsgesetzgebung — nur dort
teilen sich Vorgänge die Initiativdrucksache (WP 17: 17/1000, 17/3500, 17/8000). Notschalter
für alle: `[bawue] emit-initdrucks-ident = false`. `vorgnr` und stabile Stations-IDs genügen
für die Wiedererkennung.

**Neu bewerten, wenn:** die Einzelpläne eines Haushalts ein Vorgang werden (GitHub #60) oder
das Backend nur noch über 1:1-Identifier matcht.

<a id="dd-043"></a>
## DD-043: Der `#page=N`-Anker gehört zur Dokumentidentität

**Kontext:** PARLIS führt mehrere Dokumente unter einer Drucksache, je mit eigenem
Seitenanker (17/4495: zwei Änderungsanträge, `#page=1` und `#page=5`, V-170962). Die
Deduplizierung nur über `drucksnr` verwarf den angenommenen Antrag; ein Hash über die ganze
Datei hätte beide im Backend verschmolzen.

**Entscheidung:** Dedup über `(drucksnr, Anker)`. `hash_` faltet den Anker ein:
`sha256("<datei-hash>#page=<n>")`. Seit DD-049 auch für Protokolle.

<a id="dd-048"></a>
## DD-048: Platzhalter-Hash statt geteiltem `TODO`

**Kontext:** Unlesbare Dokumente — und ohne LLM alle — trugen `hash_ = "TODO"`. Für das
Backend waren sie damit ein einziges Dokument; seit v0.3.0 ist `TODO` auch ungültig
(64 Hex-Zeichen).

**Entscheidung:** Der Platzhalter wird bei der Konstruktion gesetzt und von einem echten
Inhalts-Hash ersetzt, sobald die Extraktion gelingt:
- PARLIS: `sha256(vorgangs_id | Fundstelle-Rohtext | link)` — ein Dokument je Fundstelle,
  auch wenn ein noch unveröffentlichtes Protokoll ohne Anker in mehreren Vorgängen steht
  (Issue #70).
- Beteiligungsportal: `sha256(link)`, seine PDFs gehören zu genau einem Prozess.

`volltext` bleibt `TODO`: Pflichtfeld, keine Identität. Ohne `drucksnr` gilt nach einem
Download-Fehler DD-067.

**Konsequenz:** Ändert sich ein Hash, bleibt der alte Row an der Station hängen
(`DELETE /api/v2/dokument/{api_id}`, manuell).

<a id="dd-049"></a>
## DD-049: Ein Dokument-Row je Fundstelle, auch beim Plenarprotokoll

**Kontext:** DD-043 nahm Protokolle aus: ein PDF, ein Row für alle Vorgänge der Sitzung.
Aber `titel`, `link` und `autoren` sind je Vorgang verschieden. Der geteilte Row trug die
Werte des zuletzt hochgeladenen Vorgangs: falsche Lesung, Link in eine fremde Debatte
(104 von 998 Dokumenten, Issue #25).

**Entscheidung:** Row-Identität und Cache-Schlüssel sind getrennt.
- `hash_` faltet den Anker immer ein. Gleiche Seite bleibt ein Row.
- Der LLM-Cache-Schlüssel für Protokolle bleibt der Datei-Hash: eine Zusammenfassung je
  Sitzung (DD-037), ein LLM-Call.

**Konsequenz:** `volltext` eines Rows ist sein Seitenfenster, `zusammenfassung` die der
Sitzung. „Beschluss des Landtags in … Beratung"-Drucksachen ohne Anker behalten den
Datei-Hash und teilen einen Row; ihr Titel ist gleich. Bekannte Lücke: Ein lesbares
Protokoll, das noch ohne Anker in mehreren Vorgängen steht, teilt ebenfalls einen Row
(letzter `titel` gewinnt); kommt der Anker nach, entsteht je Station ein weiterer Row.
