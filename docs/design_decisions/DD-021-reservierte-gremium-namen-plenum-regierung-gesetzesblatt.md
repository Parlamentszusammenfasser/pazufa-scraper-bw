[← Index](../design_decisions.md)

# DD-021: Reservierte Gremium-Namen (`plenum`, `regierung`, `gesetzesblatt`)

**Datum:** 22.04.2026 | **Aktualisiert:** 22.04.2026 (Gap G1.4 + G4 aufgelöst)

**Kontext:** Drei Quellen definieren reservierte `Gremium.name`-Werte:

1. **OpenAPI-Spezifikation** (PaZuFa-Spec v0.2.3, Feld `Gremium.name`):
   > "Name des betreffenden Gremiums. `'plenum'`, `'regierung'`, `'volk'` sind
   > reservierte namen"
2. **Community-DoD-Wiki**: `regierung`, `plenum` (als Default, "wenn etwas
   'irgendwie passiert'"), **und `gesetzesblatt`** für die Veröffentlichung im
   Gesetzblatt.
3. **BY-Referenz-Scraper** (pazufa-collectors `bylt_scraper.py`):
   emittiert `gesetzesblatt` literal für `postparl-gsblt`-Stationen (Zeile 440)
   und `plenum` für alle anderen nicht-Ausschuss-Stationen — inkl. synthetisch
   erzeugte. BY hat **keinen** generischen Fallback wie `"Landtag"`.

**Diskrepanz Spec vs. Wiki**: Die Spec listet `volk`, aber nicht `gesetzesblatt`;
das Wiki listet `gesetzesblatt`, aber nicht `volk`. Die Spec-Beschreibung
validiert das Feld nicht schema-seitig — jeder String ist erlaubt. Der BY-Scraper
nutzt `gesetzesblatt` produktiv ohne Backend-Fehler. Damit wird die
Spec-Beschreibung als **unvollständig** betrachtet, nicht als exklusiv — das
Wiki + BY-Convention sind maßgeblich.

**Backend-Verhalten** (`pazufa-backend/src/db/insert.rs:545-609`,
`migrations/20250302145212_vorgang_setup.sql:9`):

- `gremium` hat `UNIQUE (parl, name, wp)` — jeder unterschiedliche Name erzeugt
  eine separate Zeile.
- Beim Insert läuft ein pg_trgm `SIMILARITY(name, $1) > 0.66`-Check, der bei
  Near-Misses `notify_new_enum_entry(...)` triggert — eine eingebaute Canary
  für schleichende Namens-Drift.
- `SIMILARITY('Landtag', 'plenum') ≈ 0` → kein Alert. Ein Wechsel
  `Landtag → plenum` ist für das Backend unsichtbar (erzeugt eine neue
  Gremium-Zeile; die alte verwaist, ohne Datenverlust).

**Entscheidung:** Alle Stationen bekommen einen kanonischen Namen — kein
deutschsprachiger Klartext-Fallback mehr. Das Routing erfolgt im Scraper
typ-gewahr:

| Kontext / Station-Typ                                  | Gremium-Name       |
|--------------------------------------------------------|--------------------|
| Fundstelle mit Ausschuss-Angabe                        | Ausschuss-Name     |
| `postparl-gsblt` (Gesetz, Bekanntmachung, Gesetzblatt) | `gesetzesblatt`    |
| PARLIS: `preparl-*` (Regierungsbeschluss etc.), Issue #10 | `regierung`     |
| Beteiligungsportal-Station (`preparl-regent`)          | `regierung`        |
| Alle übrigen (parl-*, synthetische)                    | `plenum` (Default) |
| ICS-Plenarsitzung                                      | `plenum`           |

**Update (Issue #10):** `preparl-regbsl` (Regierungsbeschluss/Kabinettsbeschluss) fiel
ursprünglich in den `plenum`-Default, obwohl es eine Kabinetts- und keine
Landtags-Handlung ist — inkonsistent mit `preparl-regent`, das über den
Beteiligungsportal-Scraper bereits `regierung` bekommt. `_determine_gremium` routet
jetzt jeden `Stationstyp`, dessen Wert mit `preparl-` beginnt, auf `regierung` (Priorität
nach Ausschuss, vor dem `plenum`-Fallback) — nicht nur `preparl-regbsl` als
Spezialfall, damit ein künftig produzierter `preparl-eckpup`/`preparl-vbegde` nicht
denselben Bug erneut auslöst.

**Bewusst nicht geändert:**

- **Initiator-Strings** (`Autor.organisation = "Landesregierung"` etc.). Die
  reservierten Namen gelten ausschließlich für `Gremium.name`, nicht für
  `Autor.organisation`. Der Autor-String wird unverändert aus PARLIS
  übernommen (Roadmap #10 G2: kanonische Normalisierung der Autor-Strings
  steht noch aus).
- **`volk`** ist im `ReservedGremium`-Enum definiert, aber nicht eingesetzt —
  BW kennt derzeit keine `postparl-vesja`/`postparl-vesne`-Stationen im aktiven
  Vorgangstyp-Filter. Der Wert bleibt für zukünftige Volksantrag-Pfade
  verfügbar.

**Backend-Koordination für Daten vor dem Roll-out:** Durch den Wechsel
`"Landtag" → "plenum"` verwaist die bestehende `(BW, Landtag, 17)`-Gremium-Zeile.
Bereinigung per einmaliger SQL-Operation nach vollem Re-Scrape-Zyklus:

```sql
UPDATE station SET gr_id =
  (SELECT id FROM gremium WHERE parl=... AND name='plenum' AND wp=17)
WHERE gr_id =
  (SELECT id FROM gremium WHERE parl=... AND name='Landtag' AND wp=17);
DELETE FROM gremium WHERE parl=... AND name='Landtag' AND wp=17;
```

Auf Dev/Staging durch DB-Reset trivial. Produktions-Koordination mit
Backend-Team erforderlich.

**Implementierung:** `bawue/types.py` definiert die `StrEnum` `ReservedGremium`
mit den vier Werten (`PLENUM`, `REGIERUNG`, `VOLK`, `GESETZESBLATT`). Da
`StrEnum`-Member echte `str`-Instanzen sind, passieren sie die
`StrictStr`-Validierung auf `Gremium.name` ohne Konvertierung.

Verwendung:

- `bawue_vorgaenge_scraper.py::_determine_gremium(fund, station_typ)` — typ-
  abhängige Auswahl: Ausschuss-Name, `gesetzesblatt` bei `postparl-gsblt`,
  `regierung` bei jedem `preparl-*` (Issue #10), sonst `plenum`.
- `bawue_vorgaenge_scraper.py::_ensure_ablehnung_station` und
  `_ensure_initiativ_after_regbsl` — synthetische Stationen nutzen
  `ReservedGremium.PLENUM` (vorher hartcodiert `"Landtag"`).
- `bawue_beteiligung_scraper.py` (Station-Erstellung) — `ReservedGremium.REGIERUNG`.
- `ics_parser.py::extract_gremium_name` — `ReservedGremium.PLENUM` für
  Plenarsitzungen.

`tests/unit/test_enum_mapper.py::TestReservedGremiumNames` lockt die literalen
Werte gegen Spec + Wiki + BY-Convention.
`tests/unit/test_bawue_scraper.py::TestVorgangBuild::test_default_gremium_is_plenum`
und `test_gsblt_station_uses_gesetzesblatt_gremium` verifizieren das neue
Routing. `test_preparl_regbsl_station_uses_regierung_gremium` (Issue #10) sowie
`TestDetermineGremiumRouting` (direkte Coverage aller `preparl-*`-Werte, der
Ausschuss-Priorität und eines Regressionsschutzes für die übrigen `Stationstyp`-
Werte) verifizieren das `regierung`-Routing.
