# RCQ chips: read 14 days, sum a key's labels, the long tail under "Other"

## Goal
On the RCQ page, the "expected archetypes" chips have three problems. They read the field since the last format reset (or a year), when a store's players netdeck from the last two weeks. When one archetype key has several labels at the chosen tier (Azorius Control and Jeskai Control are both key `Control`), the key's share is whichever row came last, not their sum. And every archetype gets a chip, so the long tail buries the main field. Fix all three.

## Files to touch
- `web/src/lib/rcq.ts`
- `web/src/lib/rcq.test.ts`
- `web/src/components/rcq/InputsCard.tsx`
- `web/src/views/RcqView.tsx`

## Functions to add or change
- `web/src/lib/rcq.ts`:
  - `export const MAIN_SHARE = 0.02`, next to `ExpectedOption`: a chip at or above this share is shown up front.
  - `expectedOptions(presence)`: per key, collect the set of labels and sum the `share` of every row at the chosen tier (`(entry.share ?? 0) + row.share`); a key with one label is named by it, a key with several by the key itself. Tier choice, `decks` totals and sorting stay as they are. Update the doc comment.
  - New `export function splitOptions(options: ExpectedOption[]): { main: ExpectedOption[]; other: ExpectedOption[] }`: `main` is options with a non-null share `>= MAIN_SHARE`, `other` the rest (order kept).
- `web/src/components/rcq/InputsCard.tsx`: with `useState`, a `showOther` flag (default false). Chips are `main`, then those `other` options that are shown (`showOther`) or currently picked (`expected.has(key)`), then the existing `missing` ones. When some `other` options are hidden (not picked), add a button after the chips: `type="button"`, `aria-expanded={showOther}`, text `Other (N)` with N the hidden count, or `Fewer` when expanded; style it like the chips (min height 44px, pill, border).
- `web/src/views/RcqView.tsx`: drop `useMetagameStatus` and the reset-based window; add `const CHIP_WINDOW_DAYS = 14` and use `{ days: CHIP_WINDOW_DAYS }` as the `MetaWindow` for `useMetaPresence`. The share note becomes: `` `A chip's share is of the Tier ${tier} decks of the last ${CHIP_WINDOW_DAYS} days; the smaller archetypes are under "Other".` ``

## Tests to add
In `web/src/lib/rcq.test.ts` (import `MAIN_SHARE` and `splitOptions`):
- under `expectedOptions`: `sums every label of one key at the tier, named by the key` — rows `('Control','Azorius Control','B',21,0.3)`, `('Control','Jeskai Control','B',21,0.3)`, `('UBMidrange','Dimir Midrange','B',28,0.4)` with the file's `presence()` helper give `[{ key: 'Control', label: 'Control', share: 0.6 }, { key: 'UBMidrange', label: 'Dimir Midrange', share: 0.4 }]`;
- `describe('splitOptions')`: shares 0.5, `MAIN_SHARE`, 0.01 and null give main `['a','b']`, other `['c','d']`.

## Commands that must pass
- `pnpm --dir web test`
- `pnpm --dir web exec tsc -b`
- `pnpm --dir web lint`

## Out of scope
The backend's RCQ routes, the guide's own field window, docs.

## Open questions
None.
