[← Index](../design_decisions.md)

# Sitzungen (Kalender)

Wie `BawueSitzungenScraper` den Kalender füllt. Randbedingungen des Backends: Ein
Collector-Key schreibt nur Tage ab gestern (UTC), `PUT kalender` ersetzt einen ganzen Tag, und
einen leeren Tag kann er nicht leeren.

<a id="dd-069"></a>
## DD-069: Der ICS-Feed bleibt die Quelle — ausbauen statt ersetzen

**Kontext:** Bewertung der Kette ICS → `ics_parser` → `PUT kalender` (Issue #85). Vergangene
Sitzungen kann keine Quelle nachträglich ergänzen (Collector-Keys schreiben nicht zurück);
keine andere Quelle reicht so weit voraus (PARLIS: DD-068; Tagesordnungen: nur die nächste
Plenarsitzung).

**Entscheidung:**
1. Der ICS-Feed bleibt die Quelle der Termine. Geplantes steht im Kalender, Geschehenes in
   den Stationen (DD-068).
2. Die Tagesordnung ergänzt die nächste Plenarsitzung um TOPs und `nummer` (DD-070).
3. Jeder benannte Ausschuss wird eine Sitzung (DD-006).
4. Die `api_id` enthält UID, Datum und Gremium: `uuid5("{uid}|{date}|{gremium}")`. Eine
   verlegte Sitzung kollidiert so nicht mit ihrer alten Zeile (`sitzung.api_id` ist UNIQUE,
   Issue #116).

**Verworfen:** PARLIS oder Website als Hauptquelle; vergangene Sitzungen vervollständigen;
`PUT /sitzung/{api_id}` (Admin-Scope).

**Konsequenz:** Ein ganz entfallener Tag lässt sich nicht leeren (braucht eine
Backend-Änderung: leerer Body leert den Tag). Das Backend leert Tage nach UTC, der Scraper
gruppiert nach Berliner Tag; Termine vor 01:00/02:00 Uhr liegen in UTC am Vortag (bisher
keiner im Feed).

<a id="dd-006"></a>
## DD-006: Plenum und jeder benannte Ausschuss, je Kürzel eine Sitzung

**Kontext:** Der Feed hat rund acht Eventkategorien. Inzwischen nennt er Ausschüsse per
Kürzel, oft mehrere je Termin (`FinA, BildungsA, VerkA`, Issue #117).

**Entscheidung:**

| SUMMARY-Präfix | Gremium |
|---|---|
| `Plenarsitzung:` | `plenum` |
| `Fraktions- und Ausschusssitzungen: <Kürzel>, …` | je Kürzel eine Sitzung |
| `Haushaltsberatungen: <Kürzel>, …` | wie oben |

- `COMMITTEE_NAMES` bildet das Kürzel auf den Namen ab, den auch PARLIS nutzt. Das Backend
  sucht Gremien über den exakten Namen; Kalender und Stationen teilen so ein Gremium.
- Unbekanntes Kürzel: WARNING, übersprungen. Geraten wird nicht.
- Ausgeschlossen: Fraktionen, Sammel-Eintrag ohne Ausschussnamen (DoD: Namen so spezifisch
  wie möglich), Präsidium, Gedenktag, Wahl.

<a id="dd-064"></a>
## DD-064: `Sitzung.nummer` ist `0`, wenn keine Quelle sie liefert

**Kontext:** Pflichtfeld. Der ICS-SUMMARY trägt keine Nummer mehr, Ausschüsse veröffentlichen
keine, der Sitzungsplan auch nicht (Issue #65).

**Entscheidung:** Ohne Quelle `nummer = 0`, nicht geraten, WARNING je Sitzung. Der Regex
`(\d+)\.\s*Sitzung` bleibt; liefert der Feed die Nummer wieder, wird sie übernommen. Für die
nächste Plenarsitzung kommt sie aus der Tagesordnung (DD-070).

<a id="dd-070"></a>
## DD-070: TOPs aus der Plenar-Tagesordnung

**Kontext:** `/de/aktuelles/tagesordnungen` hat das PDF der **nächsten** Plenarsitzung
(Dateiname mit Datum und Nummer). Das Backend verknüpft einen TOP mit einem Vorgang nur über
ein gemeinsames Dokument. Trifft das keine bestehende Zeile, legt es eine neue an, die den
nächsten Upload des Vorgangs mehrdeutig macht (`AmbiguousMatch`, Issue #66).

**Entscheidung:**
1. Die Tagesordnung ergänzt die Plenarsitzung desselben Tages im selben `PUT`; fehlt die
   `nummer`, kommt sie aus dem Dateinamen.
2. TOP-Titel: vom Punktbeginn bis zur ersten Zeile „– Drucksache …" (unter Linux und macOS
   gleich).
3. Dokumente nur für Lesungen eines Gesetzentwurfs, und nur mit einem bereits hochgeladenen
   Hash: Der Vorgänge-Scraper registriert nach jedem Upload `drucks-hash:<drucksnr>`. Fehlt er,
   fehlt das Dokument; der Tag wird im nächsten Lauf neu versucht.
4. Cache-Wert `<Termine>:<Tagesordnung>` (DD-066). Verschwindet die Tagesordnung von der Seite,
   bleibt der Tag gecacht, damit seine TOPs nicht verloren gehen.
5. Ist die Tagesordnung nicht lesbar, geht der Kalender ohne TOPs hoch.

**Verworfen:** die Drucksache selbst hashen (erzeugt die gefährliche neue Zeile); Sitzungen
erst nach den Vorgängen laufen lassen (mit dem Register unnötig, der Kalender hinge an einem
langen Lauf).

**Konsequenz:** Ohne LLM bleibt das Hash-Register leer, TOPs haben keine Dokumente.
Ausschüsse bekommen keine TOPs. Ändert sich ein Termin, nachdem die Tagesordnung
verschwunden ist, gehen seine TOPs verloren.

<a id="dd-076"></a>
## DD-076: Beteiligungsfristen im Kalender

**Kontext:** Die Frist eines Entwurfs (DD-075) soll auch im Kalender stehen, mit Verweis auf
Vorgang und Portal (Issue #148). `PUT kalender` ersetzt den ganzen Tag, egal welcher Scraper
ihn geschrieben hat.

**Entscheidung:**
1. Nach jedem Upload legt der Beteiligungs-Scraper Frist, Titel, Portal-Link und den Hash des
   ersten Entwurfs in `beteiligungsfristen` (Redis) ab, den Hash nur für ein gelesenes
   Dokument (wie DD-070). Scheitert das Schreiben, bleibt der Vorgang ungecacht.
2. Der Sitzungen-Scraper nimmt jede Frist ab gestern (UTC, wie das Backend) in den PUT ihres
   Tages auf, auch an Tagen ohne Landtagstermin: eine Sitzung von `regierung` um 23:59 Uhr
   (Berlin), Titel `Beteiligungsfrist: <Titel>`, `link` aufs Portal, `nummer` 0, ein TOP mit
   dem Entwurf.
3. `api_id` aus Slug und Datum. Der Fingerprint eines Tages mit Frist enthält die Einträge;
   Tage ohne Frist behalten ihren.
4. Ein Marker `fristen` im Beteiligungs-Fingerprint baut gecachte Vorgänge einmal neu und
   füllt so das Register.

**Konsequenz:** Ohne LLM hat der TOP kein Dokument, die Sitzung also keinen Vorgang. Eine
verlängerte Frist erscheint am neuen Tag; den alten leert nur ein anderer PUT dieses Tages
(DD-069). Ändert sich die Frist an einem Plenartag, dessen Tagesordnung schon von der Seite
verschwunden ist, gehen seine TOPs verloren (wie DD-070). Die Website zeigt den Kalender bisher
nicht an.
