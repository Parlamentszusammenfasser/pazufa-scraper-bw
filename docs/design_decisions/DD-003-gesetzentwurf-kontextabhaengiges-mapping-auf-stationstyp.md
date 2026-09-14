[← Index](../design_decisions.md)

# DD-003: Gesetzentwurf — kontextabhängiges Mapping auf Stationstyp

**Datum:** 27.03.2026 | **Aktualisiert:** 05.04.2026

**Kontext:** PARLIS verwendet den gleichen Fundstellentext „Gesetzentwurf" sowohl für
Regierungsentwürfe als auch für parlamentarische Initiativen (z. B. Fraktionsentwürfe).
Ohne zusätzlichen Kontext würde jeder Gesetzentwurf einheitlich als `parl-initiativ`
klassifiziert.

**Entscheidung:** Das Mapping von „Gesetzentwurf" hängt vom Initiator ab:

- Initiator enthält „Landesregierung" → `preparl-regbsl` (Kabinettsbeschluss)
- Sonst → `parl-initiativ` (parlamentarische Initiative)

Analog wird der Dokumententyp kontextabhängig zugeordnet:

- Station ist `preparl-regbsl` → `preparl-entwurf`
- Sonst → `entwurf`

Dies ist das einzige Enum-Mapping, das externen Kontext (den Initiator) benötigt.

**Aktualisierung (05.04.2026):** Ursprünglich wurde „Gesetzentwurf Landesregierung"
als `preparl-regent` (Regierungsentwurf, R) klassifiziert. Nach Review durch den
Backend-Entwickler (Issue #26) umklassifiziert zu `preparl-regbsl`
(Kabinettsbeschluss, S). Begründung: PARLIS zeigt den Entwurf **nach**
Kabinettsbeschluss — den parlamentarischen Eingang, nicht die Entwurfsphase.
Die vorparlamentarische Entwurfsphase (`preparl-regent`, R) wird vom
Beteiligungsportal-Scraper abgedeckt (`bawue_beteiligung_scraper.py`).
Diese Umklassifizierung ermöglicht die Nutzung des BY-Tracks
(`((E*R+)?S)?I...`) ohne BW-spezifischen Präfix.

**Implementierung:** `enum_mapper.py`, Funktionen `map_stationstyp()` (Parameter
`initiator`) und `map_dokumententyp()` (Parameter `is_vorparlamentarisch`).
