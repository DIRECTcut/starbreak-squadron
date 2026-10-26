# Development workflow

Use an iterative implement → test → playtest → refine loop. Do not
treat implementation alone as completion.

## Before implementation
- Inspect Git status; preserve unrelated work.
- Define observable acceptance criteria.
- For bugs, reproduce the reported behavior before changing code.
  If reproduction fails, report that; do not make speculative fixes.

## Implementation and automated verification
- Make focused changes and add regression tests.
- Run the full unit/integration suite.
- Run deterministic headless gameplay probes for gameplay changes.
- Run host/client loopback tests and LAN bot soaks for multiplayer changes.
- For world-size or lifetime changes, test extended travel, bounded
  entity counts, memory growth, and crashes.

## Visual and human playtesting
- Exercise affected behavior in a real game window.
- Check relevant renders/screenshots; state exactly what was verified.
- Distinguish automated bot/render checks from human playtesting.
- Provide concise human playtest instructions. Never claim human
  playtesting or subjective gameplay quality without supporting feedback.

## Refine using evidence
- Use play-session telemetry and human feedback to guide tuning.
- Compare relevant metrics before and after changes, using consistent
  seeds/settings where feasible.
- Repeat implementation and verification until acceptance criteria pass.
- If a human playtest finds a missed bug, add regression coverage.

## Handoff
- Report changes, checks performed, results, and remaining limitations.
- Commit completed, verified work when authorized.
- Push only with explicit authorization.
