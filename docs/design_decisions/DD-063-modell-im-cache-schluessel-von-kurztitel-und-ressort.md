[← Index](../design_decisions.md)

# DD-063: Modell im Cache-Schlüssel von Kurztitel und Ressort, Temperatur bleibt Provider-Default (GitHub Issue #57)

**Datum:** 09.10.2026

**Kontext:** Kurztitel (DD-053) und Ressort (DD-055) entstehen je Vorgang per LLM und werden
in Redis gecacht. Der Schlüssel war `sha256(System-Prompt + User-Message)`; das Modell
(`[llm] model`, Default `gpt-5-nano`) fehlte. Ein Modellwechsel, etwa gegen
Fehlklassifikationen, hätte nichts bewirkt: Jeder gecachte Vorgang hätte weiter die Antwort
des alten Modells geliefert. Die Schlüsselzeile stand außerdem doppelt im Code, und kein Test
legte sie fest. Eine versehentliche Änderung am Format hätte den Cache unbemerkt verwaist.

**Entscheidung:**

1. **Das Modell gehört zum Schlüssel:** `sha256(Modell + System-Prompt + User-Message)`, für
   beide Calls gebaut in `_prompt_cache_key`. Maßgeblich ist der konfigurierte String
   `[llm] model`; `openai/gpt-5-nano` statt `gpt-5-nano` erzeugt also nur neu, trifft aber
   nie falsch.
2. **Golden Keys:** Ein Test legt je Call den Schlüssel für eine feste Eingabe fest. Ändert
   sich ein Prompt oder das Format, schlägt er fehl. Der Schlüssel wird dann bewusst
   nachgezogen, im Wissen, dass der Namespace damit verwaist.
3. **Keine Temperatur, kein Seed.** `gpt-5-nano` ist ein Reasoning-Modell und nimmt nur
   `temperature=1`. litellm 1.100.1 lehnt `temperature=0` mit `UnsupportedParamsError` ab,
   und `reasoning_effort="none"` unterstützt das Modell laut litellm-Modellkarte nicht. Einen `seed` reicht litellm
   zwar durch, OpenAI verspricht damit aber nur Best-Effort-Determinismus. Die Antworten
   bleiben also gesampelt, die Stabilität zwischen Läufen kommt allein aus dem Cache. Jede
   Schlüsseländerung (Prompt, Ressort-Liste, neu erzeugte Initiativ-Zusammenfassung) kann
   deshalb Kurztitel oder Ressort ändern, ohne dass sich der Vorgang inhaltlich geändert hat.
4. **Kurztitel und Ressort laufen weiter nacheinander.** Beide sind gecacht und teilen sich
   die LLM-Semaphore (3). Ein `asyncio.gather` spart nur bei Cache-Fehlschlägen Zeit, und
   dass diese Latenz stört, ist nicht belegt.

**Wirkung eines Modellwechsels:** Kurztitel und Ressort entstehen nur, wenn ein Vorgang neu
gebaut wird. Ein Modellwechsel erreicht deshalb nur Vorgänge, die danach ohnehin neu gebaut
werden: bei PARLIS nach einem geänderten `vg2:`-Fingerprint (DD-052), bei Beteiligung nie,
denn dort ist `vg2:` ein reiner Slug-Eintrag ohne Fingerprint. Abgeschlossene Vorgänge
behalten die alte Antwort. Sollen alle neu erzeugt werden, müssen die `vg2:`-Einträge
gelöscht werden (DD-052, Punkt 4). Das baut jeden Vorgang samt PDF-Download neu und kostet
je Vorgang zwei LLM-Calls (drei mit Re-Prompt); die Dokument-Zusammenfassungen kommen weiter
aus `llm-semantics:`.

**Migrationshinweis:** Mit dem Deploy sind alle Einträge unter `vorgang-kurztitel:` und
`vorgang-ressort:` verwaist; sie haben kein TTL und bleiben liegen, bis sie gelöscht werden.
Neu erzeugt wird erst beim nächsten Neubau eines Vorgangs, mit zwei Calls (drei mit
Re-Prompt). Weil gesampelt wird, können sich dabei Kurztitel und Ressort einmalig ändern.

**Nicht umgesetzt:** Auch der Semantik-Cache der Dokumente (`llm-semantics:`, DD-020)
enthält das Modell nicht. Nach einem Modellwechsel bleiben die Zusammenfassungen, die Eingabe
von Kurztitel und Ressort, also beim alten Modell. Mit dem Modell im Schlüssel würde nach dem
Deploy und nach jedem Modellwechsel jeder neu gebaute Vorgang alle Dokumente neu
zusammenfassen. Das ändert sichtbare Zusammenfassungen und über die User-Message auch
Kurztitel und Ressort; das ist eine eigene Entscheidung.

**Code:** `bawue_dok._prompt_cache_key`, `bawue_dok.vorgang_kurztitel`, `bawue_dok.vorgang_ressort`

**Tests:** `tests/unit/test_issue57_vorgang_llm_cache_key.py`
