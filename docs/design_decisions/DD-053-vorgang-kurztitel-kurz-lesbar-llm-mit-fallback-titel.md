[← Index](../design_decisions.md)

# DD-053: `Vorgang.kurztitel` — kurzer, lesbarer Titel per LLM, Fallback `titel` (GitHub Issue #32)

**Datum:** 19.09.2026

**Kontext:** Der Beteiligungsportal-Scraper setzte `kurztitel` auf den URL-Slug
(`dienst-und-versorgungsbezuege`), begründet mit „Backend-Merge mit PARLIS-Daten".
Das Backend matcht Vorgänge aber nur über `api_id` bzw. `ids` + Bundesland
(`pazufa-backend-lib/src/db/merge/candidates.rs`); `kurztitel` spielt dort keine Rolle
und wird beim Merge überschrieben (`execute.rs`, kein `COALESCE`). PARLIS übernahm den
`kurztitel` des initiierenden Dokuments aus dem Dokument-Prompt, der keine Längengrenze
kennt: auf Staging war er bei 78 von 235 BW-Vorgängen länger als `titel`, bei 7 eine
Kopie davon. Laut Wiki ist der Kurztitel „eine etwas griffigere Überschrift".

**Entscheidung** (mit dem Maintainer abgestimmt, siehe Issue):

1. **Immer generiert**, für PARLIS und Beteiligungsportal: ein eigener LLM-Call erhält
   `titel` + `zusammenfassung` des initiierenden Dokuments (`_initiativ_zusammenfassung`,
   Fallback: erstes Dokument mit Zusammenfassung) und liefert höchstens **60 Zeichen**.
   Ein bereits kurzer, verständlicher Titel darf unverändert zurückkommen.
2. **Regeln:** einfache Sprache, keine Floskeln wie „Gesetz zur Änderung des …", eine
   Zeile, kein Slug. Anführungszeichen und Schlusspunkt werden entfernt, spitze Klammern
   neutralisiert (DD-027). Ein Regelverstoß wird **einmal** mit Begründung nachgefragt.
3. **Fallback `titel`** bei LLM aus, Fehler oder zweitem Verstoß — nie `null` (das
   Backend überschreibt), nie der Slug.
4. **Eigener Redis-Namespace** `vorgang-kurztitel:<sha256(system prompt + prompt + titel + zusammenfassung)>`:
   stabil zwischen Läufen, die `llm-semantics:`-Caches der Dokumente bleiben gültig. Nur
   generierte Titel werden gecacht, der Fallback nicht (sonst bliebe ein transienter
   Fehler dauerhaft).

`Dokument.kurztitel` bleibt unverändert.

**Rollout:** Bestehende Vorgänge sind über `vg2:` gecacht (DD-052, Beteiligung: Slug ohne
TTL) und erhalten den neuen Kurztitel erst nach Änderung am Record oder Löschen der
`vg2:`-Einträge.

**Code:** `bawue_dok.vorgang_kurztitel`, `KURZTITEL_PROMPT`, `_kurztitel_problem`,
`_clean_kurztitel`; `_initiativ_zusammenfassung`, `_build_vorgang`;
`bawue_beteiligung_scraper._build_vorgang`

**Tests:** `tests/unit/test_issue32_vorgang_kurztitel.py`,
`test_bawue_scraper.py::TestBuildVorgang` (`…_kurztitel_…`),
`test_beteiligung_scraper.py` (`test_kurztitel_…`),
`tests/integration/test_llm_extraction.py::…::test_vorgang_kurztitel_is_short_and_readable`
