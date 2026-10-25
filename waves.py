"""Wave spawning with staggered entries."""
import random

from enemies import KINDS


def spawn_position(rng):
    """A point just outside a random screen edge."""
    from config import W, H, SPAWN_MARGIN
    side = rng.randrange(4)
    m = SPAWN_MARGIN
    if side == 0:
        return (rng.uniform(0, W), -m)
    if side == 1:
        return (rng.uniform(0, W), H + m)
    if side == 2:
        return (-m, rng.uniform(0, H))
    return (W + m, rng.uniform(0, H))


class Waves:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.wave = 0
        self.intermission = 1.2   # short breath before wave 1
        self.pending = []
        self.stagger_timer = 0.0
        self.spawned_ever = 0
        self.count_scale = 1.0  # set per-difficulty by the Game

    def composition(self, wave):
        """Return a list of enemy kinds for the given wave number.
        Group sizes are scaled by self.count_scale (difficulty); 1.0 = standard."""
        n = max(1, wave)

        def s(k):
            return max(0, int(round(k * self.count_scale)))

        kinds = []
        kinds += ["scout"] * s(min(3 + n, 9))
        kinds += ["gunner"] * s(max(0, n - 1))
        kinds += ["brute"] * s(max(0, (n - 1) // 2))
        kinds += ["dart"] * s(min(n - 1, 5)) if n >= 2 else []
        kinds = kinds[:16]  # sanity cap
        self.rng.shuffle(kinds)
        return kinds

    def update(self, world, dt):
        if not any(p.alive for p in world.players):
            # nobody alive (all downed): hold the field while revives happen
            return
        if world.enemies or self.pending:
            self.intermission = 6.0  # reset between waves
            if self.pending:
                self.stagger_timer -= dt
                if self.stagger_timer <= 0:
                    kind = self.pending.pop()
                    world.enemies.append(world.make_enemy(kind, spawn_position(self.rng)))
                    self.spawned_ever += 1
                    self.stagger_timer = 0.75
        else:
            self.intermission -= dt
            if self.intermission <= 0:
                self.wave += 1
                self.pending = self.composition(self.wave)
                self.stagger_timer = 1.5
                world.on_wave_start(self.wave)
