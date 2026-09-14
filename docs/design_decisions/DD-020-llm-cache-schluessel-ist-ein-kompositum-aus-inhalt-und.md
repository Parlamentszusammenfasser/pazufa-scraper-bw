[← Index](../design_decisions.md)

# DD-020: LLM-Cache-Schlüssel ist ein Kompositum aus Inhalt und Prompt

**Datum:** 21.04.2026

**Kontext:** Der LLM-Cache in `bawue_dok.py` speichert die Anreicherungsergebnisse
(Semantik, Dokumententyp) unter dem Schlüssel `llm-semantics:{doc_hash}`, wobei
`doc_hash` ein SHA-256-Hash des Volltexts ist. Da identische PDFs im PARLIS-System
in unterschiedlichen Kontexten auftauchen können (z. B. ein Gesetzentwurf, der
sowohl als `ENTWURF` als auch als `STELLUNGNAHME` klassifiziert werden soll),
führte ein rein inhaltsbasierter Cache-Schlüssel zu falschen Cache-Treffern: Das
LLM wurde nur einmal aufgerufen (erster `Doktyp`-Kontext), und alle nachfolgenden
Aufrufe für dasselbe PDF lieferten das gecachte Ergebnis des ersten Prompts —
unabhängig davon, welcher Prompt tatsächlich gesendet wurde.

**Entscheidung:** Der Cache-Schlüssel wird zu einem Kompositum:

```
cache_key = "{doc_hash}:{prompt_hash}"
```

Dabei ist `prompt_hash` ein SHA-256-Hash (erste 16 Zeichen) über die
Konkatenation von System-Prompt und Body-Prompt, wie sie für den jeweiligen
`Doktyp` generiert werden. Jede Kombination aus Dokument und Prompt-Vorlage
erhält damit einen eigenen Cache-Eintrag. Gleiche Dokumente mit gleichem Prompt
treffen weiterhin den Cache.

**Auswirkung auf bestehende Cache-Einträge:** Alte Redis-Einträge im Format
`llm-semantics:{doc_hash}` werden nicht mehr gelesen. Sie bleiben als Waisen
gespeichert, bis der TTL (2 Wochen) abläuft, und verursachen keine Fehler.

**Implementierung:** `bawue_dok.py`:

- `_prompt_fingerprint(doktyp)` — SHA-256-Hash über System- und Body-Prompt des jeweiligen `Doktyp`
- `_cache_key(doc_hash, prompt_hash)` — Konkatenation zu `{doc_hash}:{prompt_hash}`
- `enrich_dokument()` — berechnet beide Hashes vor dem Cache-Lookup
