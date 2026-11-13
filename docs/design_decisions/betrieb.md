[← Index](../design_decisions.md)

# Betrieb, Wahlperiode und Upgrades

Einstellungen, Abhängigkeiten und Berichte, die alle Scraper betreffen.

<a id="dd-062"></a>
## DD-062: Nur Wahlperiode 18, eine einzige Einstellung

**Kontext:** Seit 2026-05-01 gilt WP 18. Die Wahlperiode war an drei Stellen einzustellen,
die zusammengehören (Issues #63, #6).

**Entscheidung:**
1. Prod scrapt nur WP 18. Ein WP-17-Nachlauf bleibt lokal möglich (`config.wp17.toml`).
2. `[bawue] wahlperiode` (env `WAHLPERIODE`) gilt für alle Scraper; Default
   `CURRENT_WAHLPERIODE`.
3. Der Beginn kommt aus `bawue/wahlperiode.py` (WP → Konstituierung).
   `wahlperiode-start-date` grenzt nur ein; ein Datum vor dem Beginn oder in der Zukunft
   bricht ab.

**Für WP 19:** Eintrag in `WAHLPERIODE_START` ergänzen, dann `WAHLPERIODE` umstellen.
`check_for_newer_wahlperiode` warnt rechtzeitig.

<a id="dd-051"></a>
## DD-051: corelib v0.2.1 / Spec 0.2.5

**Entscheidung:**
1. `Station.trojanergefahr` fiel weg; der Score wird nicht mehr erfragt (bezahlter Output
   ohne Abnehmer).
2. „Gesetzblatt" und „Gesetz" mappen auf `Doktyp.GESETZ` (der Gesetzestext selbst);
   „Bekanntmachung" bleibt `mitteilung`.
3. Neue Felder wurden einzeln umgesetzt (DD-054 bis DD-058); neue Enum-Werte: DD-057.

**Konsequenz:** Ein Spec-Konformitätstest validiert den gesendeten Vorgang gegen das
Pydantic-Modell der corelib — der attrs-Client serialisiert ungeprüft, Drift fiele sonst erst
als HTTP 422 auf.

<a id="dd-059"></a>
## DD-059: corelib v0.3.x / Spec 0.2.7–0.2.9

**Entscheidung:**
1. `PUT kalender` sendet `X-Scraper-Id` wieder (Spec 0.2.7 verlangt ihn).
2. API-Tests patchen den generierten Client mit `autospec=True`. Ein geändertes Pflichtargument
   lässt so schon den Unit-Test scheitern; ohne hätte genau dieser `TypeError` erst im Betrieb
   gegriffen.
3. `parl-verfgstop` heißt jetzt `postparl-vgstp` (DD-057).
4. **v0.3.1** (GitHub #129): nur Pin-Wechsel. Spec und Felder bleiben gleich (der Client ist
   neu generiert: `StrEnum`, `fromisoformat`; BW nutzt `.value`, ohne Wirkung auf Payloads und
   Cache-Keys), litellm wird lazy importiert. Den neuen corelib-Cache nutzt BW **nicht**: Er kennt nur ein
   Dateisystem-Backend, Cloud Run hat kein bleibendes Dateisystem, und Redis ist Pflicht
   (DD-073). Ein eigenes Redis-Backend über `cache.backends.Backend` wäre möglich, aber
   corelib-Keys verbieten `:` — alle BW-Keys (`vg2:`, `sz:`, `llm-semantics:` …) müssten
   umbenannt werden, der Cache samt bezahlter LLM-Ergebnisse liefe leer.
5. **v0.3.2** / Spec 0.2.8 (GitHub #161; Prod kennt die neuen Werte seit spätestens 2026-11-04):
   - `Vorgang.zusammenfassung` kommt aus dem PARLIS-Kurzreferat (DD-080).
   - `Station.meta_tiebreaker` bleibt ungenutzt: Das Backend schreibt ihn nur beim Anlegen
     einer Station, nicht beim Merge. Der Track-Check sortiert schon nach
     `(zp_start, meta_tiebreaker)`, bestehende Stationen behielten aber `NULL`: Ohne Stunden-Bump
     (`enforce_total_ordering`) lägen sie gleichauf, die Reihenfolge käme aus der Datenbank, und
     der Track-Check des gemergten Vorgangs kann HTTP 400 geben. Zudem änderte das neue `zp_start` die
     Stations-`api_id` (DD-028). Erst mit Backend-Fix und Migration.
   - `gg-land-parl-haushalt` erst mit einem BW-Track dafür (DD-077).
   - `parl-anhoerung`, `beschlussprotokoll`, `inhaltsprotokoll` gibt es in BW nicht (DD-057).
6. **v0.3.3** / Spec 0.2.9 (GitHub #202):
   - Die jetzt optionalen Zeitpunkte (`zp_start`, `termin`, `zp_modifiziert`, `zp_referenz`)
     setzt BW weiterhin immer.
   - LLM-Tokens gehen in die Metriken (DD-082).
   - `berichtigung` bleibt ungemappt (DD-057); `gg-land-parl-staatsvertrag` und
     `gg-land-parl-nachtragshaushalt` sind offen (GitHub #203).

**Konsequenz:** corelibs `html_to_text` behandelt einen Text als HTML, sobald ein einzelnes
Block-Tag vorkommt, und verliert dann alle Absätze — die Garbled-Filter (DD-015) arbeiten
absatzweise. In BW-Drucksachen derzeit nicht beobachtet; tritt es auf, gehört die Korrektur
nach corelib.

<a id="dd-078"></a>
## DD-078: Ein Config-Loader, ein Wert gilt wie geschrieben

**Kontext:** Zwei Loader lasen die TOML-Datei mit verschiedenen Regeln: `BawueConfig`
überging `false`, `0` und `""`, `load_toml_section` behielt sie und las die Datei bei jedem
Aufruf neu. `--dump-config` zeigte die Abschnitte `[bawue]` usw. nicht (GitHub Issue #102).

**Entscheidung:** `BawueConfig.load()` liest die Datei einmal; `config.section(name)` liefert
einen Abschnitt mit seinen env-Overrides (`_ENV_OVERRIDES`, Namen unverändert). Ein Wert aus
der Datei gilt wie geschrieben, auch `false`/`0` — nötig für Notschalter wie
`beteiligungsfristen = false`. Eine leere Pflichtangabe (`ltzf-api-key = ""`) gilt weiter als
fehlend. `--dump-config` zeigt auch die Abschnitte, Secrets als `***`.

**Konsequenz:** `max-concurrency = 0` o. ä. in der Datei wird jetzt übernommen statt durch den
Default ersetzt. Keine der Repo-Configs enthält einen solchen Wert.

<a id="dd-082"></a>
## DD-082: Metriken und Logs nach Grafana, Mattermost nur bei Fehlern

**Kontext:** Der Run-Report in Mattermost war die einzige Auswertung: knapp, ohne Verlauf, und
ein Absturz, OOM oder Timeout schickte gar nichts (GitHub Issue #188).

**Entscheidung:** Nach jedem Lauf pusht der Scraper seine Zahlen über den gemeinsamen
`pazufa-metrics-client` an das Pushgateway, Namen und Labels wie auf der Wiki-Seite
*Observability*. Heartbeat ist `last_success_timestamp_seconds`: nur ein Lauf ohne gescheiterten
Scraper setzt ihn neu, sonst kommt der letzte Wert aus Redis (`metrics-last-success`), weil jeder
Push die ganze Gruppe ersetzt. `state="new"` enthält
erneut versuchte Einträge (ein Cache-Miss unterscheidet sie nicht); Ablehnungen mit HTTP-Status
zählen als `submitted{result="rejected", statuscode}`, Fehler ohne Antwort als
`failed{reason="exception"}`, Redis-Fehler als `failed{reason="cache_error"}`, PDFs mit 404 nach
vier Wochen als `failed{entity="document", reason="not_found"}`. LLM-Tokens (corelib ≥ 0.3.3) gehen als
`llm_tokens{type="prompt"|"completion"|"reasoning"}` aller Scraper in eine Summe je Lauf;
`reasoning` ist Teil von `completion`, die Gesamtsumme also nur `prompt` + `completion`.
`llm_cost_usd{model}` rechnet sie mit litellms mitgelieferter Preistabelle um, damit kein Preis
im Dashboard steht; gecachte Prompt-Tokens zählen zum vollen Preis (leicht zu hoch). Ohne Preis
für das Modell fehlt die Reihe, statt 0 zu melden. Logs gehen gebündelt aus einem eigenen Thread an Loki,
das Level als Label; nach einem gescheiterten Push stoppt der Versand für den Lauf. Beides nur
mit `OBSERVABILITY_PASSWORD`, sonst werden die Metriken nur geloggt. Kein OpenTelemetry: das
Pushgateway nimmt kein OTLP.

Mattermost bekommt nur noch Fehler: gescheiterte Einträge, PARLIS-Fehler, zu löschende
Portal-Vorgänge (DD-079), abgestürzte Scraper, Abbrüche. Die Zusammenfassung steht im Log.

**Konsequenz:** Auch ein gescheiterter Lauf liefert seine Zahlen; das Alter des Heartbeats
alarmiert in Grafana, der Fehler in Mattermost. Wochenbericht und „Fehlende Dokumente“ entfallen.
Noch gepufferte Logs gehen verloren,
wenn Cloud Run den Job beendet; Cloud Logging hat sie.

<a id="dd-084"></a>
## DD-084: PDF-Parser laufen im Prozess mit den Secrets — Risiko akzeptiert

**Kontext:** kreuzberg (pdfium), Tesseract und poppler lesen PDFs aus dem Internet im selben
Prozess, dessen Umgebung `LTZF_API_KEY`, `LLM_PROVIDER_KEY`, `REDIS_URL`, `MATTERMOST_HOOK`
und `OBSERVABILITY_PASSWORD` enthält. Ein Speicherfehler in einem Parser gäbe Codeausführung
neben allen Secrets (Threat Model T-03, Issue #185).

**Entscheidung:** Keine Prozesstrennung; das Risiko ist akzeptiert. Ein Kindprozess ohne
Umgebung schützt nicht: dieselbe UID liest `/proc/<ppid>/environ`. Echte Trennung braucht eine
eigene UID für den Parser (Container startet als root, Secrets als Dateien nur für `app`) oder
einen eigenen Job — ein Umbau von Dockerfile, Entrypoint und Deploy, der für die Lage
(Exploit für pdfium/Tesseract nötig, dazu ein PDF in einer Landtagsquelle) zu groß ist.

**Konsequenz:** Grenzen bleiben Nicht-root-User, Cloud-Run-Sandbox und `MAX_PDF_BYTES`
(Issue #182). kreuzberg hält Dependabot (pip) aktuell, Trivy prüft den Lockfile; Tesseract
und poppler kommen aus Debian und werden nur mit einem neuen Image aktualisiert. Neu bewerten,
wenn ein Parser-Exploit bekannt wird oder die Secrets ohnehin als Dateien gemountet werden.
