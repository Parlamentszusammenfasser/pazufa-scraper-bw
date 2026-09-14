[← Index](../design_decisions.md)

# DD-030: Ollama-/Local-Provider-Support entfernt — OpenAI als einziger LLM-Provider

**Datum:** 13.07.2026

**Kontext:** Der Scraper unterstützte zwei LLM-Provider: OpenAI (Staging/Produktion,
`gpt-5-nano`) und ein lokales Ollama als kostenlose Dev-Alternative
(`provider-base-url`, `ollama/gemma4:e4b`). Da die corelib-`LLMConnector`
kein `api_base` als Konstruktor-Argument kennt, wurde der Wert nachträglich als
Instanz-Attribut gesetzt (`self._llm.api_base = …`) und in `bawue_dok` per
`getattr` gelesen und an `litellm.acompletion` durchgereicht — ein Workaround.

Mit der Abschnitts-Extraktion (DD-029) verschärfte sich die Lage: Die
corelib-Methode `extract()` reicht `api_base` **nicht** durch, sodass die
Funktion auf dem Ollama-Pfad den falschen Endpunkt getroffen hätte. Die
Abschnitts-Extraktion — die entscheidende Qualitätsverbesserung für Issue #32 —
wäre auf Ollama also gar nicht verfügbar.

**Entscheidung:** Der Ollama-/Local-Provider-Pfad wird entfernt; OpenAI ist der
einzige unterstützte LLM-Provider. Konkret gestrichen:

- Konfigurationsschlüssel `llm.provider-base-url` / Umgebungsvariable
  `LLM_PROVIDER_BASE_URL` (`config.py`).
- Der `api_base`-Workaround in beiden Scrapern und in `extract_semantics()`
  (`bawue_dok.py`). `_llm_enabled` hängt nun allein an `LLM_PROVIDER_KEY`.
- Die OpenAI-Pfad-Bedingung in `narrow_to_relevant_section()` entfällt (es gibt
  nur noch den OpenAI-Pfad).
- Das Vergleichsskript `scripts/compare_llm_providers.py` (Zweck war der
  Ollama-vs-OpenAI-Vergleich) samt Makefile-Ziel `compare-llm` und der
  Testdatei `tests/unit/test_ollama_config.py`.
- Ollama-Defaults in `config.dev.toml` / `config.sample.toml` → `gpt-5-nano`.

**Warum kein Erhalt:** Ollama war reine lokale Dev-Bequemlichkeit; Staging und
Produktion liefen ohnehin auf OpenAI. Der Workaround (Monkey-Patch eines nicht
vorgesehenen Attributs) war fragil und blockierte die corelib-Abschnitts-
Extraktion. Der Wegfall der kostenlosen lokalen Entwicklung wird als vertretbar
bewertet; Dev benötigt jetzt einen OpenAI-Key (`gpt-5-nano` ist mit
~0,0007 $/Dokument sehr günstig).

**Implementierung:** `config.py`, `bawue_vorgaenge_scraper.py`,
`bawue_beteiligung_scraper.py`, `bawue_dok.py`; Konfig- und README-Anpassungen.
