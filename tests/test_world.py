"""Open-world (near-infinite space) regressions:

  * no invisible walls (the old W x H arena bounds must not catch the pilot)
  * camera follows the local pilot; entity lists stay bounded no matter how
    far the pilot flies
  * memory does not grow during sustained far flight (and the game does not
    crash rendering at extreme world coordinates)
  * waves keep playing: spawns follow the pilot, left-behind salvage expires,
    enemies that lose the pilot in deep space despawn (wave still clears)

Run:  .venv/bin/python -m unittest tests.test_world -v
"""
import math
import random
import tracemalloc
import unittest

import pygame

import config
from config import W, H, FPS, PARTICLE_CAP, SPAWN_DIST, ESCAPE_RADIUS

from tests.test_game import make_game, DT


class NoWalls(unittest.TestCase):
    def test_pilot_flies_beyond_old_arena_bounds(self):
        g = make_game(seed=5, autopilot=False)
        g.start()
        p = g.player
        d = pygame.math.Vector2(1, 1).normalize()
        for _ in range(3 * FPS):  # 3 s at ~400 px/s full thrust
            p.vel = d * config.PLAYER_MAX_SPEED
            g.update(DT)
        # both coordinates crossed the old right/bottom wall and keep going
        self.assertGreater(p.pos.x, W)
        self.assertGreater(p.pos.y, H)
        # velocity was not zeroed by a bounce-off-wall
        self.assertGreater(p.vel.x, 0.0)
        self.assertGreater(p.vel.y, 0.0)

    def test_pilot_can_fly_the_other_way_past_origin(self):
        g = make_game(seed=5, autopilot=False)
        g.start()
        p = g.player
        d = pygame.math.Vector2(-1, -1).normalize()
        for _ in range(4 * FPS):
            p.vel = d * config.PLAYER_MAX_SPEED
            g.update(DT)
        self.assertLess(p.pos.x, 0.0)
        self.assertLess(p.pos.y, 0.0)


class CameraAndSpawning(unittest.TestCase):
    def test_camera_tracks_the_pilot_while_flying(self):
        g = make_game(seed=6, autopilot=False)
        g.start()
        p = g.player
        for _ in range(6 * FPS):
            p.vel = pygame.math.Vector2(400, 300)
            g.update(DT)
        self.assertLess((g.cam - p.pos).length(), 90.0,
                        "camera must sit on the pilot, not the old arena center")

    def test_spawns_follow_a_flyaway_pilot(self):
        g = make_game(seed=7, autopilot=False)
        g.start()
        p = g.player
        p.pos.update(20000.0, 15000.0)          # player simply left the origin
        # clear current wave state, then let the waves run until one spawns
        g.enemies.clear()
        g.waves.pending = []
        g.waves.intermission = 0.2
        for _ in range(10 * FPS):
            if g.enemies:
                break
            g.update(DT)
        self.assertGreater(len(g.enemies), 0)
        for e in g.enemies:
            d = (e.pos - p.pos).length()
            self.assertGreaterEqual(d, SPAWN_DIST[0] - 5.0)
            self.assertLessEqual(d, SPAWN_DIST[1] + 5.0,
                                "spawn ring must be around the pilot, not the origin")

    def test_left_behind_orb_expires(self):
        g = make_game(seed=8, autopilot=False)
        g.start()
        from entities import Orb
        p = g.player
        # place salvage well behind the flight path (beyond magnet + pickup range)
        o = Orb(pygame.math.Vector2(p.pos.x - 1000.0, p.pos.y), value=30)
        g.orbs.append(o)
        seen = 0
        for _ in range(45 * FPS):   # long flight away; that orb is left behind
            p.downed = False; p.alive = True
            p.shield = config.SHIELD_MAX; p.armor = config.ARMOR_MAX; p.hull = config.HULL_MAX
            p.vel = pygame.math.Vector2(400, 0)
            g.update(DT)
            if id(o) in [id(x) for x in g.orbs]:
                seen += 1
        # the left-behind orb kept being tracked for >30 s, then expired
        self.assertLess(seen, 45 * FPS, "left-behind salvage must expire (memory bound)")
        self.assertGreaterEqual(seen, 28 * FPS, "orb must persist until its 30 s life elapses")


class EscapingEnemy(unittest.TestCase):
    def test_enemy_further_than_escape_radius_is_removed_and_wave_clears(self):
        g = make_game(seed=9, autopilot=False)
        g.start()
        p = g.player
        e = g.make_enemy("brute", (p.pos.x + ESCAPE_RADIUS + 500, p.pos.y))
        g.enemies.append(e)
        g.update(DT)
        self.assertNotIn(e, g.enemies, "far-off enemy must despawn, not chase forever")
        # despawn gives no reward
        self.assertEqual(p.score, 0)
        # and the wave machine keeps going: wave 1 now starts and spawns track the pilot
        for _ in range(12 * FPS):
            if g.waves.wave >= 1 and g.enemies:
                break
            g.update(DT)
        self.assertGreaterEqual(g.waves.wave, 1, "waves must resume after an escape-despawn")
        if g.enemies:
            for ee in g.enemies:
                self.assertLess((ee.pos - p.pos).length(), ESCAPE_RADIUS)


class FarFlight(unittest.TestCase):
    """Sustained full-speed flight: no crash, bounded lists, flat memory."""

    def _fly(self, g, seconds):
        p = g.player
        d = pygame.math.Vector2(math.sqrt(0.5), math.sqrt(0.5))
        for i in range(int(seconds * FPS)):
            # keep the pilot flying the whole window (solo flight has no revive)
            p.downed = False
            p.alive = True
            p.shield = config.SHIELD_MAX
            p.armor = config.ARMOR_MAX
            p.hull = config.HULL_MAX
            p.vel = d * config.PLAYER_MAX_SPEED
            g.update(DT)
            g.render()
        return p.pos.length()

    def test_far_flight_does_not_crash_and_lists_are_bounded(self):
        g = make_game(seed=42, autopilot=False)   # autopilot=False: no bot fire,
        g.start()                                  # keep the entity load on player flight
        dist = self._fly(g, 30)  # 30 s of sim+render at max speed
        self.assertGreater(dist, 10000.0, "test must actually fly far")
        self.assertLessEqual(len(g.bolts), 60, "expired bolts must be culled, even far from origin")
        self.assertLessEqual(len(g.orbs), 60)
        self.assertLessEqual(len(g.particles), PARTICLE_CAP)
        self.assertLessEqual(len(g.enemies), 32)

    def test_far_flight_memory_bounded(self):
        g = make_game(seed=42, autopilot=False)
        g.start()
        # warmup so one-off allocations (fonts, surfaces, bot ramp) are excluded
        self._fly(g, 10)
        import gc
        gc.collect()
        tracemalloc.start()
        cur0, peak0 = tracemalloc.get_traced_memory()
        dist = self._fly(g, 60)   # 60 more seconds of full-speed flight + render
        cur1, peak1 = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertGreater(dist, 15000.0, "test must actually fly far (>10x old arena)")
        dcur = (cur1 - cur0) / 1e6
        dpeak = (peak1 - peak0) / 1e6
        # steady state: no growing structure. Observed < 0.5 MB; allow headroom.
        self.assertLess(dcur, 8.0, f"live memory grew {dcur:.2f} MB over 60 s of flight")
        self.assertLess(dpeak, 24.0, f"peak allocations grew {dpeak:.2f} MB over 60 s of flight")


class ExtremeCoordinates(unittest.TestCase):
    """Rendering at coordinates far beyond float32 comfort must not crash."""

    def test_render_at_extreme_camera_positions(self):
        g = make_game(seed=10, autopilot=False)
        g.start()
        for cam in [(1e7, -8e7), (-5e6, 3e6), (1.5e8, 1.5e8), (0.0, 0.0)]:
            g.cam.update(cam)
            for _ in range(5):
                g.render()  # any exception fails the test
        # starfield still draws stars at extreme offsets (tiled, not empty)
        surf = pygame.Surface((W, H))
        g.stars.draw(surf, 1.0, 1e7, -8e7)
        nonblack = sum(1 for x in range(0, W, 23) for y in range(0, H, 23)
                       if surf.get_at((x, y))[:3] != (0, 0, 0))
        self.assertGreater(nonblack, 0, "starfield must remain visible via tiling")


if __name__ == "__main__":
    random.seed(1)
    unittest.main()
