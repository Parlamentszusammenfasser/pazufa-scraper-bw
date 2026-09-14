[← Index](../design_decisions.md)

# DD-046: Datumsfreie `api_id` für die synthetische `parl-initiativ`-Station (V-247045)

**Datum:** 31.07.2026

**Kontext:** Ein Staging-Lauf gegen WP18 lehnte den Vorgang V-247045 („Gesetz zu dem
Zweiten Staatsvertrag zur Änderung des Glücksspielstaatsvertrags 2021") mit
HTTP 400 Track validation ab. Die vom Backend gemeldete Stationsfolge war
`S S I L` — ein doppeltes `preparl-regbsl` —, obwohl der Scraper lokal ein
korrektes `S I L` baut. Ursache ist keine fehlerhafte Stationsbildung, sondern eine
*instabile Stationsidentität* über Läufe hinweg:

`_ensure_initiativ_after_regbsl` datiert die synthetische `parl-initiativ`-Station auf
das früheste `zp_start` der dem `preparl-regbsl` folgenden Stationen (DD-035). Dieses
Datum ist keine Eigenschaft der Station selbst, sondern hängt davon ab, welche
Fundstellen zum Scrape-Zeitpunkt bereits existieren: Solange nur der Gesetzentwurf
vorlag, fiel die Station auf den 30.06.2026 (durch `_enforce_total_ordering` auf
`01:00` gestoßen); nachdem die Erste Beratung erschien, auf den 23.07.2026. Der
DD-028-Schlüssel `bawue-station-{vid}-{typ}-{zp_start}` enthält genau dieses
`zp_start`, die `api_id` wanderte also mit (`3320efb1-…` → `be263aec-…`). Das Backend
konnte die persistierte Zeile nicht mehr zuordnen und behielt beide — dieselbe
Fehlerklasse, die DD-034/Issue #66 bereits für dokumentabhängige Schlüssel beschrieb,
hier nur über das Datum statt über die Dokumente.

Verschärfend kommt hinzu, dass die synthetische Station die Dokumente des
`preparl-regbsl` per Deepcopy übernimmt (Issue #47). Beide Stationen tragen damit
denselben Dokument-Hash, sodass auch der Hash-basierte Fallback des Backends
(`station_merge_candidates`) nicht eindeutig auflösen kann, welche der beiden Zeilen
gemeint ist.

**Entscheidung:** Die synthetische `parl-initiativ` erhält ihre `api_id` **bei der
Erzeugung** und **datumsfrei** aus `uuid5(NAMESPACE_URL, f"bawue-synth-initiativ-{vorgang_id}")`
— exakt das Vorgehen, das die synthetische `parl-ablehnung` seit DD-010 nutzt. Pro
Vorgang existiert höchstens eine solche Station (sie wird nur eingefügt, wenn dem
`preparl-regbsl` keine `parl-initiativ` folgt), der Vorgangsbezug allein scopet die
Identität also eindeutig. Für `vorgang_id == "unknown"` bleibt die `api_id` UNSET,
konsistent mit `_assign_stable_station_ids`, das ohne Vorgangs-ID ebenfalls keinen
Schlüssel bilden kann.

**Bewusst eng gehalten:** Der DD-028-Schlüssel für *reale*, aus Fundstellen gebaute
Stationen bleibt unverändert. Deren `zp_start` stammt aus dem Fundstellentext und ist
über Läufe stabil; ein Umstellen auch dieser Schlüssel würde alle bereits
persistierten Zeilen neu verschlüsseln und damit genau das Duplikat-Problem
auslösen, das diese DD behebt (die Issue-#66-Zusage
`test_station_id_matches_persisted_docless_row_after_document_arrives` bleibt grün).

**Einmalige Folge:** Bereits persistierte synthetische `parl-initiativ`-Zeilen wurden
unter dem alten, datumsabhängigen Schlüssel gespeichert und werden beim nächsten
Upload einmalig nicht wiedergefunden. Diese Zeilen sind durch den beschriebenen Bug
ohnehin bereits nicht mehr zuverlässig zuordenbar; Altbestände müssen backendseitig
bereinigt werden (der Collector-Key hat keine Delete-Rechte).

**Implementierung:** `bawue_vorgaenge_scraper.py` — `_synthetic_initiativ_api_id()`,
`_ensure_initiativ_after_regbsl(stationen, vorgang_id)` und deren Aufrufstelle in
`_build_vorgang`.

**Tests** (`tests/unit/test_bawue_scraper.py`):
`TestBuildVorgang::test_synthetic_initiativ_id_survives_arrival_of_first_reading` —
Regression über die realen V-247045-Fundstellen: derselbe Vorgang einmal nur mit dem
Gesetzentwurf und einmal zusätzlich mit der Ersten Beratung gebaut; die `api_id` der
synthetischen `parl-initiativ` (und die des realen `preparl-regbsl`) muss identisch
bleiben.
