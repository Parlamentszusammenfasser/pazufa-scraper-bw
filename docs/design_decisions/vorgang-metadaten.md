[← Index](../design_decisions.md)

# Vorgang-Metadaten

Woher die Felder eines `Vorgang` kommen, die PARLIS nicht direkt liefert. Allgemein gilt:
kein Wert → Feld `UNSET`, nie ein erfundener. Felder außerhalb des `vg2:`-Fingerprints
(DD-052) erreichen gecachte Vorgänge erst beim nächsten Neubau.

<a id="dd-009"></a>
## DD-009: Initiator aus dem Autor der ersten Fundstelle

**Kontext:** Bei Haushaltsgesetzgebung fehlt das Feld „Initiative"; DD-003 braucht es.

**Entscheidung:** Fehlt es, gilt der `autor_text` der ersten Fundstelle.

<a id="dd-023"></a>
## DD-023: `verfassungsaendernd` aus dem Titel

**Kontext:** Pflichtfeld, das keine BW-Quelle liefert. Die DoD-Regel „Pflichtfeld nicht
füllbar → Objekt weglassen" würde 100 % der Vorgänge verwerfen.

**Entscheidung:** Titel enthält „Änderung der (Landes-)Verfassung" oder
„Verfassungsänderung" → `True`, sonst `False` (`is_verfassungsaendernd`). Eine Wortgrenze
verhindert Treffer in Komposita wie „Verfassungsschutz". Art. 64 LV verlangt dafür 2/3; die
Titel benennen es zuverlässig.

**Neu bewerten, wenn:** das Feld optional wird oder eine `unknown`-Semantik bekommt — dann
Heuristik entfernen.

<a id="dd-053"></a>
## DD-053: `Vorgang.kurztitel` per LLM, höchstens 60 Zeichen

**Kontext:** Das Beteiligungsportal setzte den URL-Slug, PARLIS den ungekürzten Kurztitel des
Initiativdokuments (bei 78 von 235 länger als `titel`). Für den Backend-Merge spielt der
Kurztitel keine Rolle (Issue #32).

**Entscheidung:**
1. Ein eigener LLM-Call aus `titel` + Zusammenfassung des Initiativdokuments, ≤ 60 Zeichen,
   einfache Sprache, keine Floskeln, kein Verfahrensstand wie „beschlossen" (der Titel
   bleibt gecacht, während der Vorgang fortschreitet, Issue #72).
2. Ein Regelverstoß wird einmal mit Begründung nachgefragt.
3. Fallback `titel` (LLM aus, Fehler, zweiter Verstoß) — nie `null`, nie der Slug.
4. Cache `vorgang-kurztitel:<sha256(Modell + Prompts + Eingaben)>` (DD-063); nur
   generierte Titel, nicht der Fallback.

<a id="dd-055"></a>
## DD-055: `Vorgang.ressort` per LLM nach fachlichem Schwerpunkt

**Kontext:** BW liefert kein Ressort, nur Ministeriumsnamen; ein Ministerium deckt mehrere
Ressorts ab. `pazufa-scraper-bb` klassifiziert per LLM nach Schwerpunkt, nicht nach Akteur
(Issue #39).

**Entscheidung:**
1. Ein LLM-Call je Vorgang aus `titel` + Zusammenfassung des **Initiativdokuments** gegen
   das `Ressort`-Enum. Regeln und Enum-Liste im Prompt sind zeichengleich mit BB; Änderungen
   mit BB abstimmen. Das Modell begründet zuerst, dann nennt es das Ressort.
2. Die Antwort wird schreibweisentolerant aufgelöst; unauflösbar → verworfen mit WARNING,
   nie als Rohstring gesendet.
3. Kein Ressort → `UNSET` (LLM aus, Platzhaltertitel, Fehler, `null`, unauflösbar).
4. Cache `vorgang-ressort:` mit der Rohantwort, auch für ein klassifiziertes `null`; nur
   fehlgeschlagene Calls nicht.
5. `LLMMetrics` zählt `classified`/`null`/`rejected`/`failed` im Laufbericht (Issue #56).

**Konsequenz:** Ein Windkraft-Gesetz aus dem Umweltministerium kann `Energie` tragen —
gewollt. Abweichend von BB: Eingabe ist die Zusammenfassung statt des Volltexts.

<a id="dd-058"></a>
## DD-058: `Vorgang.sachgebiete` aus dem PARLIS-Feld „Sachgebiet"

**Kontext:** PARLIS liefert je Vorgang ein Feld Sachgebiet (WMV32) in derselben
Parlamentsspiegel-Systematik. Die Deskriptoren sind freie Schlagworte (Issue #40).

**Entscheidung:** Jeder Begriff wird exakt (ohne Groß-/Kleinschreibung) gegen das
corelib-Vokabular aufgelöst, dann der Teil vor dem ersten Komma („Jagd, Fischerei" →
„Jagd"). **Kein Fuzzy-Matching:** es macht aus „Öffentliche Schulen" „Öffentliche Schulden".
Unauflösbare Begriffe werden mit WARNING verworfen, die Platzhalter 9900/9999 nie gesendet.

**Konsequenz:** Alle 238 Vorgänge (WP 17/18) bekommen mindestens ein Sachgebiet, ohne Warnung.

<a id="dd-060"></a>
## DD-060: `Vorgang.schlagworte` aus den PARLIS-Deskriptoren

**Kontext:** PARLIS pflegt je Vorgang Deskriptoren. `WMV33` ist bei langen Listen gekürzt,
`EWBV34` vollständig (Issue #33).

**Entscheidung:** Quelle ist `EWBV34`, alle Begriffe in PARLIS-Reihenfolge, nicht-strikt
gegen das corelib-Vokabular kanonisiert: bekannte Begriffe in Vokabular-Schreibweise, alle
anderen wörtlich (strikt blieben 32 von 986). Die LLM-Schlagworte je Dokument bleiben.

**Konsequenz:** Ein Test über alle 986 beobachteten Begriffe pinnt die 8 erwarteten
Umschreibungen. Neue Begriffe kann Fuzzy falsch ziehen („Schulden" → „Schulen"); ein höherer
Schwellwert hilft nicht. Das Backend (v0.3.3) speichert Schlagworte nur beim Anlegen, beim
Merge fallen sie still weg, und es entfernt nie einen Begriff.
