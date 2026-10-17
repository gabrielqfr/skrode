# Say so when the watcher stopped after starting

## Goal
`/api/live` already reports `watcher_stopped: true` when the watcher process started and then died on its own, but the dashboard ignores it: such a tracker reads `watching: false`, so the status pill says "Not watching" and Live and Home tell the user to turn watching on, which is wrong advice. Add a "Watcher stopped" state to the status pill, a callout on the Live page above every branch (a match in progress included), and the same sentence in Home's first-run step, all telling the user to restart the tracker.

## Files to touch
- `web/src/lib/home.ts`
- `web/src/lib/home.test.ts`
- `web/src/shell/LiveStatus.tsx`
- `web/src/views/CurrentMatchView.tsx`
- `web/src/components/home/FirstRun.tsx`

## Functions to add or change
- `web/src/lib/home.ts`, after `NOT_WATCHING_SENTENCE`:
  - `export const WATCHER_STOPPED_SENTENCE = "The tracker stopped watching Arena's log after it started, so a match played now is not recorded. Restart the tracker; its log file says what went wrong."`
  - `export function watcherCallout(live: { watching: boolean; watcher_stopped?: boolean } | undefined): string | null` — `null` when `live` is undefined; `WATCHER_STOPPED_SENTENCE` when `watcher_stopped`; else `null` when watching, `NOT_WATCHING_SENTENCE` when not. Ask the stopped case first.
  - `export type LiveState = 'down' | 'stopped' | 'match' | 'watching' | 'idle'` and `export function liveState(input: { isError: boolean; watcherStopped: boolean; matchInProgress: boolean; watching: boolean }): LiveState` — the first that holds, in that order: `isError` → down, `watcherStopped` → stopped, `matchInProgress` → match, `watching` → watching, else idle.
- `web/src/shell/LiveStatus.tsx`: replace the local `State` type with `LiveState` from `lib/home`; add `stopped: 'Watcher stopped'` to `WORDS` and `stopped: 'bg-warn'` to `DOT`; compute the state with `liveState(...)` (`watcherStopped: Boolean(live.data?.watcher_stopped)`); in the panel, when the state is `stopped` show `WATCHER_STOPPED_SENTENCE` instead of the watching / not-watching paragraph.
- `web/src/views/CurrentMatchView.tsx`: import `watcherCallout` and `WATCHER_STOPPED_SENTENCE` instead of `NOT_WATCHING_SENTENCE`. The no-match branch's callout uses `watcherCallout(live.data)`. Every other branch (opening hand, intermission, the match itself) shows `<div className="shrink-0"><Callout tone="warn">{WATCHER_STOPPED_SENTENCE}</Callout></div>` first when `live.data?.watcher_stopped`.
- `web/src/components/home/FirstRun.tsx`: the first step's detail is `watcherCallout(live) ?? NOT_WATCHING_SENTENCE` (turn the `STEPS` constant into a function of `live`).

## Tests to add
In `web/src/lib/home.test.ts` (import `liveState`, `NOT_WATCHING_SENTENCE`, `WATCHER_STOPPED_SENTENCE`, `watcherCallout`):
- `describe('the pill state')`: a failed heartbeat beats a stopped watcher (`down`); a stopped watcher beats a match in progress (`stopped`); then match, watching, idle.
- `describe('the watcher callout')`: `{ watching: false, watcher_stopped: true }` gives the stopped sentence; `{ watching: false, watcher_stopped: false }` the not-watching one; watching and `undefined` give `null`.

## Commands that must pass
- `pnpm --dir web test`
- `pnpm --dir web exec tsc -b`
- `pnpm --dir web lint`

## Out of scope
The backend (`watcher_stopped` already exists in `/api/live` and `web/src/types/api.ts`), automatic restart, docs.

## Open questions
None.
