[← Index](../design_decisions.md)

# DD-034: Stabile `api_id` für *alle* Stationen (auch dokumenttragende)

**Datum:** 10.07.2026

**Kontext:** Issue #47. 45 Vorgänge scheiterten im Staging mit HTTP 500
(Duplikat-Key-Verletzung auf `rel_station_dokument_pkey`). Betroffen waren die
Haushalts-Einzelpläne: Jeder Einzelplan-Vorgang zitiert dieselbe geteilte
Staatshaushaltsgesetz-Drucksache (17/1000) als Gesetzentwurf-PDF.

**Ursache:** DD-028 gab nur *dokumentlosen* Stationen eine stabile `api_id` und
ließ dokumenttragende Stationen bewusst per Dokument-Hash matchen
(`if not isinstance(station.api_id, Unset) or station.dokumente: continue`).
Da identische PDFs vorgangsübergreifend wiederkehren, matcht der Hash aber
*über Vorgänge hinweg*: das Backend führt (per `station_merge_candidates`,
DD-010) Stationen fremder Vorgänge zusammen und verletzt dabei den
Primärschlüssel `(station, dokument)`. Der erste Sibling persistierte, der
zweite und alle folgenden brachen ab — deterministisch über alle 45 Fälle.

Ein Sekundärproblem verstärkte das: `_ensure_initiativ_after_regbsl` kopierte
die Dokumentliste des `preparl-regbsl` nur flach (`list.copy()`), sodass die
synthetische `parl-initiativ`-Station dasselbe `Dokument`-Objekt aliaste.

**Entscheidung:**

1. Die Skip-Bedingung `or station.dokumente` in `_assign_stable_station_ids`
   entfällt: **jede** Station ohne eigene `api_id` erhält eine
   Vorgang-skopierte `api_id`. Die Identität hängt damit an `(vorgang_id, typ,
   zp_start)` statt am geteilten Dokument-Hash — kein vorgangsübergreifendes
   Matching mehr.
2. ♻️ *(Ursprungsfassung, abgelöst — s. Update unten)* Dokumenttragende
   Stationen falteten zusätzlich ihre Dokument-Links in den Schlüssel. Zwei
   gleich-typige Stationen dürfen sich ein `zp_start` teilen
   (`_enforce_total_ordering` verschiebt nur verschieden-typige Kollisionen),
   tragen aber ggf. verschiedene Dokumente und dürfen nicht auf *eine* `api_id`
   kollabieren. Dokumentlose Schlüssel behalten das exakte DD-028-Format, damit
   bereits persistierte Zeilen weiter matchen.

   **Update 19.07.2026 (Issue #66):** Der Schlüssel ist jetzt
   **dokumentunabhängig**. Junge PARLIS-Einträge listen Fundstellen, bevor
   deren PDFs existieren (WP18 V-246637: Gesetzentwurf 18/75 am 12.07. ohne,
   am 18.07. mit PDF). Ein dokumentabhängiger Schlüssel ändert sich, sobald
   das PDF nachgeliefert wird — das Backend kann die Station nicht mehr der
   persistierten Zeile zuordnen, behält beide, und die doppelte
   `parl-initiativ` (ungültige `II`-Sequenz) lässt **jeden** weiteren Upload
   an der Track-Validierung scheitern (HTTP 400). Gleich-typige
   `zp_start`-Kollisionen werden stattdessen über die Listenposition
   disambiguiert (`-tie2`, `-tie3`, … ab der zweiten Station eines
   `(typ, zp_start)`-Schlüssels); die erste Station behält das exakte
   DD-028-Format, damit bereits persistierte Zeilen weiter matchen.
   Regressionstests: `test_station_api_id_stable_when_document_arrives_later`,
   `test_station_id_matches_persisted_docless_row_after_document_arrives`,
   `test_same_typ_same_date_tie_ids_stable_when_docs_change` (unit) und
   `TestStationIdStabilityAcrossRescrapes` (integration).
3. `_ensure_initiativ_after_regbsl` nutzt `deepcopy` statt `list.copy()`, sodass
   die synthetische `parl-initiativ`-Station eigene Dokument-Objekte besitzt und
   keine Mutation (z. B. die stabile `api_id`) zwischen den Stationen leakt.
   Der Deep-Copy läuft über `_deepcopy_preserving_unset` (Memo `{id(UNSET): UNSET}`):
   ein nacktes `deepcopy` klont das `UNSET`-Sentinel pro optionalem Feld, wodurch
   corelibs identitätsbasierte `to_dict`-Prüfung (`is not UNSET`) fehlschlägt und
   ein rohes `Unset` in den Payload leakt → `json.dumps` wirft "Object of type
   Unset is not JSON serializable" (72 % der WP17-Uploads schlugen so fehl).

**Implementierung:** `bawue_vorgaenge_scraper.py`, Funktionen
`_assign_stable_station_ids()` und `_ensure_initiativ_after_regbsl()`.

**Tests:** `tests/unit/test_bawue_scraper.py` —
`test_document_bearing_station_gets_stable_api_id`,
`test_assign_stable_station_ids_skips_only_existing`,
`test_same_typ_same_date_stations_with_different_docs_get_distinct_ids`,
`test_budget_siblings_sharing_pdf_get_distinct_station_ids` (Issue-#47-Muster),
`test_synthetic_initiativ_deep_copies_regbsl_documents`,
`test_synthetic_initiativ_documents_serialize_to_json` (UNSET-Sentinel bleibt
JSON-serialisierbar).

**Folgearbeit (Backend-seitig, einmalig):** Wie bei DD-028 heilt der Fix nur
*künftige* Läufe; die bereits kollidierten 45 Vorgänge müssen einmalig über den
Admin-Endpunkt neu eingelesen (bzw. bereinigt) werden, da die neue stabile
`api_id` die alten `api_id=None`-Waisen nicht matcht.
