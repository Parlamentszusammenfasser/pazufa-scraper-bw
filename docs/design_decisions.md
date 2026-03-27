# Designentscheidungen

Dieses Dokument hält Architektur- und Datenmodellierungsentscheidungen fest, die von
den PaZuFa-Standardkonventionen abweichen oder einer Erklärung bedürfen.

---

## DD-001: Änderungsanträge als Dokumente, nicht als Stationen

**Datum:** 27.03.2026

**Kontext:** PARLIS listet Änderungsanträge als separate Fundstellen auf,
wodurch der Scraper einzelne Stationen mit dem Typ `parl-initiativ` erzeugt.
Das ist irreführend, da ein Änderungsantrag keine neue parlamentarische Initiative
darstellt -- er ist ein Änderungsvorschlag, der im Rahmen einer Plenarlesung
(Beratung) eingebracht wird.

Andere PaZuFa-Scraper (z. B. RLP) bilden Änderungsanträge auf `parl-initiativ` ab
und folgen damit dem generischen Substring-Match auf "Antrag". Für BaWue überschreiben
wir diese Konvention, da die PARLIS-Daten ausreichend Kontext liefern, um
Änderungsanträge genauer zu modellieren.

**Entscheidung:** Änderungsanträge werden als Dokumente an die `parl-vollvlsgn`-Station
(Plenarlesung) angehängt, in der sie behandelt wurden. Sie erzeugen keine eigene
Station. Falls eine nachfolgende `parl-vollvlsgn`-Station existiert, werden die
Dokumente dort angehängt; andernfalls wird auf die vorhergehende zurückgegriffen.
Falls überhaupt keine `parl-vollvlsgn`-Station existiert, wird der Änderungsantrag
mit einer Warnung verworfen.

**Entscheidung:** Entschließungsanträge werden vollständig verworfen und erscheinen
nicht in der Ausgabe. Sie sind prozedurale Anträge ohne Bezug zum legislativen
Inhalt des Vorgangs.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methode `_collect_stationen()`.
Die Erkennung basiert auf dem Feld `station_typ`, das aus dem Fundstellentext
extrahiert wird (z. B. "Änderungsanträge", "Entschließungsantrag"), und wird
geprüft, bevor das generische Enum-Mapping greift.

---

## DD-002: Mitteilungen werden bewusst als `sonstig` klassifiziert

**Datum:** 27.03.2026

**Kontext:** PARLIS listet "Mitteilung"-Fundstellen als eigenständige Schritte im
Gesetzgebungsverfahren auf. Diese Mitteilungen sind generische prozedurale
Benachrichtigungen, deren Bedeutung vom Kontext abhängt — insbesondere vom
Absender und vom Zeitpunkt im Verfahren. Beispiele:

- **Mitteilung der Präsidentin des Landtags** (zwischen Erster Beratung und
  Ausschussberatung): Typischerweise die formale Überweisungsmitteilung an den
  zuständigen Ausschuss.
- **Mitteilung der Landesregierung** (nach Verkündung im Gesetzblatt): Häufig ein
  Umsetzungsbericht, eine Rechtsverordnung oder eine Statusmitteilung zum
  Inkrafttreten — oft Monate nach der Verkündung.

Das Wort "Mitteilung" ist absichtlich kein Schlüssel in `STATIONSTYP_MAP`. Da keine
einzelne Stationstyp-Zuordnung alle Varianten korrekt abbilden kann, fällt
`map_stationstyp()` auf den Default `Stationstyp.SONSTIG` zurück.

**Entscheidung:** Mitteilungen werden als `sonstig`-Stationen belassen. Eine
pauschale Zuordnung zu einem spezifischen Stationstyp (z. B. `postparl-kraft`)
wäre in vielen Fällen falsch. Der Dokumententyp `Doktyp.MITTEILUNG` wird weiterhin
korrekt über `DOKUMENTENTYP_MAP` zugeordnet.

**Implementierung:** `enum_mapper.py`, Funktion `map_stationstyp()` — der
Fallback `return Stationstyp.SONSTIG` greift, wenn kein Schlüssel in
`STATIONSTYP_MAP` matched. Die Zuordnung `"Mitteilung" → Doktyp.MITTEILUNG`
in `DOKUMENTENTYP_MAP` bleibt davon unberührt.
