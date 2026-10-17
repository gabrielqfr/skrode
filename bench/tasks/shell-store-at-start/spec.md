# The dashboard hears the desktop shell's events from app start

## Goal
In the desktop app, the dashboard keeps the shell's state in a store made by `storeOfGlobal()` in `web/src/bridge.ts`, and the store subscribes to the shell's change events when it is made. Today it is first made on the first `useShell()` call, so an event the shell publishes before any page reads the shell (an update status at startup) is lost until a reload. Make the store when `bridge.ts` loads.

## Files to touch
- `web/src/bridge.ts`
- `web/src/bridge.test.ts`

## Functions to add or change
In `web/src/bridge.ts`:
- New exported `startShellStore()`, placed right after `storeOfGlobal()`: returns `storeOfGlobal()`. Doc comment: makes the dashboard's store now, so it hears change events from app start, not from the first page that reads the shell.
- Call `startShellStore()` once at module level, right after its definition.
- Update the doc comments of `shellStore` and `useShell` to say the store is made at start (`startShellStore`), not at the first `useShell()`.
- `storeOfGlobal`, `shellStore` and `useShell` otherwise stay as they are.

## Tests to add
In `web/src/bridge.test.ts` (import `vi` from vitest too), a new `describe('the dashboard\'s store', ...)` with one test, `hears an event published before the first store read, from module load`: with the file's own `fake(settings('1.0.0'))` helper, put `raw` on `globalThis.senseiTopShell`; `vi.resetModules()`; `const fresh = await import('./bridge')`; `emit(settings('1.0.4'))`; expect `fresh.startShellStore().snapshot()?.app?.settings.version` to be `'1.0.4'`. Delete `globalThis.senseiTopShell` in a `finally`.

## Commands that must pass
- `pnpm --dir web test`
- `pnpm --dir web exec tsc -b`
- `pnpm --dir web lint`

## Out of scope
The desktop shell (`desktop/`), the `Shell` type, any page component.

## Open questions
None.
