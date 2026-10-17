# A Content-Security-Policy on every dashboard response

## Goal
The dashboard (the built web app served by `DashboardFiles` in `src/sensei_top/api/dashboard.py`) sends no Content-Security-Policy of its own. Add one to every response `DashboardFiles` returns: pages, the `index.html` fallback, and assets. API responses under `/api` must not get it. `LoopbackGuard` already adds a separate frame-ancestors-only CSP header to every response; leave it alone, so dashboard responses carry two CSP headers.

## Files to touch
- `src/sensei_top/api/dashboard.py`
- `tests/test_dashboard.py`

## Functions to add or change
In `src/sensei_top/api/dashboard.py`:
- Module constant `CONTENT_SECURITY_POLICY`, placed before `_SEPARATORS`, exactly:
  `"default-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; img-src 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"`
  with a comment giving the reason for each directive: React `style={...}` props need `'unsafe-inline'` for styles, no script is inline, the CSS inlines woff2 fonts as `data:`, card images come from the API's own routes.
- `DashboardFiles.get_response(path, scope)`: move its current body unchanged into a new `async def _answer(self, path, scope) -> Response`; `get_response` calls it, sets `response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY` and returns it. Keep the docstring on `get_response`.

## Tests to add
In `tests/test_dashboard.py`, with its existing `client` fixture:
- `test_dashboard_responses_carry_a_csp`, parametrized over `/`, `/matches/abc`, `/assets/app.js`: status 200 and `CONTENT_SECURITY_POLICY in response.headers.get_list("content-security-policy")`;
- `test_the_api_carries_no_dashboard_csp`: `GET /api/health` is 200 and no `content-security-policy` value contains `default-src`.

## Commands that must pass
- `uv run pytest -q tests/test_dashboard.py`
- `uv run ruff check .`

## Out of scope
`api/guard.py`, the API routes, the web build, docs.

## Open questions
None.
