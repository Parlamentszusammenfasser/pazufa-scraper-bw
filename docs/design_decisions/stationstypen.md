[← Index](../design_decisions.md)

# Stationstypen und Fundstellen

Wie aus PARLIS-Fundstellen Stationen und Dokumente werden. Die Mapping-Tabellen stehen in
`enum_mapper.py`; hier nur die Abweichungen und ihre Gründe.

<a id="dd-001"></a>
## DD-001: Änderungs- und Entschließungsanträge sind Dokumente, keine Stationen

**Kontext:** PARLIS führt Änderungs- und Entschließungsanträge als eigene Fundstellen. Als
`parl-initiativ` wären sie irreführend: Sie werden in einer Lesung eingebracht und abgestimmt,
sie starten kein Verfahren. Ein Entschließungsantrag begleitet den Entwurf inhaltlich
(z. B. Drs. 17/10262, V-244180).

**Entscheidung:** Beide hängen als Dokument (`antrag`) an der `parl-vollvlsgn`-Station, in der
sie behandelt werden: an der nächsten Lesung, sonst an der vorherigen. Gibt es keine Lesung,
wird die Fundstelle mit WARNING verworfen und gezählt (DD-017).

**Konsequenz:** Keine zusätzliche Station, kein Track-Risiko. Andere Scraper (z. B. RLP)
mappen „Antrag" pauschal auf `parl-initiativ`; BW weicht bewusst ab.

<a id="dd-002"></a>
## DD-002: „Mitteilung" bleibt `sonstig`

**Kontext:** Eine Mitteilung bedeutet je nach Absender und Zeitpunkt etwas anderes:
Überweisung an den Ausschuss, Umsetzungsbericht, Rechtsverordnung.

**Entscheidung:** „Mitteilung" ist kein Schlüssel in `STATIONSTYP_MAP` und fällt auf
`sonstig`. Der Dokumenttyp bleibt `mitteilung`.

**Konsequenz:** Eine pauschale Zuordnung (etwa auf `postparl-kraft`) wäre oft falsch. Mit
DD-017 erscheinen diese Stationen derzeit nicht.

<a id="dd-003"></a>
## DD-003: „Gesetzentwurf" hängt vom Initiator ab

**Kontext:** PARLIS nutzt „Gesetzentwurf" für Regierungs- und Fraktionsentwürfe gleich.

**Entscheidung:** Initiator enthält „Landesregierung" → `preparl-regbsl` (Dokument
`preparl-entwurf`); sonst `parl-initiativ` (Dokument `entwurf`). PARLIS zeigt den
Regierungsentwurf erst **nach** dem Kabinettsbeschluss, daher S und nicht R. Die
Entwurfsphase (`preparl-regent`) liefert das Beteiligungsportal (DD-007).

**Konsequenz:** Das einzige Mapping mit externem Kontext. Es erlaubt den BY-Track ohne
BW-Präfix (DD-016). Nach `preparl-regbsl` fehlt das `I`; DD-012 ergänzt es.

<a id="dd-005"></a>
## DD-005: Stellungnahmen hängen an der vorherigen Station

**Kontext:** Stellungnahmen und Antworten sind Reaktionen, kein eigener Verfahrensschritt.

**Entscheidung:** Eine Fundstelle gilt als Stellungnahme, wenn alle ihre Dokumente
`stellungnahme` sind oder sie ohne PDF „Stellungnahme"/„Antwort" im Text trägt. Ihre
Dokumente hängen an der unmittelbar vorherigen Station; ohne eine solche wird sie mit
WARNING verworfen (DD-017).

<a id="dd-008"></a>
## DD-008: Platzhalterdatum `00.00.JJJJ`

**Kontext:** PARLIS liefert `00.00.2028`, wenn nur das Jahr bekannt ist.

**Entscheidung:** Fallback in drei Stufen: `TT.MM.JJJJ` → erste vierstellige Jahreszahl als
1. Januar → aktueller Zeitpunkt. Alle Daten sind zeitzonenbewusst (UTC), weil das Backend
naive Daten mit HTTP 422 ablehnt.

<a id="dd-011"></a>
## DD-011: Whitespace normalisieren, Label-Teil des Rohtexts gegenprüfen

**Kontext:** Der Parser trennt `station_typ` am ersten Doppel-Leerzeichen. Steht eines
mitten im Label, wird „Beschluss des Landtags in Zweiter Beratung" zu „Beschluss des
Landtags" und mappt falsch auf `parl-akzeptanz`. Umgekehrt hängt PARLIS hinter die
Protokoll-Referenz Notizen an, deren Wörter den Typ verfälschen würden (V-230205,
„(Zulassung des Volksantrags)", Issue #13).

**Entscheidung:**
1. Das Mapping kollabiert Whitespace vor dem Substring-Match.
2. Zusätzlich wird der Rohtext **bis zur ersten Referenz** (Drucksache, Plenarprotokoll
   oder Datum) gemappt. Liefert das ein anderes Ergebnis als `sonstig`, gewinnt es (längerer,
   spezifischerer Schlüssel).

**Konsequenz:** Notizen hinter der Referenz typisieren keine Station mehr. Der
Dokumenttyp bleibt davon unberührt.

<a id="dd-017"></a>
## DD-017: `sonstig`-Stationen werden gefiltert, jedes Verwerfen ist sichtbar

**Kontext:** Das Backend stürzte bei `sonstig`-Stationen ab (`validate.rs`). Verworfene
Fundstellen waren zudem nur auf DEBUG sichtbar (GitHub #5).

**Entscheidung:** `[bawue] filter-sonstig-stations` (Default `true`) filtert sie, **nach**
den Sonderfällen DD-001/DD-005. Jede verworfene Fundstelle geht durch `_drop()`: eine
Log-Zeile mit Grund und ein Zähler je Vorgang, den die Run-Summary ausgibt.

| Grund | Level |
|---|---|
| `<Typ> → sonstig` | INFO |
| kein parsebares Datum | ERROR |
| Stellungnahme ohne vorherige Station (DD-005) | WARNING |
| Antrag ohne `parl-vollvlsgn` (DD-001) | WARNING |

**Neu bewerten, wenn:** das Backend `sonstig` akzeptiert — dann Filter auf `false`.

<a id="dd-018"></a>
## DD-018: entfernt

Der Workaround für nachparlamentarische Ausschussberichte existiert nicht mehr.

<a id="dd-019"></a>
## DD-019: „Antrag" nach einem Ausschussbericht ist ein Änderungsantrag

**Kontext:** PARLIS kennzeichnet Änderungsanträge oft nur als „Antrag". Nach einem
`parl-ausschber` ist das immer ein Änderungsantrag zur Beschlussempfehlung (V-214623).

**Entscheidung:** Ist das Label „Antrag"/„Anträge" und kam schon ein `parl-ausschber`,
wird die Fundstelle wie DD-001 behandelt.

<a id="dd-031"></a>
## DD-031: Unbeschriftete Plenarprotokoll-Fundstelle als erste Lesung

**Kontext:** PARLIS liefert die Erste Beratung manchmal ohne Label, nur „Plenarprotokoll
18/6 …" (V-246637). Sie wurde `sonstig` und gefiltert. Einer Ablehnung fehlte damit eine
Lesung (`IAVN` statt `IVAVN`), HTTP 400 (DD-016).

**Entscheidung:**
1. Die **erste** unbeschriftete Fundstelle mit Plenarprotokoll wird `parl-vollvlsgn`,
   solange noch keine Lesung erfasst ist. Weitere bleiben `sonstig`.
2. Härtung: Die Ausschussbericht-Neudatierung (DD-025) läuft vor der synthetischen
   Ablehnung (DD-010), und deren Anker ist die chronologisch letzte Station, nicht die
   letzte nach Listenposition.

<a id="dd-032"></a>
## DD-032: Issue #33 war schon behoben (nur Verifikation)

Falsche Runde bzw. falscher Link bei Lesungsstationen ließ sich mit dem aktuellen Code
nicht reproduzieren. Fall 2 (Haushalts-Einzelplan) beheben DD-024/DD-026. Keine
Code-Änderung; `tests/unit/test_issue33_fundstelle_mapping.py` pinnt das Verhalten. Dass
das Frontend Einzelpläne als „1./2. Lesung" zählt, ist dessen Anzeige.

<a id="dd-057"></a>
## DD-057: Neue Spec-Werte bleiben bewusst ungemappt

**Kontext:** Spec 0.2.5 brachte `preparl-formvs`, `parl-antragsst`, `parl-vermittas`,
`parl-verfgstop` (seit 0.2.7 `postparl-vgstp`) und den Doktyp `eckpunktepapier`. In PARLIS
WP 17/18 und im Beteiligungsportal kommt keiner vor (GitHub #43).

**Entscheidung:** Keiner wird gemappt.

| Wert | Grund |
|---|---|
| `preparl-formvs` | Formulierungshilfen erscheinen in PARLIS als normaler Fraktionsentwurf, nicht erkennbar |
| `parl-antragsst` | Antrags-Vorgangstypen werden nicht gescrapt |
| `parl-vermittas` | Der Landtag hat keine zweite Kammer |
| `postparl-vgstp` | PARLIS führt Verfassungsgerichtsstopps nicht als Station |
| `eckpunktepapier` | Kommt nicht vor; bei Bedarf reicht ein Titel-Mapping im Beteiligungs-Scraper |

**Konsequenz:** Der BW-Track hat für keinen der Stationstypen einen Buchstaben, eine solche
Station gäbe HTTP 400. Ein Kanarienvogel-Test prüft `Stationstyp` auf Gleichheit
(`UNPRODUCED_STATIONSTYPEN`): Jeder neue Spec-Wert lässt ihn scheitern, bis er gemappt oder
dort eingetragen ist.

**Neu bewerten, wenn:** PARLIS oder das Portal Eckpunkte/Formulierungshilfen ausweist, BW
Antrags-Vorgänge scrapt oder der Track Buchstaben dafür bekommt.
