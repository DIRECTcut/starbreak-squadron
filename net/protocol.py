"""Wire protocol: newline-delimited JSON over TCP.

Host is authoritative. Clients send inputs; host sends full snapshots.
Sizes are tuned for a 1920x1080 screen at 60 fps sim / 30 Hz snapshots:
positions are int pixels, velocities 0.1 px/s, angles 0.01 rad.

C -> S:
  join:  {"k":"join", "name":"Pilot"}
  in:    {"k":"in", "mv":[x,y], "wp":0|1|2|-1}      (wp = weapon switch or -1)
  bye:   {"k":"bye"}

S -> C:
  roster: {"k":"roster", "pid":1, "names":["You","Pilot"], "wname":["H","P"]}
  snap:   {"k":"snap", ...world state...}
  bye:    {"k":"bye", "reason": "..."}
"""
import json

SNAP_HZ = 30
MAX_REMOTE = 3
DEFAULT_PORT = 7747


def encode(msg):
    """Serialize one message as a complete wire line."""
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")


# canonical enemy kind order (host and client import the same list)
KINDS = ["scout", "gunner", "brute", "dart"]


def build_snapshot(game, seq):
    """Full world state snapshot. Positions in int px, hp/vel 0.1, angles 0.01.

    Player rows are in pid order (index 0 = host seat). fx is drained from
    game.fx so each event is delivered exactly once (the 30 Hz tick that
    drains it is the only place these lists are popped).
    """
    r1 = lambda v: round(float(v), 1)
    r2 = lambda v: round(float(v), 2)
    ri = lambda v: int(v)

    pl = []
    for p in game.players:
        pl.append([ri(p.pid),
                   ri(p.pos.x), ri(p.pos.y), ri(p.vel.x), ri(p.vel.y),
                   r1(p.shield), r1(p.armor), r1(p.hull),
                   1 if p.alive else 0, 1 if p.downed else 0,
                   r2(p.revive_progress), ri(p.weapon_idx), r2(p.heading),
                   p.name, ri(p.score), ri(p.kills), ri(p.shots_fired),
                   ri(p.shots_hit), r2(p.hit_flash), r2(p.shield_timer),
                   ri(p.aim_id) if getattr(p, "aim_id", None) is not None else -1,
                   1 if getattr(p, "target_manual", False) else 0])

    en = []
    for e in game.enemies:
        try:
            ki = KINDS.index(e.kind)
        except ValueError:
            ki = 0
        en.append([ri(e.id), ki, ri(e.pos.x), ri(e.pos.y), r1(e.hp), r1(e.max_hp),
                   r2(e.angle), 1 if e.hit_flash > 0.05 else 0])

    from weapons import WEAPONS
    bl = []
    for b in game.bolts:
        try:
            slot = WEAPONS.index(b.spec)
        except ValueError:
            slot = 3  # generic (enemy) bolt: client colors from the row
        c = b.spec.color
        bl.append([ri(b.id), b.owner_pid if b.friendly else -1, slot,
                   ri(b.pos.x), ri(b.pos.y), ri(b.vel.x), ri(b.vel.y),
                   ri(b.radius), c[0], c[1], c[2]])

    od = [[ri(o.pos.x), ri(o.pos.y), ri(o.value)] for o in game.orbs]

    fx = list(game.fx)
    game.fx = []

    ann = game.announce_text if (game.announce_t > 0 and game.announce_text) else None
    return {
        "k": "snap", "seq": seq, "t": r2(game.t),
        "rs": game.run_id, "st": game.state,
        "w": game.waves.wave, "sc": [game.score, game.kills],
        "pl": pl, "en": en, "bl": bl, "or": od, "fx": fx,
        "an": [ann, r2(max(0.0, game.announce_t))],
        "shk": r2(game.shake_t),
    }


class LineBuffer:
    """Incremental newline-delimited message decoder for socket streams."""

    def __init__(self):
        self._buf = b""

    def feed(self, data):
        self._buf += data
        out = []
        while True:
            i = self._buf.find(b"\n")
            if i == -1:
                break
            line, self._buf = self._buf[:i], self._buf[i + 1:]
            if line.strip():
                try:
                    out.append(json.loads(line.decode("utf-8")))
                except (ValueError, UnicodeDecodeError):
                    pass  # half-joined or corrupt line: drop, resync on next line
        return out