[← Index](../design_decisions.md)

# DD-001: Änderungsanträge als Dokumente, nicht als Stationen

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

**Aktualisierung (27.09.2026, GitHub Issue #5):** Entschließungsanträge werden **nicht
mehr verworfen**, sondern wie Änderungsanträge als Dokument (`antrag`) an die
`parl-vollvlsgn`-Station gehängt, in der über sie abgestimmt wird. Die Begründung oben
(„prozedural, ohne Bezug zum Inhalt") trifft nicht zu: Ein Entschließungsantrag begleitet
den Gesetzentwurf inhaltlich (z. B. SPD, Drs. 17/10262 zum Juristenausbildungsgesetz,
V-244180). Kosten entstehen keine zusätzlich — das PDF wurde schon vorher geladen und
angereichert, nur danach weggeworfen. Keine neue Station, daher kein Track-Risiko; die
Stations-`api_id` ist dokumentunabhängig (DD-034).

Findet sich keine `parl-vollvlsgn`-Station, wird jede solche Fundstelle einzeln mit
WARNING verworfen und gezählt (s. DD-017).

**Rollout:** Der `vg2:`-Fingerprint (DD-052) hasht nur PARLIS-Daten; bereits gecachte
Vorgänge erhalten ihre Entschließungsanträge erst bei der nächsten PARLIS-Änderung.

**Tests:** `tests/unit/test_issue5_dropped_documents.py` (V-244180),
`TestEntschliessungsantragHandling`.
