"""Projectiles, particles, salvage orbs."""
import math
import random

import pygame

from config import W, H, BOLT_MARGIN


def norm(v):
    """Safe normalize: returns zero vector instead of raising on length 0."""
    l = v.length()
    if l < 1e-6:
        return pygame.math.Vector2(0, 0)
    return v / l


def angle_lerp(a, b, t):
    """Lerp along the shortest arc between two angles."""
    d = math.atan2(math.sin(b - a), math.cos(b - a))
    return a + d * max(0.0, min(1.0, t))


class Particle:
    __slots__ = ("pos", "vel", "life", "max_life", "color", "size")

    def __init__(self, pos, vel, life, color, size=3):
        self.pos = pygame.math.Vector2(pos)
        self.vel = pygame.math.Vector2(vel)
        self.life = life
        self.max_life = life
        self.color = color
        self.size = size

    @property
    def dead(self):
        return self.life <= 0

    def update(self, dt):
        self.pos += self.vel * dt
        self.vel *= max(0.0, 1.0 - 2.2 * dt)
        self.life -= dt

    def draw(self, surf):
        f = max(0.0, self.life / self.max_life)
        r = max(1, int(self.size * f + 0.5))
        pygame.draw.circle(surf, self.color, (int(self.pos.x), int(self.pos.y)), r)


_NEXT_ID = [0]


def next_id():
    _NEXT_ID[0] += 1
    return _NEXT_ID[0]


class Bolt:
    def __init__(self, pos, angle, spec, friendly, target=None, owner_pid=0):
        self.id = next_id()
        self.owner_pid = owner_pid  # firing player (0 for enemies)
        self.spec = spec
        self.friendly = friendly
        dirv = pygame.math.Vector2(math.cos(angle), math.sin(angle))
        self.pos = pygame.math.Vector2(pos)
        self.vel = dirv * spec.bolt_speed
        self.radius = spec.bolt_radius
        self.life = 3.2 if spec.homing else 1.5
        self.target = target  # homing acquisition (enemy object)

    @property
    def dead(self):
        return self.life <= 0

    def update(self, world, dt):
        if self.spec.homing:
            tgt = self.target
            if tgt is not None and getattr(tgt, "hp", 0) <= 0:
                tgt = None
            if tgt is None:
                best, bd = None, 640.0
                for e in world.enemies:
                    d = (e.pos - self.pos).length()
                    if d < bd:
                        best, bd = e, d
                tgt = best
            if tgt is not None:
                want = math.atan2(tgt.pos.y - self.pos.y, tgt.pos.x - self.pos.x)
                have = math.atan2(self.vel.y, self.vel.x)
                have = angle_lerp(have, want, self.spec.turn_rate * dt)
                self.vel = pygame.math.Vector2(math.cos(have), math.sin(have)) * self.spec.bolt_speed
            # engine trail
            jitter = random.uniform(-10, 10)
            world.add_particle(
                self.pos, (-self.vel * 0.06) + (jitter, jitter * 0.5),
                0.35, (255, 190, 120), 3)
        self.pos += self.vel * dt
        self.life -= dt
        m = BOLT_MARGIN
        if self.pos.x < -m or self.pos.x > W + m or self.pos.y < -m or self.pos.y > H + m:
            self.life = min(self.life, 0.0)


class Orb:
    """Salvage pickup: magnetizes to player, heals + scores."""
    def __init__(self, pos, value):
        self.id = next_id()
        self.pos = pygame.math.Vector2(pos) + pygame.math.Vector2(
            random.uniform(-8, 8), random.uniform(-8, 8))
        self.value = value
        self.radius = 5
        self.t = random.uniform(0.0, 6.28)
        self.life = 30.0  # fade out if left too long

    @property
    def dead(self):
        return self.life <= 0

    def update(self, world, player, dt):
        self.t += dt
        self.life -= dt
        d = player.pos - self.pos
        dist = d.length()
        if dist < 240.0:
            acc = 950.0
            self._vel = getattr(self, "_vel", pygame.math.Vector2(0, 0))
            self._vel += norm(d) * acc * dt
            self._vel *= max(0.0, 1.0 - 3.0 * dt)
            self.pos += self._vel * dt
        if dist < 26.0 and player.alive:
            world.collect_orb(self, player)
            self.life = 0.0

    def draw(self, surf):
        pulse = 3.0 + 1.5 * math.sin(self.t * 6.0)
        fade = min(1.0, self.life / 3.0)
        c = tuple(int(x * fade) for x in (255, 230, 120))
        pygame.draw.circle(surf, (90, 80, 30), (int(self.pos.x), int(self.pos.y)), int(pulse) + 3)
        pygame.draw.circle(surf, c, (int(self.pos.x), int(self.pos.y)), int(pulse))
