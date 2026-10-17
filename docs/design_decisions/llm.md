[← Index](../design_decisions.md)

# LLM-Anreicherung

Was das LLM liefert, mit welchem Prompt und wie gecacht wird (`bawue_dok.py`). Die
Anreicherung ist optional (`LLM_PROVIDER_KEY`). Die Modelle sampeln (DD-063); stabil zwischen
Läufen sind die Antworten nur durch den Cache. Jede Änderung am Cache-Schlüssel kann
sichtbare Texte ändern.

<a id="dd-030"></a>
## DD-030: OpenAI ist der einzige Provider

**Kontext:** Ein lokales Ollama lief über einen Workaround (`api_base` nachträglich gesetzt),
den die corelib-Abschnittsextraktion nicht durchreichte.

**Entscheidung:** Nur OpenAI (Default `gpt-5-nano`). `provider-base-url` und der Workaround
sind entfernt; auch Dev braucht einen OpenAI-Key.

<a id="dd-013"></a>
## DD-013: Token-Kürzung — aufgehoben

Der Volltext wurde vor dem LLM-Call auf 12 000 Tokens gekürzt. Das schnitt bei
Plenarprotokollen die relevante Debatte ab (Issue #32). Ersetzt durch DD-029.

<a id="dd-029"></a>
## DD-029: Voller Text und Dokumentkontext im Prompt

**Kontext:** Die Zusammenfassung des Fischereigesetzes beschrieb ein anderes Gesetz: Die
Kürzung behielt die vorherige Debatte im selben Protokoll, und der Prompt nannte das
Zielgesetz nicht (Issue #32, V-212391).

**Entscheidung:**
- Keine Kürzung. Der normalisierte Text geht vollständig ans LLM; das Seitenfenster um den
  `#page=N`-Anker begrenzt Protokolle.
- Ein Kontext-Header nennt Vorgangstitel und Drucksache; bei mehreren Themen zählt nur
  dieses. Beide gehen in den Cache-Schlüssel ein.

**Konsequenz:** Für Protokolle gilt seit DD-037 beides nicht mehr. Die Abschnittsextraktion
über corelib (`narrow_to_relevant_section`) war nur für Protokolle gedacht und läuft deshalb
nie.

<a id="dd-033"></a>
## DD-033: Initiativdrucksache als Anker und im Cache-Schlüssel

**Kontext:** Ein Protokoll trägt keine eigene Drucksache. Der stärkste Anker für das
Zielgesetz, die Initiativdrucksache, ging verloren (Issue #35).

**Entscheidung:** `_build_vorgang` ermittelt die Initiativdrucksache vorab aus den Fundstellen
und reicht sie bis `enrich_dokument` durch (`vorgang_vnr`); sie geht in
`_prompt_fingerprint` ein. Für Protokolle gilt das seit DD-037 nicht mehr.

<a id="dd-037"></a>
## DD-037: Eine neutrale Zusammenfassung je Plenarprotokoll

**Kontext:** DD-029/DD-033 fassten ein Protokoll je Vorgang verschieden zusammen. Das
Backend speicherte damals einen Row je PDF; der zuletzt hochgeladene Vorgang überschrieb die
anderen (Issue #49: fünf Gesetze, eine Kita-Zusammenfassung).

**Entscheidung:** Für `redeprotokoll` keine Vorgangsidentität im Prompt, kein Kontext-Header,
ein eigener neutraler Prompt (`BODY_PROMPT_REDEPROTOKOLL`), der alle Tagesordnungspunkte
gleichrangig nennt. Der Cache-Schlüssel ist damit Datei-Hash + Prompt: Alle Vorgänge einer
Sitzung bekommen dieselbe Zusammenfassung, unabhängig von der Reihenfolge.

**Konsequenz:** Seit DD-049 hat jeder Vorgang wieder einen eigenen Row. Eine Zusammenfassung
je Gesetz wäre damit möglich, kostet aber einen Call je Gesetz statt je Sitzung; bleibt
neutral.

<a id="dd-020"></a>
## DD-020: Cache-Schlüssel aus Inhalt und Prompt

**Kontext:** Ein Schlüssel nur aus dem Inhalts-Hash lieferte für dasselbe PDF in anderem
Kontext das Ergebnis eines anderen Prompts.

**Entscheidung:** `llm-semantics:<doc_hash>:<prompt_hash>`. `prompt_hash` umfasst System- und
Body-Prompt des Doktyps und den Kontext (DD-029/DD-033). Zweistufig: im Speicher je Lauf,
dann Redis.

**Konsequenz:** Jede Prompt-Änderung rechnet die betroffenen Dokumente einmal neu. Das Modell
gehört nicht zum Schlüssel (DD-063).

<a id="dd-027"></a>
## DD-027: LLM-Ausgabe wird gegen den XSS-Validator gereinigt

**Kontext:** `gpt-5-nano` hängt sporadisch `</narrow>` an. Das Backend lehnt jedes `<`/`>` ab
(V-243670, 229 Läufe HTTP 400).

**Entscheidung:** Jeder LLM-String (Zusammenfassung, Abschnitte, Kurztitel, Vorwort,
Schlagworte) geht durch `_sanitize_llm_text`: HTML-Tags (`</?[a-zA-Z][^<>]{0,80}>`)
entfernen, übrige Klammern zu `‹`/`›`, leer → `None`. Das passiert **nach** dem Cache-Lesen,
so werden auch alte Einträge gereinigt.

**Verworfen:** strengerer Prompt (keine Garantie), Reinigen vor dem Cachen (alte Einträge
blieben vergiftet).

<a id="dd-054"></a>
## DD-054: Zusammenfassung als Tupel `full-llm`

**Kontext:** Seit Spec 0.2.5 ist `zusammenfassung` eine Liste typisierter Tupel; ein String
gilt als `full` ohne Herkunft (Issue #42).

**Entscheidung:** `[("full-llm", text)]`, ohne Modellnamen im Typ — sonst erzeugte jeder
Modellwechsel einen neuen Typ und alte blieben stehen. Leser holen den Text über
`zusammenfassung_text()`.

**Konsequenz:** Das Backend merged je Typ und löscht nie: ältere BW-Dokumente tragen `full`
und `full-llm`.

<a id="dd-056"></a>
## DD-056: Teil-Zusammenfassungen wie Brandenburg

**Kontext:** Die Spec erlaubt weitere Tupel. `pazufa-scraper-bb` liefert drei; ein
gemeinsames Vokabular gibt es noch nicht.

**Entscheidung:** BBs Satz unverändert, damit Länder vergleichbar bleiben: `intention-llm`,
`regelungsinhalt-llm`, `kosten-llm`, je 1–3 Sätze. Nur für `entwurf`, `preparl-entwurf`,
`beschlussempf`; im selben Call. Leere Abschnitte entfallen; ohne `full-llm` entfallen alle.
Das `-llm`-Suffix steuert das „KI-generiert"-Kennzeichen der Website.

<a id="dd-063"></a>
## DD-063: Modell im Cache-Schlüssel von Kurztitel und Ressort

**Kontext:** Ohne Modell im Schlüssel hätte ein Modellwechsel bei gecachten Vorgängen nichts
bewirkt (Issue #57).

**Entscheidung:**
1. `sha256(Modell + System-Prompt + User-Message)` für Kurztitel und Ressort
   (`_prompt_cache_key`); Golden-Key-Tests pinnen das Format.
2. Keine Temperatur, kein Seed: `gpt-5-nano` nimmt nur `temperature=1`, ein Seed ist bei
   OpenAI nur Best-Effort.

**Konsequenz:** Ein Modellwechsel erreicht nur neu gebaute Vorgänge; für alle müssen die
`vg2:`-Einträge gelöscht werden (DD-052). `llm-semantics:` enthält das Modell bewusst nicht:
sonst fasste nach jedem Wechsel jeder Neubau alle Dokumente neu zusammen.
