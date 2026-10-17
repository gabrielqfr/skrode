# Drop TopDeck events that are not Standard constructed

## Goal
TopDeck.gg organisers label Commander nights, drafts, sealed and prerelease events "Standard", so `refresh_topdeck` stores them as Standard constructed events and they pollute the metagame. Skip such events on fetch, by their tournament name, and purge any already stored on every refresh.

## Files to touch
- `src/sensei_top/metagame/topdeck.py`
- `tests/test_metagame_topdeck.py`

## Functions to add or change
In `src/sensei_top/metagame/topdeck.py` (`re`, `log` and the `store_events` import already exist there):
- Module constant `_NOT_CONSTRUCTED_RE`, compiled with `re.IGNORECASE`: `r"(?<![a-z])(commander|edh|draft|sealed|cube|jumpstart|pre.?r?e?a?l)"`. Leading boundary only, so "Pre Release", "Prerelease" and the misspelt "PreRealese" all match. Put it after `MAX_AGE_SECONDS`.
- New `_purge_not_constructed(conn) -> None`: select `event_id, name` from `meta_event` where `event_id LIKE 'Tournaments/Topdeck/%'`; for each whose `name` matches the regex, in one transaction (`BEGIN` … `COMMIT`, `ROLLBACK` and re-raise on error, as `refresh_topdeck` does for its own write): set `duplicate_of = NULL` on rows of `meta_event` pointing at it, delete its rows from `meta_deck_archetype`, `meta_round_match` and `meta_deck` (by `event_id`), then from `meta_event`. Log one warning with the count. Do nothing when none match.
- `refresh_topdeck(...)`: in the loop over tournaments, after the existing "no TID" skip, skip a tournament whose `str(tournament.get("tournamentName") or "")` matches the regex: log a warning and add one to `skipped`. After `store_events(conn, events)`, call `_purge_not_constructed(conn)`.

## Tests to add
In `tests/test_metagame_topdeck.py`, with the file's existing `_TOURNAMENT` dict and `conn` fixture:
- helper `_named(tid, name)` returning `{**_TOURNAMENT, "TID": tid, "tournamentName": name}`;
- `test_non_constructed_names_are_skipped_and_std_is_kept`: with `TOPDECK_API_KEY` set and `topdeck.list_tournaments` patched to return "Commander night" (t1), "Pre release" (t2) and "Torneo magic std" (t3), `refresh_topdeck(conn, force=True)` returns `events == 1` and `skipped == 2`, and `meta_event` holds only `Tournaments/Topdeck/t3.json`;
- `test_a_stored_non_constructed_event_is_purged_with_its_rows`: store an event named "fractured pre release" directly (`topdeck._normalize`, `source.parse_event`, `TournamentFile("Tournaments/Topdeck/old.json", "x")`, `store_events`), then refresh with an empty tournament list; `meta_event`, `meta_deck`, `meta_round_match` and `meta_deck_archetype` are all empty.

## Commands that must pass
- `uv run pytest -q tests/test_metagame_topdeck.py`
- `uv run ruff check .`

## Out of scope
Other metagame sources, the fetch hold table, docs.

## Open questions
None.
