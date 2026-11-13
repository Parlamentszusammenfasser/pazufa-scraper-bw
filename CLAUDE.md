# CLAUDE.md — BaWue Scraper

Scrapes Landtag Baden-Württemberg (parliament code `BW`, Wahlperiode 18, Python 3.14,
package `bawue` in `src/`) and pushes `Vorgang`/`Sitzung` objects to the
[PaZuFa](https://codeberg.org/PaZuFa/parlamentszusammenfasser) Write-API v2. Depends only on
[pazufa-scraper-core](https://codeberg.org/PaZuFa/pazufa-scraper-core) (pinned git tag).
Repo, issues and PRs live on **GitHub** (`gh`); backend, spec and corelib on Codeberg.
The generic guidelines in `../CLAUDE.md` apply too.

## Maintenance mode — why it shapes every change

The scraper runs in production (GCP Cloud Run); **every merge to `main` ships**. Its users see
our output as public parliamentary data, so a wrong Station or a rejected upload is a visible
defect. Work like the lead engineer who owns it long-term:

- **Understand before changing.** Behavior here is usually deliberate. Read the code, its
  callers, the relevant DD and tests; name what the change can break before writing it.
- **Small, focused diffs.** The smallest change that fully solves the problem. No speculative
  options, no side features, no tutorial-style code ("forum examples") — write what this
  codebase would write.
- **Leave touched code better.** Continuous improvement keeps a long-lived codebase readable:
  if code you touch can be modernized or clarified, refactor it under green tests in a separate
  `refactor:` commit. Larger refactorings: propose, don't do.
- **Tests are the safety net, not ballast.** Failing test first; each fixed issue leaves a
  regression test named for the issue/Drucksache. Cover the edge cases real PARLIS data
  produces (missing dates, same-day Stationen, recurring Drucksachen). After implementing,
  prune: merge near-duplicates into parameterized tests, drop tests of library/standard
  behavior. Fewer, sharper tests are easier to keep green.
- **Comments say why** (cite DD/issue), never what.

## What breaks things — the backend's two rules

A `Vorgang` is a list of `Station`s (`typ`, `zp_start`, `gremium`, `dokumente`). Most bugs
come from two backend behaviors:

1. **Track validation orders Stationen by `zp_start`, not list order**, and demands a canonical
   sequence (BY track, DD-016). Out-of-order timestamps → HTTP 400. So ordering logic must
   reason about time, not position (#48).
2. **The backend merges Stationen by `api_id`, else by document hash.** The same PDF recurs
   across Vorgänge, so hash matching collides → HTTP 500. Hence every Station gets a
   Vorgang-scoped stable `api_id` (DD-028/DD-034, #47). Treat `api_id`s and the cache
   fingerprint as contracts: changing them duplicates Stationen or forces a full re-upload.

## Design decisions

`docs/design_decisions.md` indexes every deliberate deviation from PaZuFa conventions
(DD-001 …; details in `docs/design_decisions/<topic>.md`, anchor `#dd-NNN`). Check it before
touching station mapping, ordering, enums or synthetic Stationen — what looks like a bug is
often a DD. When a change alters a rule, update the DD in the same change; prefer extending an
existing DD over adding one. Keep DDs as context → decision → consequence; history goes in
commits. `docs/architecture.md` has the full component and data-flow reference.

## Workflow

- Branch `fix/issue-<N>-<slug>` (or `fix/<slug>`) off `main`; PR via `gh pr create --base main`.
  Don't commit or push unless asked.
- Conventional commits (`feat`, `fix`, `refactor`, `test`, `docs`, `chore`, …; `!` for breaking):
  python-semantic-release derives versions and releases from them on `main`.
- `make test` and `make lint` must be green. See `make help` for the rest (`test-all` needs a
  backend; `run` needs `config.toml` + Redis; `mock_pazufa_server.py` replaces the backend).
- `locallogs/*.jsonl` are dumps of fully built Vorgänge (post `_build_vorgang`), useful as
  real-world test input — not error logs.
