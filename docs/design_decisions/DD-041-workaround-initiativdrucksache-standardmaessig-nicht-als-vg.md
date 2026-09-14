[← Index](../design_decisions.md)

# DD-041: WORKAROUND — Initiativdrucksache standardmäßig nicht als `vg_ident` senden (togglebar)

**Datum:** 13.07.2026

**Kontext:** Ein voller WP17-Backfill gegen Backend 0.2.14 brach an 31 Vorgängen ab
(27× HTTP 500 `duplicate key … rel_station_dokument_pkey`, 4× HTTP 400 „Track
validation Failed"). Root-Cause-Analyse (inkl. Backend-Quellcode, https://codeberg.org/PaZuFa/pazufa-backend/issues/150)
zeigte: die Ursache liegt **im Backend**, nicht im Scraper.

`pazufa-backend-lib/src/db/merge/candidates.rs::vorgang_merge_candidates` hält zwei
Vorgänge für **denselben** Vorgang, wenn gilt:

```
(wahlperiode, typ) gleich
UND ( api_id exakt gleich  ODER  (irgendein vg_ident gleich UND ein Bundesland gleich) )
```

Der `vg_ident`-Zweig behandelt **jeden einzelnen** übereinstimmenden `vg_ident` als
Identitätsbeweis — unabhängig davon, ob der Identifier-Typ überhaupt 1:1 mit einem
Vorgang ist. `initdrucks` (die Drucksache des *initiierenden* Dokuments, seit Issue #26
als Cross-Referenz mitgegeben) ist aber **n:1**: Jeder Haushalts-Einzelplan zitiert
dieselbe Staatshaushaltsgesetz-Drucksache (z. B. 17/8000). Damit matchen alle ~18
Einzelpläne einer Haushaltssaison gegenseitig, das Backend „merged" sie und

1. überschreibt via `execute_merge_vorgang` (`execute.rs:283`) **bedingungslos** Titel/
   Kurztitel/`verfassungsaendernd`/Typ des zuerst angelegten Vorgangs (stille Korruption,
   falls die Stationen nicht kollidieren), und
2. matcht danach fremde Stationen über geteilte Dokument-Hashes → Insert kollidiert mit
   `rel_station_dokument (stat_id, dok_id)` → HTTP 500.

Wichtig: Ein **korrekter, distinkter `api_id`** (DD-028/DD-034) verhindert das **nicht** —
der `vg_ident`-Zweig ist ein `OR` und feuert unabhängig vom `api_id`-Match. Der einzige
scraper-seitige Hebel ist deshalb, **welche `vg_ident` wir senden**.

**Entscheidung:** Die Emission der Initiativdrucksache als `vg_ident`
(`typ="initdrucks"`) wird hinter einen Konfigurationsschalter gelegt:
`[bawue] emit-initdrucks-ident` (Default `false`). Standardmäßig hängt nur der
1:1-Identifier `vorgnr` am Vorgang; die Cross-Referenz aus Issue #26 bleibt
deaktiviert, bis das Backend nur noch über 1:1-Identifier matcht. Der Schalter
ist als **Klassenattribut-Default** (`_emit_initdrucks_ident = False`) umgesetzt,
damit Tests und andere `object.__new__`-Konstruktionspfade den sicheren Wert erben,
ohne ihn explizit setzen zu müssen (analog zum Muster von DD-017).

- Verhindert den Fehler robust — auch **über Läufe hinweg** (EP12 in Lauf 1, EP11 in
  Lauf 2 würden sonst weiterhin kollidieren; eine reine Intra-Lauf-Deduplizierung
  reichte nicht).
- Die Information geht nicht verloren: Die Initiativdrucksache steht weiterhin als
  `drucksnr` am Dokument der initiierenden Station.
- Re-Upload-Idempotenz bleibt erhalten: `vorgnr` + stabile Stations-`api_id`
  (DD-028/DD-034) genügen für korrekte Wiedererkennung (empirisch verifiziert gegen
  0.2.14: EP11/EP12 laden 201/201, Re-Upload ohne Duplikate).

**Empirische Verifikation (Backend 0.2.14, frische DB):**

| Payloads                           | Ergebnis                                           |
|------------------------------------|----------------------------------------------------|
| ep12 + ep11 **mit** `initdrucks`   | 201, **500** (`rel_station_dokument_pkey`)         |
| ep12 + ep11 **ohne** `initdrucks`  | 201, 201 — zwei distinkte Vorgänge, Titel erhalten |
| Re-Upload beider ohne `initdrucks` | 201, 201 — keine Duplikat-Zeilen                   |

**Reversibilität:** Sobald das Backend nur noch über 1:1-Identifier matcht (oder
`initdrucks` aus der Merge-Kandidatensuche ausschließt — Vorschläge siehe
https://codeberg.org/PaZuFa/pazufa-backend/issues/150), genügt
`emit-initdrucks-ident = true` in der `[bawue]`-Konfiguration, um die Cross-Referenz
aus Issue #26 wieder zu aktivieren — kein Code-Change nötig. `initiativ_drucksnr`
aus den Fundstellen (DD-033, `_initiativ_drucksnr_from_fundstellen`) ist von alldem
unberührt und bleibt für die Abschnitts-Extraktion erhalten.

**Implementierung:** `bawue_vorgaenge_scraper.py` — Klassenattribut-Default
`_emit_initdrucks_ident = False`, aus `[bawue] emit-initdrucks-ident` in `__init__`
gelesen; `_build_vorgang()` hängt den `initdrucks`-`VgIdent` nur bei aktivem Schalter
an (`_initiativ_drucksnr(stationen)` liefert die Nummer). Dokumentiert in
`config.sample.toml`.

**Tests:** `tests/unit/test_bawue_scraper.py::TestBuildVorgang` —
`test_ids_omit_initiativdrucksache_by_default` (Default: Initiative mit Drucksache
17/10266 → nur `vorgnr`, kein `initdrucks`), `test_ids_include_initiativdrucksache_when_enabled`
(Schalter an → `initdrucks` = 17/10266 vorhanden), `test_ids_omit_initiativdrucksache_when_absent_though_enabled`
(Schalter an, aber keine Drucksache → kein `initdrucks`). Backend-seitige Reproduktion
& Evidenz: https://codeberg.org/PaZuFa/pazufa-backend/issues/150.
