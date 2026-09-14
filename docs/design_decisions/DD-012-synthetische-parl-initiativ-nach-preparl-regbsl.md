[← Index](../design_decisions.md)

# DD-012: Synthetische `parl-initiativ` nach `preparl-regbsl`

**Datum:** 29.03.2026 | **Aktualisiert:** 05.04.2026

**Kontext:** Die Backend-Track-Definition für BaWue-Gesetzgebung verlangt nach
der vorparlamentarischen Phase (`((E*R+)?S)?`) zwingend eine `parl-initiativ`-Station
(`I`), bevor die parlamentarische Bearbeitung beginnt (`VA*...`). Bei
Fraktionsentwürfen ist dies unproblematisch — der Fundstellentext „Gesetzentwurf"
wird direkt als `parl-initiativ` klassifiziert (s. DD-003).

Bei Regierungsentwürfen entsteht eine Lücke: PARLIS verwendet **eine einzige
Fundstelle** „Gesetzentwurf" für den gesamten Vorgang der Einbringung. Diese
wird als `preparl-regbsl` (Kabinettsbeschluss) klassifiziert — PARLIS zeigt den
Entwurf **nach** dem Kabinettsbeschluss, also den parlamentarischen Eingang.
Die vorparlamentarische Entwurfsphase (`preparl-regent`, R) wird bereits vom
Beteiligungsportal-Scraper abgedeckt. PARLIS liefert keine separate Fundstelle
für die parlamentarische Einbringung; die nächste Fundstelle ist direkt
„Erste Beratung" (`parl-vollvlsgn`).

**Evidenz:** Überprüfung auf der PARLIS-Website (4 Vorgänge, WP 16 + WP 17)
bestätigt, dass Regierungsentwürfe durchgängig von „Gesetzentwurf Landesregierung"
direkt zu „Erste Beratung" springen — ohne Zwischeneintrag. Das Feld „Initiative"
auf der Detailseite ist ein Metadatenfeld am Vorgang, keine eigene Fundstelle.

**Entscheidung:** Analog zu DD-010 (synthetische Ablehnung) wird eine synthetische
`parl-initiativ`-Station eingefügt, wenn auf `preparl-regbsl` nicht bereits eine
`parl-initiativ` folgt. Die synthetische Station übernimmt die Dokumente der
`preparl-regbsl`-Station (der Gesetzentwurf *ist* die parlamentarische Initiative)
und erhält als Datum den Zeitpunkt der nächsten Station (typischerweise die Erste
Beratung). Dies ist semantisch korrekt: Die Einbringung eines Regierungsentwurfs
in den Landtag stellt gleichzeitig die parlamentarische Initiative dar — PARLIS
bildet lediglich beide Schritte in einer Fundstelle ab.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methoden `_build_vorgang()`
und `_ensure_initiativ_after_regbsl()`.
