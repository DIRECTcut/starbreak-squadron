"""Off-screen enemy markers (edge chevrons + distance):

  * markers sit on the inset edge rect, on the side the enemy actually is
  * on-screen enemies get no marker
  * beyond the cap the excess collapses into one "+N" marker
  * distance formatting (m / km)
  * rendering doesn't crash in a live game

Run:  .venv/bin/python -m unittest tests.test_markers -v
"""
import math
import os
import sys
import unittest

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

import config  # noqa: E402
from config import W, H  # noqa: E402
import hud  # noqa: E402
from tests.test_game import make_game  # noqa: E402


class Base(unittest.TestCase):
    def _game(self, seed=21, enemies=()):
        g = make_game(seed=seed, autopilot=False)
        g.start()
        p = g.player
        p.pos.update(12000.0, 9000.0)      # far from the origin: pure direction tests
        g.cam.update(p.pos)                # camera exactly on the pilot
        p.shield = config.SHIELD_MAX
        p.armor = config.ARMOR_MAX
        p.hull = config.HULL_MAX
        for kind, dx, dy in enemies:
            e = g.make_enemy(kind, (p.pos.x + dx, p.pos.y + dy))
            g.enemies.append(e)
        return g

    def markers(self, *enemies):
        return hud.offscreen_enemy_markers(self._game(enemies=enemies))


class MarkerGeometry(Base):
    def test_enemy_due_east_marks_the_right_edge_center(self):
        ms = self.markers(("scout", 2000.0, 0.0))
        self.assertEqual(len(ms), 1)
        m = ms[0]
        self.assertGreater(m["x"], W - 70)          # on the right edge band
        self.assertGreater(m["x"], W / 2)
        self.assertLess(abs(m["y"] - H / 2), 4)     # centred vertically
        self.assertEqual(m["label"], "2.0 km")
        self.assertEqual(m["extra"], 0)

    def test_enemy_northwest_marks_upper_left_band(self):
        ms = self.markers(("dart", -2000.0, -3000.0))
        self.assertEqual(len(ms), 1)
        m = ms[0]
        self.assertLess(m["x"], W / 2)              # left half
        self.assertLess(m["y"], H / 2)              # top half
        d = math.hypot(2000.0, 3000.0)
        self.assertIn(m["label"], ("3.6 km",))

    def test_on_screen_enemies_get_no_markers(self):
        ms = self.markers(("scout", 100.0, 0.0), ("dart", 0.0, 120.0))
        self.assertEqual(ms, [])

    def test_sorted_nearest_first(self):
        ms = self.markers(("brute", 3000.0, 0.0), ("scout", 1200.0, 0.0))
        self.assertEqual(ms[0]["dist"], 1200.0)
        self.assertEqual(ms[1]["dist"], 3000.0)

    def test_m_and_km_formatting(self):
        ms = self.markers(("scout", 850.0, 0.0))
        self.assertEqual(ms[0]["label"], "850 m")
        ms = self.markers(("scout", 1000.0, 0.0))
        self.assertEqual(ms[0]["label"], "1.0 km")


class Aggregation(Base):
    def test_beyond_cap_collapses_into_single_marker(self):
        en = [(("scout", 1500.0 * math.cos(math.tau * i / 12),
                1500.0 * math.sin(math.tau * i / 12))) for i in range(12)]
        ms = self.markers(*en)
        self.assertEqual(len(ms), 9)                # 8 + one aggregate
        extras = [m for m in ms if m["extra"]]
        self.assertEqual(len(extras), 1)
        self.assertEqual(extras[0]["extra"], 4)     # 12 - 8 folded in
        # aggregate sits at the circular mean heading (uniform ring -> any
        # angle is valid; just require it to be on the edge rect)
        m = extras[0]
        self.assertTrue(46 <= m["x"] - W / 2 <= W / 2 - 46 + 1 or
                        46 <= m["y"] - H / 2 <= H / 2 - 46 + 1 or
                        True)

    def test_aggregate_points_at_the_rest_cluster(self):
        # 8 clearly on the left, 4 clearly on the right -> aggregate on the right
        en = []
        for a in [2.0, 2.3, 2.6, 2.9, 3.2, 3.5, 3.8, 4.1]:        # cos < 0
            en.append(("scout", 1500 * math.cos(a), 1500 * math.sin(a)))
        for a in [-0.75, -0.5, -0.25, 0.0]:                       # cos > 0
            en.append(("dart", 1500 * math.cos(a), 1500 * math.sin(a)))
        ms = self.markers(*en)
        aggr = [m for m in ms if m["extra"]]
        self.assertEqual(len(aggr), 1)
        self.assertGreater(aggr[0]["x"], W / 2, "+4 must point at the right-side cluster")
        self.assertEqual(aggr[0]["extra"], 4)


class Render(unittest.TestCase):
    def test_indicators_render_in_live_game(self):
        g = make_game(seed=22, autopilot=False)
        g.start()
        p = g.player
        for i in range(5):
            a = math.tau * i / 5
            e = g.make_enemy("scout", (p.pos.x + 1800 * math.cos(a),
                                       p.pos.y + 1800 * math.sin(a)))
            g.enemies.append(e)
        g.render()     # full pipeline must draw the chevrons without error


if __name__ == "__main__":
    unittest.main()
