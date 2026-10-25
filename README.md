# Astral Vector

A top-down spaceflight shooter for PC, inspired by **Everspace 2**:
inertial flight, auto-locking weapons, ships with simple but distinct AI, and a
**shield → armor → hull** layered damage model with delayed shield regeneration.

Plays **solo** or as **LAN co-op** (2–4 ships, host-authoritative, stdlib only —
no new dependencies). A dead pilot becomes a downed beacon a teammate can
channel-revive; the run ends only when *every* ship is down.

Built with **Python 3.12 + pygame** (procedural audio via numpy; no asset files).

## Run it

```bash
cd game
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

Or with uv:

```bash
cd game
uv venv --python 3.12 && uv pip install -r requirements.txt
.venv/bin/python main.py
```

### LAN co-op (2–4 players, same network)

One machine hosts and runs the authoritative sim; the others connect as
co-pilots. Host-authoritative with 30 Hz full-state snapshots and 60 Hz
client inputs — plain TCP + newline-delimited JSON, stdlib only.

```bash
# host (the sim). --auto-start skips the ENTER wait.
.venv/bin/python main.py --host --port 7747 --name "Hawk"
# co-pilot, from another machine on the same LAN (use the IP the host printed):
.venv/bin/python main.py --client <HOST_IP> --port 7747 --name "Wren"
```

Each player controls their own ship (WASD + auto-lock weapons, `1/2/3` to
swap). The host's own ship is a normal, human-piloted ship — no host
privilege. Leaving the table drops a pilot's ship (it becomes a downed
beacon the rest of the team can still revive); the freed seat can be taken by
a new joiner. `CMD/CTRL+Q` (or the window close) leaves cleanly.

The network layer is covered by `tests/test_net.py` (loopback host + real
sockets) and by a two-process bot soak over 127.0.0.1:

```bash
AUTOQUIT=20 ./scripts/soak_lan.sh   # host bot + client bot, no exceptions, screenshots
```

### Controls

| Key | Action |
| --- | --- |
| `WASD` / arrows | flight (inertial thrust, drift, momentum) |
| auto | weapons auto-lock and fire on the nearest ship (Everspace-style) |
| `1` | **PULSE-7** — rapid, accurate beam |
| `2` | **SCATTER-V** — 5-pellet blast cone |
| `3` | **HOMER-X** — homing missile with AoE splash |
| `[` / `]` | **manual target lock** — pin, then step to previous / next nearest |
| `T` | back to auto-lock (nearest ship) |
| `P` / `Esc` | pause |
| `R` | restart (in flight or on game over) |

## Gameplay

- **Defense layers:** bullets eat your **shield** first (no overshoot), then
  **armor**, then **hull**. Shield regenerates ~4 s after your last hit.
  Salvage orbs you pick up from kills score points *and* partially repair
  shield → armor → hull.
- **Co-op revive (fleet, not solo, decides the run):** when a ship is
  destroyed it is *downed*, not gone — a beacon with a channel ring. A
  teammate standing within range auto-channels the revive (~3 s); getting hit
  resets the channel. The revived ship returns at half hull, no shield/armor.
  The run only ends when **all** pilots are down ("FLEET DOWN").
- **Enemies (simple logic AIs):**
  - *Scout* — aggressive, closes in, strafes on a drifting orbit, single shots.
  - *Gunner* — holds mid-range, orbits, fires 3-round led bursts (fast bolts).
  - *Brute* — slow tank, keeps range, 5-shot spread. Leads your predicted
    position when firing.
  - *Dart* (wave 2+) — fast interceptor that hunts your **predicted** position
    and rams for heavy contact damage. No bolts. Exists to break the
    "strafe in circles, take no damage" meta.
- **Waves** scale in count, HP, speed and damage; enemies spawn staggered
  from off-screen edges.
- **Targeting:** weapons auto-lock the **nearest** ship in range (default).
  `[` / `]` pin a manual lock — the first press pins the ship you were already
  auto-aiming (no jump), subsequent presses step through the nearest ships;
  a locked target that dies hands its rank to whatever becomes nearest in
  that slot; `T` returns to auto. Manual locks render gold so co-op players
  can tell them apart. In LAN play the same ops ride over the wire (`t` field,
  one-shot like weapon switches).
- Score, kill feed, wave announcements, damage flash + screen shake,
  explosions, missile trails, salvage magnetism, low-hull warning pulse.

## Testing / iteration

The project ships with three layers of testing so changes can be verified
headlessly (no display required):

```bash
# 1) Unit + integration tests (33 tests: damage pipeline, regen timing,
#    targeting, homing, waves, economy, render smoke tests, key event
#    handling, silent-audio-regression checks, co-op revive mechanics,
#    and LAN protocol loopback tests)
.venv/bin/python -m unittest discover -s tests

# 2) Headless "autopilot" soak test: a bot flies the ship, cycles all three
#    weapons, fights for N frames; verifies spawning, combat both ways,
#    salvage economy and wave progression, then passes/fails with stats
SDL_VIDEODRIVER=dummy .venv/bin/python main.py --selftest 3000 --seed 1 \
    --screenshot check.png

# 3) LAN bot soak: real host + client processes over 127.0.0.1 (bot co-pilots),
#    asserts no exceptions and that both windows render a screenshot
AUTOQUIT=20 ./scripts/soak_lan.sh
```

All three were used during development to validate balance and catch
regressions (defense-pipeline tests assert exact shield/armor/hull handoffs;
a regression test pins that audio builds all 11 sounds instead of failing
silently; the LAN loopback test drives a real host + socket client through
join → roster → input → snapshot → drop → re-seat).

### Session logging (the playtest → tuning loop)

Every human run and bot probe writes to `sessions/`:
- `S-<stamp>-<mode>.jsonl` — event log: `open`, 5 Hz `sample` rows
  (player levels/pos, enemies, drained counters: near-misses, shot counts,
  salvage), plus `hit` (which layer absorbed it), `kill` (kind, TTM,
  damage-to-kill), `wave`, `weapon`, `death` rows.
- `S-<stamp>-<mode>.summary.json` — derived metrics for analysis:
  dps in/out, damage absorbed per layer, minimum defense levels over time,
  fraction of time hull < 50, **player accuracy**, **dodge rate**
  (near-misses vs hits taken), weapon time share, wave-clear pacing.

Difficulty probes (run headless, used to gate any tuning change):

```bash
# no-aim probe: a stationary player must die in ~10-45 s (currently ~16 s)
python main.py --selftest 3600 --bot steady --seed 1
# dodging player proxy: should survive with visible defense attrition
python main.py --selftest 3600 --bot weave --seed 1
```

Tuning protocol: change numbers in `config.py` / `enemies.py` / `weapons.py`
→ run the full test suite (regression gate) → run both probes (difficulty
gate) → optionally replaytest and diff the session summaries
(`telemetry.latest_session_summaries(dir)` loads the last N for an agent).

## Layout

| File | Purpose |
| --- | --- |
| `main.py` | CLI entry: play (`python main.py`) or self-test (`--selftest`) |
| `game.py` | `Game`: states (menu/pause/over), update, render, self-test harness |
| `player.py` | ship: inertial flight, autofire, shield/armor/hull, salvage repair |
| `enemies.py` | 3 enemy AIs + firing state machines |
| `weapons.py` | weapon definitions |
| `entities.py` | bolts (incl. homing), particles, salvage orbs |
| `waves.py` | wave composition + staggered spawning |
| `stars.py` | parallax starfield + nebula background |
| `hud.py` | HUD / menu / pause / game-over screens, per-pilot name tags |
| `audio.py` | procedural SFX (guarded; game still runs with no audio device) |
| `config.py` | tunables (incl. co-op/revive constants) |
| `telemetry.py` | session logging (per-pilot vitals) |
| `net/` | LAN layer — `protocol.py` (NDJSON + snapshots), `host.py` (authoritative server), `client.py` (render-only co-pilot) |
| `scripts/soak_lan.sh` | two-process LAN bot soak |
| `tests/` | unittest suite (game, audio, telemetry, net) |
