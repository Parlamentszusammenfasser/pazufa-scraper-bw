[← Index](../design_decisions.md)

# DD-016: Track-Validierung — BW verwendet den BY-Track unverändert

**Datum:** 05.04.2026 | **Aktualisiert:** 06.04.2026

**Kontext:** Mit Backend v0.2.7 werden Vorgänge gegen Track-Definitionen (DFA/Regex)
validiert. Die Erstanalyse schlug einen eigenen BW-Track vor, der PARLIS-Abweichungen
durch optionale Elemente (`V?J`, `V?N`) kompensiert. Nach Review durch den
Backend-Entwickler (Crystalkey) und Analyse der Geschäftsordnung des Landtags BW
(17. WP, §42–§49) zeigte sich: Alle Abweichungen lassen sich scraper-seitig lösen.

**Entscheidung:** BW verwendet den BY-Track unverändert:

```
gg-land-parl = "((E*R+)?S)?I((VA*(Z|VJGK|VN|VA*(Z|VJGK|VN)))|Z)"
```

**Begründung:**

1. **Tracks bilden parlamentarisches Recht ab**, nicht was der Scraper liefern kann.
   Abweichungen in PARLIS-Daten sind Scraper-Bugs, kein Grund den Track abzuschwächen.
2. **R→S Umklassifizierung** (DD-003): PARLIS „Gesetzentwurf Landesregierung" wird
   als `preparl-regbsl` (S) statt `preparl-regent` (R) klassifiziert. Damit matcht
   der BY-Präfix `((E*R+)?S)?` korrekt.
3. **GO §42 schreibt mindestens zwei Lesungen vor** — `VJ` statt `V?J` ist korrekt.
   126/128 Annahme-Vorgänge bestätigen zwei explizite Lesungen vor Annahme.
4. **GO §43 Abs. 4 verbietet jede Abstimmung in der 1. Lesung** — `IVN` (Ablehnung
   nach nur einer Lesung) ist rechtlich unmöglich. Alle 31 Ablehnungen folgen `IVAVN`.
5. **Prefix-Matching** akzeptiert unvollständige Vorgänge ohnehin — `IVAVJG` ist ein
   gültiger Präfix von `IVAVJGK`, auch wenn K (Inkrafttreten) nicht gescrapt wurde.
6. **Y = Volksentscheid** (`postparl-vesja`), nicht Ausfertigung. Irrelevant für den
   `gg-land-parl`-Track. Volksanträge benötigen ggf. einen eigenen `gg-land-volk`-Track.

**Validierte Sequenzen** (Dev-Lauf 171 Vorgänge, WP 17):

| Sequenz   | Anzahl | Beschreibung                           |
|-----------|--------|----------------------------------------|
| `SIVAVJG` | 112    | Regierungsentwurf (nach R→S) + Annahme |
| `IVAVJG`  | 14     | Fraktionsentwurf + Annahme             |
| `IVAVN`   | 31     | Ablehnung nach Ausschuss und 2. Lesung |
| `S`, `SI` | 3      | Unvollständig — gültige Präfixe        |

Quelle: [Issue #26](https://codeberg.org/PaZuFa/pazufa-backend/issues/26),
Crystalkey-Review 05.04.2026.
