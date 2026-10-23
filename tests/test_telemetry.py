"""Telemetry regression tests: sessions must capture a coherent, complete story."""
import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402
from enemies import Enemy  # noqa: E402
from weapons import WEAPONS  # noqa: E402
from telemetry import latest_session_summaries  # noqa: E402
from tests.test_game import make_game, DT  # noqa: E402


class TelemetryTest(unittest.TestCase):
    def _base_from(self, tmpdir):
        names = [f for f in os.listdir(tmpdir) if f.endswith(".summary.json")]
        self.assertTrue(names, "no summary file written")
        path = os.path.join(tmpdir, names[0])
        return path[: -len(".summary.json")]

    def test_session_captures_full_play(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = make_game(seed=5, sessions=tmp)
            g.start()
            g.waves.intermission = 9999          # isolate: no wave interference
            g.on_wave_start(1)                   # but log one wave event

            # --- kill: one-shot a scout with a manual bolt
            e = Enemy("scout", (700, 360))
            g.enemies.append(e)
            e.hp = 1  # one-shot so it doesn't wander out of the bolt line
            g.spawn_player_bolt(e.pos - (30, 0), 0.0, WEAPONS[0])
            for _ in range(30):
                g.update(DT)
            self.assertTrue(e.dead)              # kill happened (must be recorded)
            g.enemies.clear()                    # isolate: drop wave spawns

            # --- more shots: aim two bolts at a durable second scout
            e2 = Enemy("scout", (700, 360))
            g.enemies.append(e2)
            for x in (9, 20):
                g.spawn_player_bolt(e2.pos - (x, 0), 0.0, WEAPONS[0])
            g.update(DT)

            # --- one hit on the player, absorbed by hull
            g.player.shield = 0
            g.player.armor = 0
            g.spawn_enemy_bolt(g.player.pos, 0.0, 10, 999, (255, 0, 0), 4)
            g.update(DT)                          # one hit, absorbed by hull

            g.to_menu()                           # triggers session close
            summaries = latest_session_summaries(tmp, limit=1)
            self.assertEqual(len(summaries), 1)
            s = summaries[0]

            self.assertGreaterEqual(s["kills_total"], 1)
            self.assertGreater(s["damage_in"], 0)
            self.assertGreater(s["absorbed_by_layer"]["hull"], 0)
            ss = s["shot_stats"]
            # pfired counts only autofire (player._fire); manual bolts only hit
            self.assertGreaterEqual(ss["player_fired"], 1)
            self.assertGreaterEqual(ss["player_hit_enemies"], 3)  # all 3 manual bolts
            self.assertEqual(ss["enemy_fired"], 1)
            self.assertEqual(ss["enemy_hit_player"], 1)
            share = sum(s["weapon_time_share"].values())
            self.assertLess(share, 1.01)
            self.assertFalse(s["died"])
            self.assertGreater(s["damage_out"], 0)

            base = self._base_from(tmp)
            with open(base + ".jsonl") as f:
                rows = [json.loads(l) for l in f if l.strip()]
            types = [r["type"] for r in rows]
            self.assertEqual(types[0], "open")
            for expected in ("sample", "hit", "kill", "wave"):
                self.assertIn(expected, types)
            hits = [r for r in rows if r["type"] == "hit"]
            self.assertEqual(hits[-1]["layer"], "hull")
            kills = [r for r in rows if r["type"] == "kill"]
            self.assertEqual(kills[0]["kind"], "scout")
            self.assertGreater(kills[0]["dmg"], 0)
            wave_rows = [r for r in rows if r["type"] == "wave"]
            self.assertEqual(wave_rows[0]["n"], 1)
            samples = [r for r in rows if r["type"] == "sample"]
            self.assertTrue(all("p" in r and "f" in r for r in samples))

    def test_steady_bot_session_runs_and_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = make_game(seed=3, autopilot=True, bot="steady", sessions=tmp)
            ok, report = g.run_selftest(2400)  # 40 s: steady bot should die mid-window
            self.assertTrue(ok)
            summaries = latest_session_summaries(tmp, limit=5)
            self.assertGreaterEqual(len(summaries), 1)
            self.assertTrue(all(s["mode"] == "bot-steady" for s in summaries))

    def test_log_off_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = make_game(seed=2)  # sessions=None -> logging disabled
            g.start()
            for _ in range(60):
                g.update(DT)
            g.to_menu()
            self.assertEqual(os.listdir(tmp), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
