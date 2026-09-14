[← Index](../design_decisions.md)

# DD-027: LLM-Output-Sanitisierung gegen XSS-Validator des Backends

**Datum:** 11.05.2026

**Kontext:** Im Staging-Lauf 2026-05-10 (Container 22:45 → 06:06 CEST,
Cycle-Loop nach DD-026-Fix) lehnte das Backend V-243670 („Gesetz zur
Änderung des Universitätsklinika-Gesetzes (UKG) und anderer Gesetze") über
229 Cycles hinweg mit HTTP 400 ab:

```
body.stationen[4].dokumente[0].zusammenfassung:
  Validation error: xss detected
  [{"value": String("… mit Artikel 4 ab dem 1. Januar 2026.</narrow>")}]
```

DD-025 hatte den Artefakt bereits beobachtet und als Folgearbeit
ausgewiesen. Eine Inventur des aktuellen JSONL bestätigt: Genau **ein**
Vorgang ist betroffen, **kein** anderes LLM-Stringfeld
(`kurztitel`/`vorwort`/`schlagworte`) enthält im Lauf HTML-ähnliche Tokens.

**Ursache:** `gpt-5-nano` emittiert sporadisch ein abschließendes
`</narrow>`-Token am Ende längerer Antworten, obwohl der System-Prompt
ausdrücklich Formatierungen verbietet (`Füge keine Formatierungen oder
Hervorhebungen hinzu.`). Der Backend-XSS-Validator weist jeden String mit
`<` oder `>` ab. `normalize_volltext` schützt das `volltext`-Feld bereits
durch Guillemet-Substitution (DD-014), die LLM-Semantik-Felder
(`zusammenfassung`, `kurztitel`, `vorwort`, `schlagworte`) wurden bisher
**ungeprüft** in das `Dokument` übernommen.

**Entscheidung:** Zwei Sanitiser-Helper in `bawue_dok.py`
(`_sanitize_llm_text`, `_sanitize_llm_strings`) reinigen jeden
LLM-emittierten String an der Datenmodellgrenze in `enrich_dokument`:

1. Entferne wohlgeformte HTML-Tags via Regex `</?[a-zA-Z][^<>]{0,80}>` —
   trifft `</narrow>`, `<strong>`, `<br/>` usw., **nicht** aber
   mathematische Vergleiche wie `x < 5 und y > 3` (das Tag muss mit einem
   Buchstaben beginnen).
2. Verbleibende Einzel-Brackets werden auf Guillemets `‹` / `›` abgebildet
   (Defense-in-Depth, gleicher Mechanismus wie `normalize_volltext`).
3. Whitespace wird getrimmt; ein nach Reinigung leerer String wird zu
   `None` (das API-Modell lässt `None`-Felder weg statt Leerstrings zu
   senden, die das Backend ebenfalls ablehnt).

Sanitisiert werden im Erfolgspfad von `enrich_dokument`:
`zusammenfassung`, `kurztitel`, `vorwort` (Strings) sowie jedes Element
von `schlagworte` (Liste, leer-nach-Reinigung wird verworfen). Numerische
Felder (`meinung`, `trojanergefahr`) bleiben unangetastet — sie laufen
durch `_validate_scores`.

**Anwendungsort:** Die Sanitisierung passiert **nach** dem Cache-Lookup,
also direkt vor der `Dokument`-Konstruktion. So werden auch alte
Redis-/In-Memory-Cache-Einträge mit `</narrow>` (z. B. der seit Tagen
vergiftete V-243670-Eintrag) bei jedem Read transparent gereinigt — eine
manuelle Cache-Invalidierung ist nicht nötig.

**Begründung gegen Alternativen:**

1. **System-Prompt verschärfen / Re-Prompting:** würde nur das
   Wahrscheinlichkeitsmuster verschieben, nicht garantieren. Modelle wie
   gpt-5-nano emittieren weiterhin gelegentlich Formatierungs-Token.
2. **Sanitisierung in `extract_semantics` vor dem Caching:** Alte
   Cache-Einträge blieben vergiftet, manuelle Redis-Bereinigung wäre
   nötig.
3. **Pauschale Guillemet-Substitution wie in `normalize_volltext`:** Würde
   `‹/narrow›` als sichtbares Artefakt in der Zusammenfassung lassen.
   Tag-Stripping vorab ergibt sauberere Ausgabe; Guillemet-Pass bleibt als
   zweite Verteidigungslinie.

**Tests:** `tests/unit/test_bawue_dok.py`:

- `TestSanitizeLlmText` (10 Tests) — direkter Helper-Test:
  `</narrow>`-Trailing-Stripping, Inline-Tag-Stripping, selbstschließendes
  `<br/>`, Pass-through für sauberen Text, Guillemet-Substitution für
  echte Vergleichsoperatoren, leere/None-Eingabe → None,
  pathologische Lang-Tags (>80 Zeichen) fallen auf Bracket-Substitution
  zurück, Listen-Helper droppt Leer-Ergebnisse.
- `TestEnrichDokumentSanitization::test_narrow_artefact_stripped_from_zusammenfassung` —
  End-to-End: LLM-Mock liefert `</narrow>` und `<hr/>` zurück, das
  resultierende `Dokument` enthält keine Brackets in
  `zusammenfassung`/`vorwort`.

**Verifikation in Produktion:** Beim nächsten Staging-Cycle wird V-243670
ohne `Track validation Failed` für `zusammenfassung` durchlaufen. Die
beiden verbleibenden bekannten Fehler (`gg-land-volk` für V-230205 und
V-232608, DD-024-Folgearbeit) bleiben Backend-seitig.
