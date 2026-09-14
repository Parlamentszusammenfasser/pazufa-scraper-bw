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
