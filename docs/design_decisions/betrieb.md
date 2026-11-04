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
## DD-059: corelib v0.3.x / Spec 0.2.7–0.2.8

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
5. **v0.3.2** / Spec 0.2.8: nur Pin-Wechsel; ungesetzte neue Felder fehlen im Payload. Prod
   läuft noch auf Spec 0.2.6 (Stand 2026-11-01) und kennt keinen neuen Wert, daher bleibt
   alles Neue ungenutzt: `gg-land-parl-haushalt` erst mit #60 und Track-Regex;
   `Station.meta_tiebreaker` statt Stunden-Bumps (`enforce_total_ordering`) ändert `zp_start`
   und damit die Stations-`api_id` (DD-028), braucht also eine Migration; `Vorgang.zusammenfassung`
   wäre ein zusätzlicher LLM-Call; `if_none_match` lohnt für den wöchentlichen Abruf (DD-072) nicht.

**Konsequenz:** corelibs `html_to_text` behandelt einen Text als HTML, sobald ein einzelnes
Block-Tag vorkommt, und verliert dann alle Absätze — die Garbled-Filter (DD-015) arbeiten
absatzweise. In BW-Drucksachen derzeit nicht beobachtet; tritt es auf, gehört die Korrektur
nach corelib.

<a id="dd-072"></a>
## DD-072: Wochenbericht aus dem Backend

**Kontext:** Leser wollen wissen, welche Gesetzesvorhaben in der Woche neu sind oder
weitergekommen sind. Der Scraper hat keine Historie, und der „geändert"-Zeitstempel im
Backend setzt jeder Neuaufbau (Issue #82).

**Entscheidung:** Im ersten Lauf jeder ISO-Woche holt der Scraper alle BW-Vorgänge der
Wahlperiode und merkt sich je `gg-*`-Vorgang den Typ der letzten **begonnenen** Station in
Redis (`wochenbericht`). Der Vergleich mit der Vorwoche ergibt 🆕 neu und ➡️ andere Station,
als Abschnitt im Run-Report (ab 20 Zeilen gekürzt). Fail-soft: bei Fehlern bleibt der alte
Stand.

Mit dem Bericht kommt „Fehlende Dokumente“: PDFs, die mehr als 4 Wochen nach dem
Fundstellen-Datum noch HTTP 404 liefern (Plenarprotokolle erscheinen nach 1–2 Wochen), eine
Zeile je Datei mit Vorgängen und Links, als Markdown statt Codeblock (Issue #128). 5xx und
Timeouts zählen nicht. Die Liste entsteht im Lauf selbst: ungecachte Vorgänge werden jedes Mal
neu gebaut (DD-067).

**Konsequenz:** Der erste Lauf und ein geleertes Redis liefern keinen Bericht. Eine zweite
Station gleichen Typs gilt nicht als Änderung. Ohne LLM-Anreicherung lädt der Scraper keine
PDFs, die Liste bleibt dann leer.

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
