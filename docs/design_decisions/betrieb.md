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
## DD-059: corelib v0.3.x / Spec 0.2.7

**Entscheidung:**
1. `PUT kalender` sendet `X-Scraper-Id` wieder (Spec 0.2.7 verlangt ihn).
2. API-Tests patchen den generierten Client mit `autospec=True`. Ein geändertes Pflichtargument
   lässt so schon den Unit-Test scheitern; ohne hätte genau dieser `TypeError` erst im Betrieb
   gegriffen.
3. `parl-verfgstop` heißt jetzt `postparl-vgstp` (DD-057).
4. **v0.3.1** (GitHub #129): nur Pin-Wechsel. Spec und API-Modelle bleiben gleich, litellm wird
   lazy importiert. Den neuen corelib-Cache nutzt BW **nicht**: Er kennt nur ein
   Dateisystem-Backend, Cloud Run hat kein bleibendes Dateisystem, und Redis ist Pflicht
   (DD-073). Ein eigenes Redis-Backend über `cache.backends.Backend` wäre möglich, aber
   corelib-Keys verbieten `:` — alle BW-Keys (`vg2:`, `sz:`, `llm-semantics:` …) müssten
   umbenannt werden, der Cache samt bezahlter LLM-Ergebnisse liefe leer.

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

**Konsequenz:** Der erste Lauf und ein geleertes Redis liefern keinen Bericht. Eine zweite
Station gleichen Typs gilt nicht als Änderung.
