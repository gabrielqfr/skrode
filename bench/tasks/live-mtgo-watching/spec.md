# The Live status and Settings say MTGO is watched, not read once

## Goal
The watcher now stores each MTGO game once the game ends, but `/api/live` still names its MTGO flag `mtgo_read_at_startup`, and the Live status panel and the Settings hint still say MTGO is read once when the app starts and a later match appears after a restart. Rename the field to `mtgo_watching` and correct both texts.

## Files to touch
- `src/sensei_top/api/models/common.py`
- `src/sensei_top/api/routes/meta.py`
- `tests/test_api.py`
- `web/src/types/api.ts`
- `web/src/shell/LiveStatus.tsx`
- `web/src/components/settings/SourcesSection.tsx`

## Functions to add or change
- `src/sensei_top/api/models/common.py`, `class LiveStatus`: rename `mtgo_read_at_startup` to `mtgo_watching`, default `False`, description `"the running watcher has an MTGO game-log directory and stores each game from it once the game ends"`. In the class docstring, say `watching` and `mtgo_watching` are both asked of the watcher object directly (`is_alive()`, and the MTGO directory it was started with).
- `src/sensei_top/api/routes/meta.py`, `live()`: pass `mtgo_watching=bool(getattr(watcher, "mtgo_dir", None))`.
- `web/src/types/api.ts`: regenerate with `pnpm --dir web gen:types`; do not edit by hand.
- `web/src/shell/LiveStatus.tsx`: the paragraph shown when `watching && live.data?.mtgo_watching` becomes `<p>MTGO is watched too: each game appears once it ends.</p>`.
- `web/src/components/settings/SourcesSection.tsx`, `MagicOnlineCard`: the hint for a set folder becomes `'Each game is read once it ends.'`.
- Nothing in `src/` or `web/src/` may mention `mtgo_read_at_startup` afterwards.

## Tests to add
In `tests/test_api.py`:
- `test_live_status_reports_counts_without_a_watcher`: also assert `body["mtgo_watching"] is False`;
- `test_live_reports_an_attached_watcher`: after `watching` is True, assert `mtgo_watching` is False (no MTGO directory);
- new `test_live_reports_mtgo_watching_when_the_watcher_has_a_directory(tmp_path, make_client)`: a `sensei_top.live.LiveIngester(db_path=..., log_path=tmp_path / "Player.log", mtgo_dir=tmp_path / "mtgo")` on a fresh database, attached with `create_app(db_path, watcher=watcher)`, `watcher.stats.running = True`; `/api/live` has `watching` and `mtgo_watching` both True.

## Commands that must pass
- `uv run pytest -q tests/test_api.py`
- `uv run ruff check .`
- `pnpm --dir web gen:types`
- `pnpm --dir web exec tsc -b`
- `pnpm --dir web lint`

## Out of scope
The watcher itself (`live.py`), the MTGO reader, docs.

## Open questions
None.
