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

**Verworfen:** Zeitstempel versetzen. `II` bleibt ungültig; das Problem ist Identität, nicht
Sortierung.

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
## DD-041: Initiativdrucksache als `vg_ident`

**Kontext:** Das Backend hält zwei Vorgänge für einen, sobald **ein** `vg_ident`
übereinstimmt (`vorgang_merge_candidates`). Teilen fremde Vorgänge einen `initdrucks`, folgen
stille Überschreibung von Titeln und HTTP 500
([pazufa-backend#150](https://codeberg.org/PaZuFa/pazufa-backend/issues/150)). Eine korrekte
`api_id` hilft nicht, der `vg_ident`-Zweig ist ein ODER. In WP 17 teilen sich nur die
Einzelpläne eines Haushalts eine Initiativdrucksache (17/1000, 17/3500, 17/8000).

**Entscheidung:** `initdrucks` wird für jeden Vorgang gesendet. Die Einzelpläne werden in
ihr Staatshaushaltsgesetz gefaltet (DD-077), danach ist `initdrucks` eindeutig. Notschalter
für alle: `[bawue] emit-initdrucks-ident = false`; `vorgnr` und stabile Stations-IDs genügen
dann für die Wiedererkennung.

**Neu bewerten, wenn:** sich andere Vorgänge eine Initiativdrucksache teilen oder das Backend
nur noch über 1:1-Identifier matcht.

<a id="dd-077"></a>
## DD-077: Ein Vorgang je Haushalt

**Kontext:** PARLIS führt jeden Einzelplan als eigenen Vorgang (WP 17: drei Haushalte mit je
17 Einzelplänen). Alle zitieren den Gesetzentwurf des Staatshaushaltsgesetzes als
Initiativdrucksache, für das Backend also derselbe Vorgang (DD-041). Seit Backend v0.3.4 sollen
Scraper sie zusammenführen (GitHub #60).

**Entscheidung:** `listing_page_extractor` faltet Haushaltsgesetzgebung-Vorgänge mit gleicher
Initiativdrucksache in einen.
- Identität (vorgnr, `api_id`, Titel, „Aktueller Stand") ist die des Vorgangs ohne
  „Einzelplan" im Titel: des Staatshaushaltsgesetzes.
- Ein Einzelplan trägt nur Fundstellen bei, deren Drucksache oder Plenarprotokoll das
  Staatshaushaltsgesetz nicht schon zitiert: vor allem Beschlussempfehlung und Anträge, auch
  spätere Berichte. Seine erneuten Zitate von Lesungen und Beschlüssen (mit Einzelplan-Seiten)
  landeten sonst hinter dem Gesetzblatt und brächen den Track.
- Gemischt wird nach Datum, jeder Record in seiner PARLIS-Reihenfolge: Anträge hängen an der
  folgenden oder laufenden Lesung (DD-001). Ein falsch datierter Antrag zieht die späteren
  Fundstellen seines Records mit nach hinten.
- Der `vg2:`-Fingerprint (DD-052) entsteht aus dem gefalteten Record, deckt also jeden
  Einzelplan ab.

**Konsequenz:** Die Beschlussempfehlungen aller Einzelpläne bilden eine `parl-ausschber`-Station
(DD-039). Debatten je Einzelplan erreicht man nur noch über die Protokoll-Fundstellen des
Staatshaushaltsgesetzes. Bereits hochgeladene Einzelplan-Vorgänge (WP 17: 51) löscht nur ein
Admin, und zwar **vor** einem Neulauf von WP 17: Beim Station-Merge verknüpft das Backend nur
neu eingefügte Dokumente, die der Einzelpläne blieben sonst ohne Verknüpfung. Die alte
`parl-ausschber` findet das Backend über die geteilte Beschlussempfehlung und aktualisiert sie.
Jeder Haushaltsgesetzgebung-Vorgang, auch Begleitgesetz und Nachtrag, hat den Typ
`gg-land-parl-haushalt` (BW-Track wie `gg-land-parl`, Tracks ab v0.1.2). Ein Marker im
`vg2:`-Fingerprint baut gespeicherte einmal neu, auch abgeschlossene. Voraussetzung: Das Backend
legt den Typ in die `vgtyp_class` von `gg-land-parl`
([pazufa-backend#227](https://codeberg.org/PaZuFa/pazufa-backend/issues/227)), sonst scheitern
erneute Uploads, der Typwechsel und die Übernahme eines Portal-Entwurfs (DD-079) mit HTTP 500.

<a id="dd-079"></a>
## DD-079: Referentenentwurf und Gesetzentwurf sind ein Vorgang

**Kontext:** Ein Entwurf vom Beteiligungsportal (`preparl-regent`, DD-007) und der spätere
Gesetzentwurf in PARLIS teilten weder `api_id` noch `vg_ident`; das Backend legte zwei Vorgänge an
(Issue #180). Keine Quelle nennt die andere, und Titel ändern sich auf dem Weg.

**Entscheidung:**
1. Nach jedem Upload legt der Beteiligungs-Scraper den Entwurf in `beteiligung-entwuerfe` ab
   (`api_id`, Wahlperiode, Portal-Link, Ministerium, PDFs mit Datum). Scheitert das, bleibt er
   ungecacht. Der Marker `entwuerfe` im Fingerprint füllt das Register einmal.
2. Kandidaten eines Gesetzentwurfs der Landesregierung (`gg-land-parl`, `-haushalt`): Entwürfe derselben
   Wahlperiode aus dem Jahr vor der Drucksache, nicht am selben Tag (`R` vor `S`). Sie gehören zum
   `vg2:`-Fingerprint (DD-052): Ein später registrierter Entwurf baut den Vorgang einmal neu.
   Verglichen werden nur die noch nicht übernommenen.
3. Der Text entscheidet: Mindestens 0,4 der Wort-5-Gramme des Entwurfs stehen in der Drucksache,
   beim Zweitbesten weniger als 0,2 (WP 17/18: Paare 0,51 bis 0,96, fremde höchstens 0,16). Ist ein
   PDF nicht lesbar, entscheidet der nächste Lauf; der Vorgang ist dann schon hochgeladen, also gilt
   der zweite Weg in 4 (selten: in 8 Wochen Prod kein Fehlschlag). Ist die Drucksache ohne Text (OCR
   fand keinen), hat der Vorgang keinen Entwurf, bis sich sein `vg2:`-Fingerprint ändert: Jeder
   Lauf läse denselben leeren Text. Ein vom Portal entferntes Entwurfs-PDF (404) oder eines ohne
   Text zählt als leer.
4. Kennt das Backend den Vorgang noch nicht (`GET vorgang`), übernimmt er die `api_id` des
   Entwurfs; das Backend führt beide zusammen und behält die `preparl-regent`-Station. Sonst
   behält er seine `api_id` (zwei Kandidaten: `AmbiguousMatch`) und bekommt die Station mit
   denselben Dokumenten; den Portal-Vorgang löscht ein Admin. Der Laufbericht (Mattermost) nennt
   ihn in jedem Lauf, bis `GET vorgang` ihn nicht mehr findet.
5. Die Entscheidung steht nur in `entwurf-uebernahmen`. Danach lädt der Beteiligungs-Scraper den
   Entwurf nicht mehr hoch, geprüft auch direkt vor dem Upload: PARLIS schreibt den Vorgang, sonst
   wechselten Titel und Kurztitel.

**Verworfen:** Titelvergleich (WP 17: ein falsches Paar), gemeinsamer `vg_ident` (die Website
zeigt `ids[0]`, Titel wechseln), Abgleich im Backend (träfe alle Länder).

**Konsequenz:** Ein verpasster Treffer lässt zwei Vorgänge wie bisher. Ein falscher ist bei diesen
Schwellen unwahrscheinlich, aber möglich; der Laufbericht nennt deshalb jede Übernahme mit Wert.
Rückgängig, ohne den Entwurf: Vorgang löschen, im Eintrag `vorgnr` auf `-` setzen (sperrt den
Entwurf) und `vg2:<vorgnr>` löschen. Geht Redis verloren, lädt der Beteiligungs-Scraper alle
Entwürfe einmal neu: Ein übernommener zeigt kurz seinen Titel, ein gelöschter Portal-Vorgang kommt
zurück und steht wieder im Laufbericht. Ministerium und Landesregierung sind nur bei Übernahme
beide `initiatoren`. PDFs von Kandidaten werden einmal je Lauf gelesen.

<a id="dd-043"></a>
## DD-043: Der `#page=N`-Anker gehört zur Dokumentidentität

**Kontext:** PARLIS führt mehrere Dokumente unter einer Drucksache, je mit eigenem
Seitenanker (17/4495: zwei Änderungsanträge, `#page=1` und `#page=5`, V-170962). Die
Deduplizierung nur über `drucksnr` verwarf den angenommenen Antrag; ein Hash über die ganze
Datei hätte beide im Backend verschmolzen.

**Entscheidung:** Dedup über `(drucksnr, Anker)`. `hash_` faltet den Anker ein:
`sha256("<datei-hash>#page=<n>")`. Seit DD-049 auch für Protokolle. Ohne Anker sendet
`hash_` die echten Digests der Datei, `[sha256+bytes, sha1+bytes]`: Den sha1 nutzt das
Backend für Querverweise (Issue #41). Ein Fenster bekommt keinen sha1 — das Backend
matcht auf jeden gemeinsamen Hash und führte alle Fenster eines PDFs zusammen.

**Konsequenz:** `subdoc_id` ersetzt den eingefalteten Anker erst, wenn das Backend (v0.3.5)
zweierlei ändert: Der Merge schreibt `subdoc_id` nicht und überschreibt den Fenster-Hash mit
dem Datei-Hash — der Row sähe aus wie der ankerlose der Datei. Und der Treffer über
`drucksnr`/`typ`/`zp_referenz` ignoriert `subdoc_id`, trennt also die Fenster einer
Drucksache nicht. Dazu kommt eine Bereinigung der Fenster-Rows ([Issue #41,
Kommentar](https://github.com/Parlamentszusammenfasser/pazufa-scraper-bw/issues/41#issuecomment-6586875474)). Der Register-Eintrag eines TOP (DD-070) und die Beteiligungsfrist (DD-076)
speichern nur den sha256 (`types.row_sha256`). Bestehende Vorgänge erhalten den sha1 erst,
wenn sich ihre Quelle ändert: der PARLIS-Datensatz oder die Seite im Beteiligungsportal (DD-052).

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
- Der LLM-Cache-Schlüssel für Protokolle ist der Datei-Hash, mit den Seiten des
  Tagesordnungspunkts, wenn PARLIS sie nennt (DD-037).

**Konsequenz:** `volltext` eines Rows ist sein Seitenfenster, `zusammenfassung` die seines
Tagesordnungspunkts (DD-037). „Beschluss des Landtags in … Beratung"-Drucksachen ohne Anker behalten den
Datei-Hash und teilen einen Row; ihr Titel ist gleich. Bekannte Lücke: Ein lesbares
Protokoll, das noch ohne Anker in mehreren Vorgängen steht, teilt ebenfalls einen Row
(letzter `titel` gewinnt); kommt der Anker nach, entsteht je Station ein weiterer Row.
