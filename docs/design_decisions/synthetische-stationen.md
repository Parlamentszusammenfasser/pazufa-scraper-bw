[← Index](../design_decisions.md)

# Synthetische und übersprungene Stationen

Wo PARLIS einen Verfahrensschritt nicht als Fundstelle führt, der Track ihn aber verlangt
(DD-016), erzeugt der Scraper die Station selbst — nur bei eindeutigem Signal.

<a id="dd-010"></a>
## DD-010: Synthetische Ablehnung aus „Aktueller Stand"

**Kontext:** Annahmen haben eine Fundstelle, Ablehnungen oft nicht: Das Ergebnis steht nur im
Protokoll und im Feld „Aktueller Stand: Abgelehnt".

**Entscheidung:** Steht dort „Abgelehnt" und gibt es noch keine `parl-ablehnung`, wird eine
angehängt. Ihr Datum ist das der chronologisch letzten Station (DD-031). Ohne Stationen
keine Ablehnung.

<a id="dd-012"></a>
## DD-012: Synthetische Initiative nach dem Kabinettsbeschluss

**Kontext:** Ein Regierungsentwurf ist in PARLIS eine einzige Fundstelle, gemappt auf
`preparl-regbsl` (DD-003). Danach folgt direkt die Erste Beratung; der Track verlangt
dazwischen ein `I`.

**Entscheidung:** Folgt auf `preparl-regbsl` keine `parl-initiativ`, wird eine eingefügt. Sie
übernimmt die Dokumente des Entwurfs — die Einbringung *ist* die Initiative. Ihr Datum regelt
DD-035, ihre `api_id` DD-046.

<a id="dd-036"></a>
## DD-036: Vorgänge nur mit nachparlamentarischen Stationen werden übersprungen

**Kontext:** Unter „Gesetzgebung" führt PARLIS auch Bekanntmachungen, Neufassungen und
Berichtigungen (z. B. V-212734, V-222654). Sie haben nur `postparl-*`-Stationen, der Track
lehnt sie ab.

**Entscheidung:** Sind alle Stationen `postparl-*`, überspringt `item_extractor` den Vorgang
mit INFO-Log. Das sind keine Gesetzgebungsverfahren des Landtags.

<a id="dd-071"></a>
## DD-071: `postparl-kraft` aus der Inkrafttreten-Klausel

**Kontext:** PARLIS nennt das Inkrafttreten fast nie. Es steht im Schlussartikel des Gesetzes
im Gesetzblatt-PDF (DD-047). Gestuftes Inkrafttreten ist upstream ungeklärt
([parlamentszusammenfasser#66](https://codeberg.org/PaZuFa/parlamentszusammenfasser/issues/66)).
Ein geschätztes „virtuelles" Inkrafttreten wurde verworfen: es wäre in Einzelfällen falsch.

**Entscheidung:**
1. Gelesen wird der letzte Artikel/§ vor „Stuttgart, den", nicht zitierte Fremdtexte.
2. Das Datum ist das **späteste** der Klausel (wie beim BU-Scraper).
3. „In Kraft" ohne Datum im Satz (Bedingung): keine Station, WARNING.
4. Rückwirkend (vor `G`): keine Station, sonst scheitert der Track.
5. In der Zukunft: zurückgehalten wie DD-068.
6. Die `api_id` ist datumsfrei (wie DD-046), der Track erlaubt nur ein `K`.
7. Ist das Gesetzblatt nicht abrufbar (kein 404), folgt ein neuer Versuch im nächsten Lauf.

**Konsequenz:** Stichprobe 2024–2026: 80 von 82 Gesetzen gelesen, die zwei übrigen sind
bedingt. Nicht gelesen: Zitate vor 2024 und Berichtigungen. Nennt PARLIS später selbst ein
Inkrafttreten, entstünde ein zweites `K` (bisher nie beobachtet).
