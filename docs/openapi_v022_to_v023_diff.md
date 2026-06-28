# OpenAPI Client Diff: `openapi_client` (v0.2.2) → `pazufa_corelib.api_client` (v0.2.3)

> Phase 0.1 deliverable for [migration_remove_pazufa_collector.md](migration_remove_pazufa_collector.md).
> Status: **Draft — reviewer sign-off PENDING.** Do not start Phase 1 until a
> reviewer signs off on this diff (Phase 0.1 gate).
> Generated 2026-06-28 from the actually-vendored code, not from the spec text.

## Sources compared

| Side | Generator | Datamodel | Package | Spec |
| ---- | --------- | --------- | ------- | ---- |
| **Old** (current) | `openapi-generator-cli` (java) | Pydantic v2 | `openapi_client` (built into `vendor/pazufa-collector/oapicode`) | v0.2.2 |
| **New** (target) | `openapi-python-client` | attrs + `UNSET` sentinel | `pazufa_corelib.api_client` (`vendor/pazufa-scraper-core`) | v0.2.3 |

> ⚠️ **Vendored-version caveat.** The migration plan (v3) pins
> `pazufa-corelib` to `rev = "v0.1.2"`, but the locally vendored checkout
> reports `version = "0.1.1rc5"` and its `CHANGELOG.md` lists the
> `AuthorResolver`/`OrganizationResolver` work under **`[Unreleased]`** — i.e.
> this tree pre-dates even the 0.1.1 final release. The field/enum shapes
> below were read from *this* tree. Re-confirm against the actually-pinned
> tag before Phase 1 (see also Phase 0.2 backend gate). This does not block
> Phase 0, which still targets `openapi_client`.

---

## 1. Headline differences (apply to every model)

1. **Construction kwargs are identical** for business fields — both take
   `Vorgang(api_id=..., titel=..., wahlperiode=..., ...)`. This is what makes
   the Phase 0.3 type-alias flip viable for *model classes*.
2. **Optional-field default sentinel differs.** Pydantic side defaults
   optionals to `None`; attrs side defaults them to `UNSET` (`pazufa_corelib.api_client.types.UNSET`).
   - Serialisation consequence: `UNSET` fields are **omitted** from `to_dict()`
     output; the old Pydantic path emitted explicit `null` for `None`-valued
     optionals. (Risk #3 in the plan.) Any BaWue construction site that passes
     `field=None` for an optional must be reviewed — under attrs that emits
     JSON `null`, under the old path it also emitted `null`, but the *default*
     (field omitted entirely) now differs.
3. **Serialisation API differs.** Pydantic: `model_dump_json()` /
   `Model.from_json()` / collector's `sanitize_for_serialization`. attrs:
   `to_dict()` / `Model.from_dict()`. BaWue source does **not** call these
   directly today (serialisation happens inside the collector cache layer), so
   this only matters once `bawue/cache.py` is ported in Phase 2 (cache-format
   migration, plan §1.3).
4. **Unknown keys.** attrs `Model.from_dict()` tolerates unknown keys (stashes
   them in `additional_properties`); it does **not** raise. (The plan §1.3
   claim that `from_dict` "will raise on keys it doesn't recognise" is
   inaccurate for this generator — verify against the real cache-migration
   path in Phase 2.)

---

## 2. Enum member-name collapse — **biggest mechanical change, not covered by the alias flip**

Enum **string values are identical** across both clients. But the **Python
member identifiers differ**, because the two generators sanitise the `-` in
values like `gg-land-parl` differently:

| Enum | Value | Old member (`openapi_client`) | New member (`pazufa_corelib`) |
| ---- | ----- | ----------------------------- | ----------------------------- |
| `Vorgangstyp` | `gg-land-parl` | `GG_MINUS_LAND_MINUS_PARL` | `GG_LAND_PARL` |
| `Vorgangstyp` | `gg-land-volk` | `GG_MINUS_LAND_MINUS_VOLK` | `GG_LAND_VOLK` |
| `Stationstyp` | `parl-initiativ` | `PARL_MINUS_INITIATIV` | `PARL_INITIATIV` |
| `Stationstyp` | `preparl-regbsl` | `PREPARL_MINUS_REGBSL` | `PREPARL_REGBSL` |
| `Doktyp` | `preparl-entwurf` | `PREPARL_MINUS_ENTWURF` | `PREPARL_ENTWURF` |
| … | (all hyphenated values) | `*_MINUS_*` | `*_*` |

**Rule: in Phase 1, strip every `_MINUS_` from enum member references.**

> The Phase 0.3 type-alias adapter routes the enum **class import** through
> `bawue.types`, but member access (`Vorgangstyp.GG_MINUS_LAND_MINUS_PARL`)
> references a name that does not exist on the new enum. Aliasing the class
> does **not** fix this. ~75 member references in `src/` (heaviest:
> `enum_mapper.py` ≈45, `bawue_vorgaenge_scraper.py` ≈26) plus ~140 in tests
> must be renamed in Phase 1. This is a `_MINUS_` → `` search-and-replace, but
> it is a real edit the "single alias flip" framing in the plan understates.

No enum *values* were added or removed between the two enums for the members
BaWue uses (`Vorgangstyp`, `Stationstyp`, `Doktyp`, `Parlament` all verified
member-for-member).

---

## 3. Model-class field diffs (BaWue-relevant models only)

Business field names and required/optional split are **unchanged** for every
model BaWue touches. The only construction-affecting differences:

### `Dokument` — field renamed `hash` → `hash_`
- Old: `Dokument(..., hash=doc_hash)` (`hash: StrictStr`).
- New: attribute is `hash_` (Python keyword-clash avoidance); JSON key is still
  `"hash"`. Construction becomes `Dokument(..., hash_=doc_hash)`.
- **BaWue call sites to change in Phase 1:** `bawue_dok.py:679`, `bawue_dok.py:703`,
  `bawue_vorgaenge_scraper.py:836`, `bawue_beteiligung_scraper.py:205`, plus
  test fixtures (`test_bawue_dok.py`, `test_bawue_scraper.py`,
  `test_beteiligung_scraper.py`, `test_llm_extraction.py`).
  > Wrap this in the Phase 0.4 `make_dokument` helper too if the test churn is
  > large — currently deferred; `make_vorgang` is the only required helper.

### `StationDokumenteInner` — **class removed** in the new client
- Old: `Station.dokumente: List[StationDokumenteInner]`,
  `Sitzung.dokumente: Optional[List[StationDokumenteInner]]`. BaWue wraps every
  doc as `StationDokumenteInner(dok)`.
- New: the union is inlined — `Station.dokumente: list[Dokument | str]`
  (same for `Sitzung.dokumente`, `Top.dokumente`, `Station.stellungnahmen`).
  There is **no** `StationDokumenteInner` wrapper class.
- **Phase 1 change:** drop the wrapper — `StationDokumenteInner(dok)` → `dok`.
  Affected: `bawue_vorgaenge_scraper.py` (lines 376, 595, 703, 798, 864, 921,
  927 — type hints + the `StationDokumenteInner(dok)` construction at 864) and
  `bawue_beteiligung_scraper.py` (199, 232). The `list[StationDokumenteInner]`
  type hints become `list[Dokument | str]`.
- **The type-alias adapter cannot paper over this** (the symbol won't exist on
  the new side). Phase 0.3 keeps the alias pointing at `openapi_client` so this
  stays compiling until Phase 1; Phase 1 must delete the wrapper usage in the
  same commit that flips the alias.

### `VgIdent.typ` — enum relaxed to `str`
- Old: `typ: VgIdentTyp` (enum). BaWue already passes plain strings
  (`VgIdent(id=..., typ="vorgnr")`, `typ="initdrucks"`), which Pydantic coerces.
- New: `typ: str`. Plain strings pass through unchanged. **No BaWue change required.**

### `TouchedByInner` → `TouchedByItem`
- Confirmed rename (both classes exist on their respective sides).
- **Not used anywhere in BaWue** (`rg TouchedBy src/ tests/` → no hits). No work.

### Verified unchanged (field-for-field) for BaWue usage
`Autor`, `Gremium`, `Parlament`, `Vorgang`, `Sitzung`, `Top`, `Station`
(modulo the `dokumente` union above), `Dokument` (modulo `hash_`).

---

## 4. API-client / endpoint contract diffs (Phase 1.2 / 1.2a)

| Concern | Old (`openapi_client`) | New (`pazufa_corelib.api_client`) |
| ------- | ---------------------- | --------------------------------- |
| Client class | `ApiClient(Configuration)` ctx manager + `CollectorSchnittstellenApi` | `AuthenticatedClient(base_url, token, prefix, auth_header_name, ...)` |
| `vorgang_put` | `api.vorgang_put(str(scraper_id), item)` | `vorgang_put.sync_detailed(client=client, body=item, x_scraper_id=str(scraper_id))` |
| `kal_date_put` | `kal_date_put(x_scraper_id=..., parlament=..., datum=str, sitzung=...)` | `kal_date_put.sync_detailed(parlament, datum, client=client, body=list[Sitzung], x_scraper_id=...)` — `parlament`/`datum` **positional**, `datum` is `datetime.date` (not str), arg renamed `sitzung`→`body` and is a **list** |
| Error on 4xx/5xx | raises `ApiException` with `.status` | returns `Response(parsed=None)` **silently** unless `raise_on_unexpected_status=True` |

Verified signatures (from vendored code):
- `vorgang_put.sync_detailed(*, client, body: Vorgang, x_scraper_id: str)`
- `kal_date_put.sync_detailed(parlament: Parlament, datum: datetime.date, *, client, body: list[Sitzung], x_scraper_id: str)`

This is the rationale for the mandatory `bawue/api.py` status shim (plan §1.2a).

---

## 5. Phase-1 change checklist distilled from this diff

- [ ] Flip `bawue/types.py` aliases to `pazufa_corelib.api_client.models.*`.
- [ ] `_MINUS_` → `` on all enum member references (~75 in src, ~140 in tests).
- [ ] `Dokument(hash=...)` → `Dokument(hash_=...)` (4 src + test fixtures).
- [ ] Remove `StationDokumenteInner(dok)` wrapper → `dok`; retype
      `list[StationDokumenteInner]` → `list[Dokument | str]`.
- [ ] Build `AuthenticatedClient`; port endpoint calls + §1.2a status shim.
- [ ] Cache-format migration (`vg:`→`vg2:`) in Phase 2 once cache is ported.
