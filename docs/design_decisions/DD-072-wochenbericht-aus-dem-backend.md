[← Index](../design_decisions.md)

# DD-072: Wochenbericht aus dem Backend (GitHub Issue #82)

**Datum:** 17.10.2026

**Kontext:** Wer die Seite nur ab und zu liest, will wissen, welche Gesetzesvorhaben in der
Woche neu sind oder weitergekommen sind. Der Scraper selbst hat keine Historie (DD-052). Der
Zeitstempel „geändert“ im Backend reicht nicht: Jeder Neuaufbau (z. B. DD-071) setzt ihn.

**Entscheidung:**

1. Einmal pro ISO-Woche, im ersten Lauf (normal Montag 03:00), holt der Scraper alle
   BW-Vorgänge der Wahlperiode (`GET /api/v2/vorgang`) und merkt sich je Gesetzgebungsvorgang
   (`gg-*`) den Typ der letzten **begonnenen** Station in Redis (`wochenbericht`).
2. Der Vergleich mit dem Stand der Vorwoche ergibt die Zeilen: 🆕 neu, ➡️ andere Station.
   Verspätete Uploads zählen so in der Woche, in der sie ankommen.
3. Geplante Stationen (DD-069) zählen erst ab ihrem Datum.
4. Der Bericht ist ein Abschnitt im Run-Report (#52). Ab 20 Zeilen wird gekürzt.
5. Fail-soft: Scheitert der Abruf, bleibt der alte Stand, der nächste Lauf versucht es neu.

**Nicht abgedeckt:** Der erste Lauf und ein geleertes Redis liefern keinen Bericht, nur einen
neuen Stand. Scheitert der Mattermost-Post, fehlt der Bericht dieser Woche. Eine zweite Station
gleichen Typs (z. B. zweite Ausschussberatung) gilt nicht als Änderung.

**Code:** `bawue.wochenbericht`, `api.get_vorgaenge`, `__main__.main`

**Tests:** `tests/unit/test_wochenbericht.py`, `test_api.py::TestGetVorgaengeIssue82`,
`test_main.py::TestIssue82WeeklySection`
