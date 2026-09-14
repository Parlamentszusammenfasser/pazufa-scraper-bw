[← Index](../design_decisions.md)

# DD-010: Synthetische Ablehnung aus „Aktueller Stand"

**Datum:** 27.03.2026

**Kontext:** PARLIS behandelt Annahme und Ablehnung asymmetrisch. Angenommene
Vorgänge erhalten eine explizite Fundstelle (z. B. „Zustimmung", „Annahme",
„Gesetzesbeschluss"), die vom Scraper als `parl-akzeptanz`-Station erfasst wird.
Abgelehnte Vorgänge erhalten **keine** solche Fundstelle — der Vorgang endet
einfach nach der letzten Plenarlesung. Das Abstimmungsergebnis ist nur im
Plenarprotokoll-PDF und im Metadatenfeld „Aktueller Stand" (Wert: „Abgelehnt")
der PARLIS-Suchergebnisse dokumentiert.

**Evidenz:** In einem ersten Scrape-Lauf (236 Vorgänge, WP 17) wurden
462 `parl-akzeptanz`-Stationen, aber **null** `parl-ablehnung`-Stationen
extrahiert. Ein späterer vollständiger Staging-Lauf (235 Vorgänge, 13.04.2026)
zeigte jedoch, dass PARLIS bei einigen Vorgängen durchaus „Ablehnung"-Fundstellen
liefert — teils sogar doppelt. Die synthetische Station wird daher nur benötigt,
wenn keine Fundstellen-basierte `parl-ablehnung` vorhanden ist.

**Entscheidung:** Wenn das Metadatenfeld „Aktueller Stand" den Wert „Abgelehnt"
enthält und noch keine `parl-ablehnung`-Station existiert, wird eine synthetische
Station angehängt. Das Datum wird von der letzten vorhandenen Station übernommen
(i.d.R. die finale Plenarlesung, in der die Abstimmung stattfand). Falls keine
Stationen vorhanden sind, wird keine synthetische Station erzeugt.

Das Feld „Aktueller Stand" wird bereits durch den generischen `<dl>`-Metadaten-
Parser in `parlis_parser.py` erfasst und als Schlüssel im `RawVorgang`-Dict
bereitgestellt — es war lediglich nicht ausgewertet.

**Implementierung:** `bawue_vorgaenge_scraper.py`, Methoden `_build_vorgang()`
und `_ensure_ablehnung_station()`.
