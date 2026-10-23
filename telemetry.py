"""Session telemetry: capture playtests as JSONL events + a derived summary.

One session = one continuous play (menu -> ... -> game over / quit).
Events are appended as they happen; state samples at SAMPLE_HZ. `close()`
flushes two files into SESSIONS_DIR:

    S-<UTCstamp>-<mode>.jsonl        # open/sample/hit/kill/wave/weapon/down/revive rows
    S-<UTCstamp>-<mode>.summary.json # difficulty/strain metrics + per-pilot standings

Kept deliberately dependency-free and cheap (a few small dicts per tick).
"""
import json
import os
import time
from datetime import datetime, timezone


class Session:
    def __init__(self, dirpath, mode, meta=None):
        self.dirpath = dirpath
        self.mode = mode            # "play" | "bot-weave" | "bot-a-b" | "coop"
        self.meta = meta or {}
        self.rows = []
        self.t = 0.0
        self.wall0 = time.time()
        self._wave_times = [0.0]
        self._closed = False

    # ---- capture API (called by Game) ----
    def open(self):
        self.rows.append({"type": "open", "t": 0.0, "wall0": self.wall0, **self.meta})

    def sample(self, game):
        """5 Hz state snapshot; also drains the per-sample counters.

        `ps` is the per-pilot dict (pid -> vitals); `p` is the legacy shape
        for the local (first) pilot, kept for old consumers/tests.
        """
        self.t += game.sess_dt
        comp = {}
        for e in game.enemies:
            comp[e.kind] = comp.get(e.kind, 0) + 1
        c = game.counters
        ps = {}
        for pp in game.players:
            ps[str(pp.pid)] = {
                "x": int(pp.pos.x), "y": int(pp.pos.y),
                "s": round(pp.shield, 1), "a": round(pp.armor, 1), "h": round(pp.hull, 1),
                "v": int(pp.vel.length()), "w": pp.weapon_idx,
                "alive": pp.alive, "downed": pp.downed,
                "rev": round(pp.revive_progress, 2),
            }
        row = {
            "type": "sample",
            "t": round(self.t, 3),
            "p": ps.get("0", {}),
            "ps": ps,
            "f": {"n": len(game.enemies), "c": comp, "b": len(game.bolts)},
            "score": game.score,
            "k": game.kills,
            # counters drained from last sample
            "near": c["near"], "pf": c["pfired"], "ph": c["phit"],
            "ef": c["efired"], "eh": c["ehit"], "orb": c["orbs"],
        }
        for k in c:
            c[k] = 0
        self.rows.append(row)
        return row

    def event(self, type_s, **data):
        data.setdefault("t", round(self.t, 3))
        data["type"] = type_s
        self.rows.append(data)

    def hit(self, layer, after):
        self.event("hit", layer=layer, after=after)

    def kill(self, kind, wave, ttm, dmg_taken, max_hp, pid=None):
        self.event("kill", kind=kind, wave=wave, ttm=round(ttm, 2),
                   dmg=round(dmg_taken, 1), max_hp=int(max_hp), pid=pid)

    def wave(self, n, comp):
        self.event("wave", n=n, comp=comp)
        self._wave_times.append(self.t)

    def weapon(self, idx):
        self.event("weapon", i=idx)

    def death(self, game):
        self.event("death", wave=game.waves.wave, score=game.score, kills=game.kills)

    # ---- close + derived metrics ----
    def close(self, game):
        if self._closed:
            return None
        self._closed = True
        samples = [r for r in self.rows if r["type"] == "sample"]
        summary = self._summary(game, samples)
        path = self._write(summary)
        return path

    def _per_player(self, game, samples):
        """Per-pilot standings: score, kills, accuracy, strain, survivability."""
        out = {}
        for p in game.players:
            k = str(p.pid)
            rows = [r["ps"][k] for r in samples if k in r["ps"]]
            min_levels = {}
            if rows:
                min_levels = {
                    "shield": min(r["s"] for r in rows),
                    "armor": min(r["a"] for r in rows),
                    "hull": min(r["h"] for r in rows),
                }
                live = sum(1 for r in rows if r["alive"]) / len(rows)
            else:
                live = 0.0
            acc = (p.shots_hit / p.shots_fired) if p.shots_fired else None
            out[k] = {
                "name": p.name,
                "score": p.score,
                "kills": p.kills,
                "accuracy": round(acc, 3) if acc is not None else None,
                "damage_in": round(p.total_damage_taken, 1),
                "damage_out": round(p.total_damage_dealt, 1),
                "absorbed": dict(p.absorbed),
                "downs": sum(1 for ev in self._events("down") if ev.get("pid") == p.pid),
                "revives": sum(1 for ev in self._events("revive") if ev.get("pid") == p.pid),
                "min_levels": min_levels,
                "alive_time_frac": round(live, 3),
            }
        return out

    def _kill_rows(self):
        return [r for r in self.rows if r["type"] == "kill"]

    def _events(self, kind_type):
        return [r for r in self.rows if r["type"] == kind_type]

    def _summary(self, game, samples):
        kills_by = {}
        for r in self._kill_rows():
            kills_by[r["kind"]] = kills_by.get(r["kind"], 0) + 1
        # counters still buffered (since last sample)
        c = game.counters
        pf, ph = c.get("pfired", 0), c.get("phit", 0)
        ef, eh = c.get("efired", 0), c.get("ehit", 0)
        near = c.get("near", 0)
        for r in samples:
            pf += r["pf"]; ph += r["ph"]; ef += r["ef"]; eh += r["eh"]; near += r["near"]
        # team strain. The legacy (flat) fields report the LOCAL pilot (pid 0),
        # matching the old single-player shape; per-pilot numbers live in
        # summary["players"][pid].
        p0 = next((p for p in game.players if p.pid == 0), game.players[0])
        rows0 = [r["ps"][str(p0.pid)] for r in samples if str(p0.pid) in r["ps"]]
        if rows0:
            mins = {
                "shield": min(r["s"] for r in rows0),
                "armor": min(r["a"] for r in rows0),
                "hull": min(r["h"] for r in rows0),
            }
            frac_hull_low = sum(1 for r in rows0 if r["h"] < 50) / len(rows0)
            frac_shield_zero = sum(1 for r in rows0 if r["s"] < 5) / len(rows0)
            wt = {str(i): 0 for i in range(3)}
            for r in rows0:
                wt[str(r["w"])] += 1
            weapon_share = {k: round(v / max(1, sum(wt.values())), 3) for k, v in wt.items()}
        else:
            mins, frac_hull_low, frac_shield_zero, weapon_share = {}, 0.0, 0.0, {}
        wave_times = self._wave_times
        wave_gaps = [round(b - a, 1) for a, b in zip(wave_times, wave_times[1:]) if b > a]
        duration = self.t
        dmg_in = sum(p.total_damage_taken for p in game.players)
        dmg_out = sum(p.total_damage_dealt for p in game.players)
        absorbed = {}
        for p in game.players:
            for layer, v in p.absorbed.items():
                absorbed[layer] = absorbed.get(layer, 0.0) + v
        hits = sum(1 for _ in self._events("hit"))
        summary = {
            "mode": self.mode,
            "meta": self.meta,
            "duration_s": round(duration, 1),
            "score": game.score,
            "kills_total": sum(kills_by.values()),
            "killed_by_kind": kills_by,
            "waves_reached": game.waves.wave,
            "wave_clear_gaps_s": wave_gaps,
            "damage_in": round(dmg_in, 1),
            "dps_in": round(dmg_in / duration, 3) if duration > 0.5 else None,
            "damage_out": round(dmg_out, 1),
            "dps_out": round(dmg_out / duration, 3) if duration > 0.5 else None,
            "absorbed_by_layer": absorbed,
            "min_levels": mins,
            "frac_time_hull_below_50": round(frac_hull_low, 3),
            "frac_time_shield_below_5": round(frac_shield_zero, 3),
            "hits_taken_events": hits,
            "shot_stats": {
                "player_fired": pf, "player_hit_enemies": ph,
                "enemy_fired": ef, "enemy_hit_player": eh,
                "near_misses": near,
                "player_accuracy": round(ph / pf, 3) if pf else None,
                "dodge_rate": round((near) / (near + eh), 3) if (near + eh) else None,
            },
            "weapon_time_share": weapon_share,
            "salvage_collected": game.stats["orbs_collected"],
            "died": not any(p.alive for p in game.players),
            "restarts": game.stats["restarts"],
            "players": self._per_player(game, samples),
        }
        return summary

    def _write(self, summary):
        os.makedirs(self.dirpath, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        base = os.path.join(self.dirpath, f"S-{stamp}-{self.mode}")
        with open(base + ".jsonl", "w") as f:
            for row in self.rows:
                f.write(json.dumps(row) + "\n")
        with open(base + ".summary.json", "w") as f:
            json.dump(summary, f, indent=2)
        print(f"[session] saved {base}.jsonl (+ .summary.json)")
        return base


def latest_session_summaries(dirpath, limit=5):
    """Helper for tuning agents: most recent summary dicts, oldest->newest."""
    if not os.path.isdir(dirpath):
        return []
    files = sorted(
        (f for f in os.listdir(dirpath) if f.endswith(".summary.json")),
        key=lambda f: os.path.getmtime(os.path.join(dirpath, f)),
    )[-limit:]
    out = []
    for f in files:
        with open(os.path.join(dirpath, f)) as fh:
            out.append(json.load(fh))
    return out