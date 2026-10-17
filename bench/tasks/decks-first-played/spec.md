# /api/decks leaves out zero-match decks and carries first_played

## Goal
`/api/decks` lists every row of `v_deck_summary`, including MTGO decks (from the store or an export) that no match is picked to yet. Those zero-match decks reach the deck dropdown and deck pages as dead ends; the MTGO picker reads its own candidates route, so it does not need them here. Leave them out. Also add `first_played`, the deck's earliest match start: a version's date range in the frontend starts at `first_seen`, which for an MTGO deck is the day the file was saved, not the day it was first played.

## Files to touch
- `src/sensei_top/api/models/common.py`
- `src/sensei_top/api/routes/meta.py`
- `tests/test_api.py`
- `web/src/types/api.ts`
- `web/src/lib/deckOverview.ts`
- `web/src/lib/deckOverview.test.ts`

## Functions to add or change
- `src/sensei_top/api/models/common.py`, `class Deck`: add `first_played: str | None = Field(None, description="when the deck's earliest match started, across every source")` between `last_played` and `first_seen`.
- `src/sensei_top/api/routes/meta.py`, `decks(conn)`: one query. A CTE `first_played` = `SELECT md.deck_id, MIN(m.started_at) AS first_played FROM matches m JOIN v_match_deck md ON md.match_id = m.match_id GROUP BY md.deck_id`; select today's columns from `v_deck_summary s` plus `fp.first_played` (`LEFT JOIN first_played fp ON fp.deck_id = s.deck_id`), `WHERE s.matches_played > 0`, same `ORDER BY COALESCE(s.last_played, s.last_seen, '') DESC`. Use one grouped pass, not a per-deck subquery: the view's COALESCE has no index. Update the docstring.
- `web/src/types/api.ts`: regenerate with `pnpm --dir web gen:types`; do not edit by hand.
- `web/src/lib/deckOverview.ts`, `versionDays(deck, now)`: takes `Pick<Deck, 'first_played' | 'first_seen' | 'last_played'>` and starts from `deck.first_played ?? deck.first_seen`. Update its doc comment.

## Tests to add
- `tests/test_api.py`, after `test_decks_carry_the_context_the_selector_shows`, using the file's existing `client`, `mtgo_pick_client`, `mtgo_pick_db`, `make_client`, `_pick_decks`, `MTGO_PICK_A`, `MTGO_PICK_B`:
  - `test_a_decks_first_played_is_its_earliest_match_start`: the one deck's `first_played` is `"2026-08-13T14:49:19"` and is before `last_played`;
  - `test_decks_leave_out_one_no_match_is_picked_to`: `/api/health` reports 4 decks, but `/api/decks` returns only `Spellelementals`, with matches;
  - `test_a_picked_mtgo_deck_appears_and_its_range_starts_at_its_first_match`: pick both MTGO matches to the `Sample` deck via `PUT /api/matches/{id}/mtgo-pick`; `/api/decks` has 2 decks; Sample's `matches_played == 2`, `first_played == "2099-01-01T00:00:00"`, `last_played == "2099-01-02T00:00:00"`, and `first_seen` is set and differs from `first_played`.
- `web/src/lib/deckOverview.test.ts`: the `version()` helper sets `first_played: null`; add under `versionDays`: `starts at the first played match, not the day the deck was saved` (first_seen Sep 1, first_played Sep 10, last_played Sep 14 → `'Sep 10 to 14'`) and `falls back to first_seen when first_played is null` (→ `'Sep 1 to 14'`).

## Commands that must pass
- `uv run pytest -q tests/test_api.py`
- `uv run ruff check .`
- `pnpm --dir web gen:types`
- `pnpm --dir web test`
- `pnpm --dir web exec tsc -b`
- `pnpm --dir web lint`

## Out of scope
The MTGO candidates route, `v_deck_summary` and other views, docs.

## Open questions
None.
