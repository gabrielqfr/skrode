# The CLI report labels rows with no deck

## Goal
`sensei-top report` crashes on a database holding an MTGO match that no deck is picked to. Such a match has no `deck_id`, so each deck section's label `COALESCE(name, substr(deck_id, 1, 8))` is `NULL`, and the play/draw section fails when it formats or slices that label (game length would fail next). Every deck section must label such rows `(no deck)` instead, and the report must run to the end.

## Files to touch
- `src/sensei_top/cli.py`
- `tests/test_cli_report.py`

## Functions to add or change
- `cmd_report(args)` in `src/sensei_top/cli.py`: it has four SQL queries that build a `deck` label with `COALESCE(<name>, substr(<deck_id>, 1, 8))` — the deck win-rate section (`v_deck_winrate`), play/draw (`v_play_draw_winrate`), game length (`v_deck_game_length`) and the per-turn averages section. Add `'(no deck)'` as the last argument of each of the four `COALESCE` calls. Change nothing else in the function; the print formats stay as they are.

## Tests to add
New file `tests/test_cli_report.py`. A helper `_report(monkeypatch, tmp_path, capsys) -> str`:
- sets `XDG_CONFIG_HOME` to `tmp_path / "config"` and `MTGO_HANDLE` to `Player1`;
- copies `tests/fixtures/mtgo_match_session.log` into a new `tmp_path / "share"` directory as `Match_GameLog_f2244feb-54e1-48ec-b3c5-711b20844adf.dat`;
- opens `tmp_path / "t.db"` with `sensei_top.db.open_database`, runs `sensei_top.reader.mtgo.ingest_mtgo_dir(conn, share)` and `sensei_top.projector.matches.project(conn)`, closes it, clears capsys;
- runs `cli.build_parser().parse_args(["--db", str(db_path), "report"])` and `args.func(args)`, returns captured stdout.

Tests (the fixture's two games: Player1 on the play in game 1, on the draw in game 2, wins both, 9 and 8 turns):
- `test_play_draw_labels_a_match_with_no_deck`: output contains `f"{'(no deck)':<22} {'on the play':<12} 1/1 games 100.0%"` and the same for `on the draw`.
- `test_game_length_labels_a_match_with_no_deck`: output contains `f"{'(no deck)':<22} turn   8   1/1 games 100.0%"`, the same for `turn   9`, and `f"{'(no deck)':<22} avg length 8.5 turns (2 games)"`.

## Commands that must pass
- `uv run pytest -q tests/test_cli_report.py`
- `uv run ruff check .`

## Out of scope
The report's other sections and formats, the projector, the views in migrations, docs.

## Open questions
None.
