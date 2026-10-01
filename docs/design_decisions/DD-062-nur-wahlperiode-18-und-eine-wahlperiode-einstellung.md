[← Index](../design_decisions.md)

# DD-062: Nur Wahlperiode 18, eine einzige Wahlperioden-Einstellung (GitHub Issues #63, #6)

**Datum:** 30.09.2026

**Kontext:** Der Landtag ist seit 2026-05-01 in WP 18. Staging und Prod scrapen WP 18
bereits seit 2026-09-13, weil die GitHub-Variablen `WAHLPERIODE=18` und
`WAHLPERIODE_START_DATE=2026-05-01` gesetzt sind. Der Image-Default (`config.sample.toml`),
die Code-Defaults und die Doku nannten aber noch WP 17. Die Wahlperiode war an drei Stellen
einzustellen: `[bawue] wahlperiode`, `[bawue] wahlperiode-start-date` und
`[beteiligung] wahlperiode` (env `BETEILIGUNG_WAHLPERIODE`). Diese Werte gehören aber
zusammen.

**Entscheidung:**

1. **Prod scrapt nur WP 18.** WP 17 ist abgeschlossen und liegt im Backend vor. Ein
   WP-17-Nachlauf bleibt als lokaler, manueller Lauf mit `wahlperiode = 17` in einer lokalen
   Konfiguration möglich (z. B. dem gitignorierten `config.wp17.toml`).
2. **Eine Einstellung:** `[bawue] wahlperiode` (env `WAHLPERIODE`) gilt für alle drei
   Scraper. Default ist `CURRENT_WAHLPERIODE`, die neueste bekannte WP. Die CLI-Werkzeuge
   `dry_run` und `verify_fulltext` nehmen `--wahlperiode` mit demselben Default.
3. **Beginn abgeleitet:** `bawue/wahlperiode.py` führt eine Tabelle WP → Tag der
   Konstituierung (17: 2021-04-26, 18: 2026-05-01). `[bawue] wahlperiode-start-date` (env
   `WAHLPERIODE_START_DATE`) grenzt den Suchbereich nur noch ein. Ein Datum vor dem Beginn
   der WP oder in der Zukunft bricht ab, ebenso eine unbekannte WP ohne Startdatum. Beides
   wird also nicht still falsch gesucht.
4. **`[beteiligung] wahlperiode` entfällt,** ebenso `BETEILIGUNG_WAHLPERIODE`. Ein
   verbliebener TOML-Schlüssel wird ignoriert und löst eine Warnung aus.

**Rollout:** Die GitHub-Variablen `BETEILIGUNG_WAHLPERIODE` und `WAHLPERIODE_START_DATE`
in den Environments `staging` und `prod` löschen. Die erste wird nicht mehr gelesen. Die
zweite würde sonst den abgeleiteten Beginn dauerhaft übersteuern.

**Für WP 19:** einen Eintrag in `WAHLPERIODE_START` ergänzen, dann `wahlperiode` bzw.
`WAHLPERIODE` umstellen. Die Warnung von `check_for_newer_wahlperiode` nennt beide Schritte.
