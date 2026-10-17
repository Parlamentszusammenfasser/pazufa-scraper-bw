[← Index](../design_decisions.md)

# Caching

Wann ein Vorgang oder Sitzungstag neu hochgeladen wird. Die Schlüssel haben kein TTL. Ein
voller Neubau eines Bestands heißt: die Einträge des Präfixes löschen. Eine eingebaute
Versionskonstante gibt es bewusst nicht; ein gezielter Neubau ändert den Fingerprint
(Beispiel: DD-071).

| Präfix | Inhalt | DD |
|---|---|---|
| `vg2:` | Fingerprint je PARLIS-Vorgang bzw. Beteiligungsprozess | DD-052 |
| `sz:` | Fingerprint der Termine (+ Tagesordnung) je Tag | DD-066, DD-070 |
| `llm-semantics:` | LLM-Ergebnis je Dokument und Prompt | DD-020 |
| `vorgang-kurztitel:`, `vorgang-ressort:` | LLM-Ergebnis je Vorgang | DD-053, DD-055, DD-063 |
| `drucks-hash:` | Hash hochgeladener Drucksachen für TOPs | DD-070 |
| `wochenbericht` | letzte begonnene Station je Vorgang, Stand der Vorwoche | DD-072 |

<a id="dd-073"></a>
## DD-073: Redis ist Pflicht — ohne Redis startet kein Lauf

**Kontext:** Ohne Cache baut ein Lauf jeden Vorgang neu, lädt alle PDFs und zahlt alle
LLM-Calls erneut. Der Dry-Run, der ohne Redis lief, ist entfernt (GitHub #122).

**Entscheidung:** Ist Redis beim Start nicht erreichbar (nach drei Wiederholungen), bricht der Lauf
ab: `RedisUnavailableError`, eine Alarm-Nachricht an Mattermost, Exit-Code 1. Fehlt `[cache]`,
gilt `localhost:6379`; ein stiller Lauf ohne Cache ist nicht vorgesehen.

**Konsequenz:** Cloud Run wiederholt einen gescheiterten Lauf einmal, das gibt bis zu zwei
Alarme. Ein Ausfall *während* des Laufs ist eigene Arbeit (GitHub #105).

<a id="dd-052"></a>
## DD-052: Fingerprint des PARLIS-Records im `vg2:`-Cache

**Kontext:** Der Schlüssel war nur die Vorgangs-ID, jeder Eintrag ein Treffer. Ein Vorgang
blieb auf dem Stand des ersten Uploads (Issue #46). Der Record liegt bei jedem Lauf ohnehin
vollständig vor.

**Entscheidung:**
1. `vg2:<vorgangs_id>` speichert einen Fingerprint. Weicht er ab, wird der Vorgang mit
   denselben stabilen IDs neu gebaut und hochgeladen.
2. Gehasht wird, was Fortschritt anzeigt: je Fundstelle Rohtext + `pdf_url` (sortiert) und
   „Aktueller Stand". Dazu ein Marker `postparl-kraft`, wenn eine Fundstelle eine
   Gesetzblatt-Nr. trägt: der einmalige Neubau gecachter Gesetze für DD-071.
3. Nicht gehasht: vom Parser abgeleitete Felder (sonst invalidiert jede Parser-Verbesserung
   alles), die Reihenfolge, Titel/Initiative/Sachgebiet/Typ/Deskriptoren, Scraper-Output.
4. Ein Vorgang mit noch fehlendem PDF (Issue #66) oder künftiger Fundstelle (DD-068) wird
   nicht gecacht.
5. **Beteiligungsportal** (Issue #104): `vg2:<slug>` hält den Fingerprint aus Status (Liste),
   Titel, Ministerium, Kommentarfrist und PDF-Links (URL + Titel). Dafür wird die
   Detailseite schon beim Listen gelesen, ein Request je Prozess und Lauf (derzeit ~6) —
   nur so fallen ein neues PDF oder eine geänderte Frist auf. Scheitert der Abruf, gibt es
   keinen Fingerprint: kein Treffer, kein Cache-Eintrag.

**Konsequenz:** Neue abgeleitete Felder erreichen abgeschlossene Vorgänge erst nach Löschen
der `vg2:`-Einträge. Ein Neubau lädt die PDFs erneut; LLM-Calls fallen nur für neue
Dokumente an. Ein unter gleicher URL ausgetauschtes PDF bleibt unbemerkt (#73). Die alten
Beteiligungs-Einträge (Vorgang-JSON) treffen nie und werden einmal neu gebaut.

<a id="dd-066"></a>
## DD-066: Fingerprint der Termine im `sz:`-Cache

**Kontext:** Wie DD-052, für Sitzungen: Der Schlüssel war nur das Datum, nach dem ersten Lauf
erreichte keine Änderung mehr das Backend (Issue #85).

**Entscheidung:** `sz:sha256(datum)` speichert den Fingerprint der Termine des Tages: je
Termin UID, `DTSTART`, SUMMARY, geparstes Gremium und `nummer`, dazu die Wahlperiode;
sortiert. Anders als DD-052 auch Parser-Werte: ein Neu-Upload kostet nur einen `PUT`. Nicht
gehasht: `DTEND`, `DTSTAMP`.

**Konsequenz:** `PUT` ersetzt den ganzen Tag, ein entfallener Termin verschwindet damit. Ein
Tag, dessen Termine alle entfallen, fällt aus dem Feed; seine Sitzung bleibt im Backend
(DD-069).
