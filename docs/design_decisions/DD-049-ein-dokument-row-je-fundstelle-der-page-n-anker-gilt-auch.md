[← Index](../design_decisions.md)

# DD-049: Ein `Dokument`-Row je Fundstelle — der `#page=N`-Anker gilt auch für `REDEPROTOKOLL` (Issue #25)

**Datum:** 05.08.2026

**Kontext:** [DD-043](DD-043-geteilte-sammeldrucksache-dedup-hash-beruecksichtigen-den.md) nahm `REDEPROTOKOLL` von der anker-spezifischen
Hash-Bildung aus: ein Plenarprotokoll-PDF sollte **einen** Dokument-Row über alle in
der Sitzung behandelten Vorgänge hinweg belegen (DD-036/DD-037, Issue #49). Für
`hash_`, `volltext` und die LLM-Zusammenfassung ist das richtig — ein Row, ein
Cache-Eintrag, eine sitzungsbezogene Zusammenfassung, die kein Geschwister-Gesetz
überschreiben kann.

Die Ausnahme war aber **zu weit gefasst**. Drei Felder dieses Rows sind
Fundstellen-Metadaten *pro Vorgang*, nicht Inhalt *pro PDF*:

| Feld | Warum pro Vorgang |
|---|---|
| `titel` | Die Lesung unterscheidet sich je Gesetz derselben Sitzung („Erste Beratung" für das eine, „Zweite Beratung" für das andere) |
| `link` | Der `#page=N`-Anker zeigt auf den Abschnitt **dieses** Gesetzes |
| `autoren` | Wird pro Vorgang abgeleitet (Initiator-Fallback, [DD-042](DD-042-dokument-autoren-ausschuss-vor-initiator-fallback-issue-71.md)) |

Da das Backend `rel_station_dokument` über den Hash schlüsselt, hielt der geteilte Row
die Werte des zuletzt hochgeladenen Vorgangs. Jeder andere Vorgang der Sitzung zeigte
eine falsche Lesung, und sein Deep-Link führte in eine fremde Debatte. Betroffen waren
104 von 998 Dokumenten des BW-Listings; im letzten realen WP17-Lauf waren **89 von 133**
Protokoll-Datei-Hashes an mehr als einen `#page=`-Link gebunden.

**Nachweis an den Originalquellen** (Plenarprotokoll 17/137, 10.12.2025, von 10
Vorgängen zitiert):

| Vorgang | Gesetz | PARLIS-Fundstelle | Anker | PDF-Seite enthält |
|---|---|---|---|---|
| V-244180 | Juristenausbildungsgesetz | Erste Beratung | `#page=70` | „Juristenausbildungsgesetz" |
| V-243384 | schulgesetzliche Regelungen | Zweite Beratung | `#page=31` | „Schulgesetz" |

Gespeichert war für V-244180 `titel: "Zweite Beratung"` und `#page=31` — also die
Werte von V-243384.

**Entscheidung:** Row-Identität und Cache-Schlüssel werden getrennt. Beides leitete
sich bisher aus derselben Variable ab; das war die eigentliche Ursache der Konflation.

- **`hash_` (Row-Identität)** faltet den `#page=N`-Anker **immer** ein, sobald einer
  vorliegt — ohne doktyp-Zweig. Eine Fundstelle, ein Row. Zwei Fundstellen auf
  *dieselbe* Seite bleiben ein Row (echte Dublette, DD-043-Absicht bleibt erhalten).
- **Der LLM-Semantik-Cache-Schlüssel** verwendet für `REDEPROTOKOLL` weiterhin den
  reinen Datei-Hash (`file_hash`), für alle anderen doktypen den Anker-Hash. Damit
  bleibt [DD-037](DD-037-neutrale-vorgangs-unabhaengige-zusammenfassung-fuer.md)/Issue #49 unangetastet: die Zusammenfassung wird **einmal
  je Protokoll-PDF** berechnet und von jedem Row unverändert übernommen — Issue #49
  war ein Cache-Problem, kein Identitätsproblem.
- `autoren` wird nicht angefasst: sobald jeder Vorgang seinen eigenen Row hat, kann
  kein fremder Wert mehr einwandern. Ob der Initiator-Fallback (DD-042) für
  Redeprotokolle inhaltlich passt, ist eine eigene Frage.

**Warum das Backend N Rows über ein PDF verträgt:** Der Standardfall tut es längst.
Ohne LLM-Anreicherung — der Default — trägt jedes Dokument `sha256(link)` inklusive
Anker ([DD-048](DD-048-link-abgeleiteter-platzhalter-hash-statt-geteiltem-todo.md)), also einen Row je Fundstelle. [DD-047](DD-047-gesetzblatt-als-datums-lookup-statt-eigener-vorgangsquelle.md) hat
zusätzlich geprüft, dass zwei Vorgänge, die ein PDF teilen, beide mit HTTP 201
angenommen werden; Vorgangs-Matching läuft über `vg_ident`, nicht über Dokument-Hashes.

**Bewusst nicht geändert:**

- **Kein per-Gesetz-Narrowing.** Getrennte Rows machen bill-spezifische
  Zusammenfassungen technisch wieder möglich (Issue #49s Überschreib-Problem entfällt),
  aber das kostet einen LLM-Call je Gesetz statt je Sitzung. Bleibt beim
  sitzungsbezogenen Text (DD-037). Folge: `volltext` eines Rows ist sein eigenes
  Seitenfenster, `zusammenfassung` die der ganzen Sitzung.
- **Fundstellen ohne Anker** (54 von 1 134 Protokoll-Referenzen im WP17-Lauf, sämtlich
  „Beschluss des Landtags in Zweiter/Dritter Beratung"-Drucksachen, die der
  `enum_mapper` auf `REDEPROTOKOLL` abbildet) behalten den reinen Datei-Hash und damit
  einen geteilten Row. Dort gibt es nichts pro Vorgang zu erhalten — `titel` ist über
  alle zitierenden Vorgänge identisch.

**Migrationshinweis:** Aus je einem geteilten Protokoll-Row entstehen mehrere
Dokumente mit korrekten Ankern — gewollte Korrektur, sichtbar als neue Objekte
(wie bei DD-048). Die Redis-Cache-Einträge bleiben gültig, da ihr Schlüssel unverändert
der Datei-Hash ist.

**Code:** `bawue_dok.py::enrich_dokument` (`file_hash` vs. `doc_hash`, `cache_hash`)

**Tests:**
- `tests/unit/test_bawue_dok.py::TestPerVorgangProtocolRows` — zwei Fundstellen der
  Sitzung 17/137 erhalten verschiedene `hash_`, behalten je `titel`/`link`/`autoren`,
  teilen die Zusammenfassung (ein LLM-Call); gleiche Seite → ein Row; Fundstelle ohne
  Anker behält den Datei-Hash.
- `tests/integration/test_issue25_shared_protocol_rows.py` — dieselbe Kette gegen die
  Originalquellen: PARLIS liefert beide Anker, die PDF-Fenster belegen sie inhaltlich,
  die Anreicherung des echten PDFs ergibt zwei Rows bei einem LLM-Call.
- `tests/unit/test_bawue_dok.py::TestSharedRedeprotokollNeutralSummary` (Issue #49)
  unverändert in der Aussage, jetzt mit unterschiedlichen Ankern: verschiedene
  `hash_`, identische Semantik.
