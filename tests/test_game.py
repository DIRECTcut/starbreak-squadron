import os
import sys
import unittest

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from game import Game  # noqa: E402
from enemies import Enemy  # noqa: E402
import config  # noqa: E402
from weapons import WEAPONS  # noqa: E402

DT = 1 / 60


def make_game(seed=7, autopilot=False, bot="weave", sessions=None):
    if not pygame.get_init():
        pygame.init()
    if pygame.display.get_surface() is None:
        pygame.display.set_mode((config.W, config.H), pygame.SRCALPHA)
    log = sessions is not None
    return Game(seed=seed, autopilot=autopilot, mute=True, bot=bot,
                log_sessions=log, sessions_dir=sessions or config.SESSIONS_DIR)


class DefenseTest(unittest.TestCase):
    def test_damage_pipeline_shield_armor_hull(self):
        g = make_game()
        g.start()
        p = g.player
        p.take_damage(g, 40)  # shield 100 -> 60
        self.assertEqual(p.shield, 60)
        self.assertEqual(p.armor, config.ARMOR_MAX)
        self.assertEqual(p.hull, config.HULL_MAX)
        p.take_damage(g, 80)  # 60 into shield, 20 into armor
        self.assertEqual(p.shield, 0)
        self.assertEqual(p.armor, 80)
        self.assertEqual(p.hull, config.HULL_MAX)
        p.take_damage(g, 85)  # 80 into armor, 5 into hull
        self.assertEqual(p.armor, 0)
        self.assertEqual(p.hull, 95)
        dead = p.take_damage(g, 200)
        self.assertTrue(dead)
        self.assertFalse(p.alive)
        self.assertEqual(p.hull, 0)

    def test_shield_regen_after_delay(self):
        g = make_game()
        g.start()
        p = g.player
        p.take_damage(g, 40)
        for _ in range(600):  # 10 s (> 4.0 s regen delay + refill at current rate)
            g.update(DT)
            g.enemies.clear()  # isolate: no incoming fire during the window
        self.assertEqual(p.shield, config.SHIELD_MAX)

    def test_regen_blocked_while_freshly_hit(self):
        g = make_game()
        g.start()
        p = g.player
        p.take_damage(g, 40)
        for _ in range(20):  # 0.33 s only
            g.update(DT)
        self.assertEqual(p.shield, 60)  # still in regen delay window

    def test_salvage_repairs_shield_then_armor(self):
        g = make_game()
        g.start()
        p = g.player
        p.take_damage(g, 120)  # 100 into shield, 20 into armor
        self.assertEqual(p.shield, 0)
        self.assertEqual(p.armor, 80)
        p.repair(100)  # scaled x0.4 -> +40, shield takes it first
        self.assertEqual(p.shield, 40)
        self.assertEqual(p.armor, 80)
        p.repair(400)  # -> shield to max, remainder to armor
        self.assertEqual(p.shield, config.SHIELD_MAX)
        self.assertEqual(p.armor, config.ARMOR_MAX)


class CombatTest(unittest.TestCase):
    def test_player_bolt_hits_enemy(self):
        g = make_game()
        g.start()
        e = Enemy("scout", (600, 360))
        g.enemies.append(e)
        spec = WEAPONS[0]
        g.spawn_player_bolt((600, 300), 1.5708, spec)  # straight down toward enemy
        for _ in range(60):
            g.update(DT)
            if e.dead:
                break
        self.assertTrue(e.dead)
        self.assertGreaterEqual(g.kills, 1)
        self.assertGreater(g.score, 0)

    def test_scatter_fires_five_pellets(self):
        g = make_game()
        g.start()
        e = Enemy("scout", (700, 360))
        g.enemies.append(e)
        g.player.pos = pygame.math.Vector2(400, 360)
        g.player.weapon_idx = 1
        spec = WEAPONS[1]
        before = len(g.bolts)
        g.player._fire(g, spec, e)
        self.assertEqual(len(g.bolts) - before, spec.pellets)

    def test_homing_missile_strikes_target(self):
        g = make_game()
        g.start()
        e = Enemy("brute", (900, 500))
        g.enemies.append(e)
        spec = WEAPONS[2]
        self.assertTrue(spec.homing)
        g.spawn_player_bolt((300, 200), 0.0, spec, target=e)  # fired away, must turn
        hp0 = e.hp
        for _ in range(300):
            g.update(DT)
            if e.dead:
                break
        self.assertTrue(e.dead)
        self.assertGreater(hp0, 0)

    def test_enemy_fire_hurts_player_pipeline(self):
        g = make_game()
        g.start()
        p = g.player
        p.shield = 0
        p.armor = 0
        g.spawn_enemy_bolt(p.pos, 0.0, 10, 999, (255, 0, 0), 4)
        g.update(DT)
        self.assertAlmostEqual(p.hull, config.HULL_MAX - 10)

    def test_enemy_fires_within_range(self):
        g = make_game()
        g.start()
        e = Enemy("gunner", (640, 460))
        g.enemies.append(e)
        e.fire_timer = 0.0
        g.player.pos = pygame.math.Vector2(640, 200)
        for _ in range(60):
            g.update(DT)
            if any(not b.friendly for b in g.bolts):
                break
        self.assertTrue(any(not b.friendly for b in g.bolts))


class EventsTest(unittest.TestCase):
    """Keydown handling must not crash on any real key constant (regression: K_ESC)."""

    def keydown(self, g, key):
        g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=key))

    def test_state_machine_via_events(self):
        g = make_game()
        self.assertEqual(g.state, "menu")
        self.keydown(g, pygame.K_RETURN)          # launch
        self.assertEqual(g.state, "play")
        self.keydown(g, pygame.K_ESCAPE)          # esc -> pause
        self.assertEqual(g.state, "pause")
        self.keydown(g, pygame.K_p)               # p -> resume
        self.assertEqual(g.state, "play")
        self.keydown(g, pygame.K_3)               # switch weapon
        self.assertEqual(g.player.weapon_idx, 2)
        self.keydown(g, pygame.K_1)
        self.assertEqual(g.player.weapon_idx, 0)

    def test_restart_and_gameover_menu(self):
        g = make_game()
        g.start()
        g.score = 500
        self.keydown(g, pygame.K_r)               # restart in flight
        self.assertEqual(g.state, "play")
        self.assertEqual(g.score, 0)
        g.state = "over"
        self.keydown(g, pygame.K_ESCAPE)          # game over -> menu
        self.assertEqual(g.state, "menu")
        self.keydown(g, pygame.K_SPACE)           # menu: space also launches
        self.assertEqual(g.state, "play")

    def test_unknown_key_is_ignored(self):
        g = make_game()
        g.start()
        self.keydown(g, pygame.K_F13)             # no-op, no crash
        self.assertEqual(g.state, "play")


class WavesTest(unittest.TestCase):
    def test_waves_progress_and_spawn(self):
        g = make_game(seed=3)
        g.start()
        seen_live = False
        for _ in range(60 * 40):  # up to 40 s of sim
            g.update(DT)
            if g.enemies:
                seen_live = True
            if g.waves.wave >= 1 and seen_live:
                break
        self.assertTrue(seen_live)
        self.assertGreaterEqual(g.waves.wave, 1)
        self.assertGreater(g.waves.spawned_ever, 0)

    def test_wave_composition_scales(self):
        from waves import Waves
        w = Waves(seed=1)
        c1 = w.composition(1)
        c5 = w.composition(5)
        self.assertGreaterEqual(len(c5), len(c1))
        self.assertIn("brute", c5)
        self.assertNotIn("brute", c1)
        self.assertIn("dart", c5)     # interceptors join from wave 2+
        self.assertNotIn("dart", c1)
        self.assertTrue(all(k in ("scout", "gunner", "brute", "dart") for k in c1 + c5))


    def test_enemies_scale_with_wave(self):
        g = make_game()
        g.start()
        g.waves.wave = 1
        e1 = g.make_enemy("scout", (400, 300))
        g.waves.wave = 5
        e5 = g.make_enemy("scout", (400, 300))
        self.assertGreater(e5.max_hp, e1.max_hp)
        self.assertGreaterEqual(e5.speed, e1.speed)


class EconomyTest(unittest.TestCase):
    def test_kill_drops_orbs_and_score(self):
        g = make_game()
        g.start()
        e = Enemy("brute", (700, 400))
        g.enemies.append(e)
        e.hp = 1  # one-shot it
        g.player.pos = pygame.math.Vector2(80, 700)  # stay out of magnet range
        g.spawn_player_bolt(e.pos - (30, 0), 0.0, WEAPONS[0])
        for _ in range(10):
            g.update(DT)
        self.assertGreaterEqual(len(g.orbs), 9)
        self.assertGreaterEqual(g.kills, 1)
        # now fly the player in: orbs magnetize and get collected
        g.player.pos = pygame.math.Vector2(700, 400)
        for _ in range(60 * 4):
            g.update(DT)
            if not g.orbs:
                break
        self.assertGreater(g.stats["orbs_collected"], 0)
        self.assertGreater(g.score, 0)


class SmokeTest(unittest.TestCase):
    def test_render_states_crash_free(self):
        g = make_game(seed=11)
        g.render(); pygame.display.flip()          # menu
        g.start()
        for _ in range(300):
            g.update(DT)
        g.render(); pygame.display.flip()          # playing
        g.state = "pause"
        g.render(); pygame.display.flip()
        g.state = "over"
        g.render(); pygame.display.flip()
        self.assertTrue(True)

    def test_particle_cap_respected(self):
        g = make_game()
        g.start()
        for _ in range(3000):
            g.add_particle((100, 100), (1, 0), 5.0, (255, 255, 255), 2)
        self.assertLessEqual(len(g.particles), config.PARTICLE_CAP)

    def test_autopilot_survives_somewhat_and_fights(self):
        g = make_game(seed=42, autopilot=True)
        g.start()
        for _ in range(60 * 60):  # 60 s of sim
            g.update(DT)
            if g.state == "over":
                g.start()
        self.assertGreater(g.player.total_damage_dealt, 0)
        self.assertGreater(g.kills, 0)


class CoopTest(unittest.TestCase):
    """Co-op (2-player) rules: revive channel, interrupt, fleet-down."""

    def _pair(self, **kw):
        g = make_game(seed=3, autopilot=True, bot="weave,steady", **kw)
        g.start()
        a, b = g.players
        self.assertEqual(len(g.players), 2)
        self.assertTrue(a.alive and b.alive)
        return g, a, b

    def _down(self, g, p):
        p.take_damage(g, 10000)  # blow shield+armor+hull straight through
        self.assertTrue(p.downed and not p.alive)

    def test_revive_channel_revives_downed_player(self):
        g, a, b = self._pair()
        self._down(g, a)
        b.pos = a.pos + (30, 0)
        b.vel = (0, 0)
        for i in range(60 * 5):  # up to 5 s
            g.update(DT)
            if a.alive:
                break
        self.assertTrue(a.alive, "downed player should be revived by teammate")
        self.assertAlmostEqual(a.hull, config.REVIVE_HULL_FRAC * config.HULL_MAX)
        self.assertEqual(a.shield, 0.0)
        self.assertEqual(a.armor, 0.0)
        # session stayed open: run continued for the surviving pilot
        self.assertNotEqual(g.state, "over")

    def test_hit_interrupts_revive_channel(self):
        g, a, b = self._pair()
        self._down(g, a)
        b.pos = a.pos + (25, 0)
        b.vel = (0, 0)
        for _ in range(60):  # 1 s of channel
            g.update(DT)
        self.assertGreater(a.revive_progress, 0.5)
        a.take_damage(g, 5)  # any hit on the downed body resets the channel
        self.assertEqual(a.revive_progress, 0.0)

    def test_solo_down_continues_fleet_down_ends(self):
        g, a, b = self._pair()
        self._down(g, a)
        b.pos = a.pos + (20, 0)
        b.vel = (0, 0)
        for _ in range(60 * 2):  # 2 s (short of the 3 s channel)
            g.update(DT)
        self.assertNotIn("over", [g.state])
        # now take the second pilot too: run must end after grace period
        self._down(g, b)
        for _ in range(60 * 3):
            g.update(DT)
            if g.state == "over":
                break
        self.assertEqual(g.state, "over")

    def test_two_bots_fight_without_error(self):
        g = make_game(seed=9, autopilot=True, bot="weave,weave")
        g.start()
        for _ in range(60 * 30):
            g.update(DT)
            if g.state == "over":
                break
        self.assertGreater(g.kills, 0)
        self.assertGreater(sum(p.total_damage_dealt for p in g.players), 0)


class TargetingTest(unittest.TestCase):
    """Manual target selection: auto-lock default, [ / ] cycling, T to undo,"""

    def _setup(self, seed=4):
        g = make_game(seed=seed, autopilot=False, bot="")
        g.start()
        p = g.players[0]
        p.pos.update(500.0, 500.0)
        # three enemies at 50 / 250 / 450 px east
        e_near = g.make_enemy("scout", (550.0, 500.0))
        e_mid = g.make_enemy("gunner", (750.0, 500.0))
        e_far = g.make_enemy("brute", (950.0, 500.0))
        g.enemies.append(e_near)
        g.enemies.append(e_mid)
        g.enemies.append(e_far)
        return g, p, e_near, e_mid, e_far

    def test_auto_locks_nearest(self):
        g, p, e_near, e_mid, e_far = self._setup()
        self.assertEqual(p.target(g.enemies), e_near)

    def test_first_press_pins_auto_target(self):
        g, p, e_near, _, _ = self._setup()
        p.cycle_target(1)
        self.assertEqual(p.target_rank, 0)
        self.assertEqual(p.target(g.enemies), e_near)   # no jump on first press

    def test_next_prev_step_through_distance_order(self):
        g, p, e_near, e_mid, e_far = self._setup()
        for rank, want in ((1, e_near), (2, e_mid), (3, e_far)):
            p.cycle_target(1)
            self.assertEqual(p.target(g.enemies), want, "cycle to rank %d" % (rank - 1))
        p.cycle_target(1)  # wrap-around
        self.assertEqual(p.target(g.enemies), e_near)
        p.cycle_target(-1)  # previous wraps back
        self.assertEqual(p.target(g.enemies), e_far)

    def test_prev_from_auto_pins_then_steps_back(self):
        g, p, e_near, e_mid, e_far = self._setup()
        p.cycle_target(-1)          # pins auto target (rank 0)
        self.assertEqual(p.target(g.enemies), e_near)
        p.cycle_target(-1)          # rank 0 -> -1 -> mod wraps to last (farthest)
        self.assertEqual(p.target(g.enemies), e_far)

    def test_clear_returns_to_auto(self):
        g, p, e_near, _, _ = self._setup()
        p.cycle_target(1); p.cycle_target(1)          # lock mid-enemy
        p.clear_target()                              # back to auto
        self.assertIsNone(p.target_rank)
        self.assertEqual(p.target(g.enemies), e_near)
        # auto keeps tracking whatever is nearest, even if the lock order shifted
        p.pos.update(940.0, 500.0)                    # now the far enemy is nearest
        self.assertEqual(p.target(g.enemies), [e for e in g.enemies
                                               if e.pos.x == 950.0][0])

    def test_locked_target_death_hands_rank_to_next(self):
        g, p, e_near, e_mid, e_far = self._setup()
        p.cycle_target(1); p.cycle_target(1)          # rank 1 = mid
        self.assertEqual(p.target(g.enemies), e_mid)
        e_mid.hp = 0                                  # killed
        self.assertEqual(p.target(g.enemies), e_far)  # rank 1 of [near, far]

    def test_out_of_range_has_no_target(self):
        g, p, e_near, e_mid, e_far = self._setup()
        for e in (e_near, e_mid, e_far):
            e.pos.y += 900                            # all beyond AIM_RANGE (580)
        self.assertIsNone(p.target(g.enemies))
        p.cycle_target(1)
        self.assertIsNone(p.target(g.enemies))


if __name__ == "__main__":
    unittest.main(verbosity=2)
