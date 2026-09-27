[← Index](../design_decisions.md)

# DD-017: Konfigurierbare Filterung von `sonstig`-Stationen

**Datum:** 06.04.2026

**Kontext:** PARLIS liefert Fundstellen wie „Mitteilung" oder „Dokument", die keinem
spezifischen Stationstyp zugeordnet werden können (s. DD-002). Der `enum_mapper`
klassifiziert diese als `Stationstyp.SONSTIG`. Das Backend (v0.2.7) löst bei
`sonstig`-Stationen einen Panic aus (`validate.rs:310`, `.get()` statt `[]`).
28 Vorgänge im Dev-Lauf waren betroffen.

Das Backend wird diesen Bug voraussichtlich in einer kommenden Version beheben und
`sonstig`-Stationen akzeptieren.

**Entscheidung:** `sonstig`-Stationen werden in `_collect_stationen()` herausgefiltert,
gesteuert durch den Konfigurationsparameter `filter-sonstig-stations` (Default: `true`).
Sobald das Backend `sonstig` akzeptiert, kann der Filter durch Setzen auf `false`
deaktiviert werden.

Der Filter greift **nach** den Sonderbehandlungen für Stellungnahmen,
Änderungsanträge und Entschließungsanträge (DD-001, DD-005), da diese ebenfalls
als `sonstig` klassifiziert werden können, aber als Dokumente an vorhergehende
Stationen angehängt werden sollen.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methode `_collect_stationen()`.
Konfiguration über `[bawue]` → `filter-sonstig-stations` in `config.toml`.

---

**Aktualisierung (27.09.2026, GitHub Issue #5):** Gefilterte `sonstig`-Stationen wurden
nur auf DEBUG geloggt, das Verwerfen blieb damit unsichtbar (z. B. Mitteilung der
Präsidentin, Drs. 17/10130, 22 S., V-244180). Jede verworfene Fundstelle läuft jetzt über
`_drop()`: eine Log-Zeile `Dropping Fundstelle '<raw>' in <vorgnr>: <Grund>` und ein
Zähler je Vorgang, den die Run-Summary als „Vorgänge with dropped Fundstellen" ausgibt
(max. 20 Einträge, auch nach Mattermost).

| Grund | Level |
|---|---|
| `<Typ> → sonstig` (dieser Filter, z. B. Mitteilung — DD-002) | INFO |
| `no parseable date` | ERROR (ersetzt das frühere „Skipping station"-ERROR; die Ursache loggt `_parse_fundstelle_date`) |
| `Stellungnahme without preceding station` (DD-005) | WARNING |
| `Änderungsantrag/Entschließungsantrag without parl-vollvlsgn` (DD-001) | WARNING |

Gezählt wird nur, was in diesem Lauf gebaut wurde — Cache-Treffer (DD-052) werden nicht
neu gebaut. Manuell konstruierte Scraper (dry_run, Tests) loggen nur
(`_dropped_fundstellen = None`, Klassenattribut-Default wie DD-041).

**Tests:** `tests/unit/test_issue5_dropped_documents.py`,
`TestFilterSonstigStations::test_sonstig_filtering_logs_info_message`.
