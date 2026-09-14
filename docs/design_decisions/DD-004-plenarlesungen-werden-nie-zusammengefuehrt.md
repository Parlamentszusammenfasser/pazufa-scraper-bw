[← Index](../design_decisions.md)

# DD-004: ~~Plenarlesungen werden nie zusammengeführt~~

**Datum:** 27.03.2026 | **Aktualisiert:** 23.04.2026

> **Status:** Teilweise abgelöst durch [DD-024](DD-024-plenarlesungen-gleicher-runde-werden-konsolidiert.md)
> (Runden-Konsolidierung für `parl-vollvlsgn`). Der ursprüngliche Leitsatz — unterschiedliche
> Lesungen (Erste/Zweite/Dritte) bleiben getrennte Stationen — bleibt gültig.

**Kontext:** PARLIS liefert für einen Vorgang häufig mehrere Fundstellen mit dem
gleichen Stationstyp und Gremium hintereinander — z. B. zwei Ausschuss-Fundstellen
für denselben Ausschuss. Um Duplikate zu vermeiden, werden aufeinanderfolgende
Stationen gleichen Typs und Gremiums zu einer Station zusammengeführt (Merge).

**Entscheidung (ursprünglich):** Stationen vom Typ `parl-vollvlsgn` (Plenarlesungen) werden **nie**
zusammengeführt. Jede Lesung (Erste, Zweite, Dritte Beratung) bleibt eine eigene
Station — auch wenn sie direkt aufeinander folgen und dasselbe Gremium `plenum`
(reservierter Name, s. DD-021) haben. Eine Zweite Beratung ist schlicht eine weitere Station vom Typ
`parl-vollvlsgn`, kein gesonderter Stationstyp.

**Aktualisierung (DD-024):** Fundstellen derselben Runde (identischer roh-`station_typ`-Text, z. B.
beide "Zweite Beratung" für Staatshaushaltsgesetz-Einzelpläne) werden nun **zu einer Station
konsolidiert**. Unterschiedliche Rundentexte ("Erste" vs. "Zweite" vs. "Überweisung") bleiben wie
ursprünglich getrennt. Siehe DD-024 für Kontext und Begründung.

Für Ausschussberatungen (`parl-ausschber`) wird zusätzlich rückwärts über die
Plenarstationsgrenze hinaus nach einem passenden Ausschuss gesucht, aber nicht
vorwärts über eine Plenarstation hinweg.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methoden `_try_merge_station()`
und `_find_matching_ausschuss()`.
