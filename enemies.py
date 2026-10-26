"""Hostile ships: simple steering AIs + firing logic."""
import math
import random

import pygame

from config import W, H, CONTACT_DMG
from entities import norm, angle_lerp, next_id

V2 = pygame.math.Vector2

# kind: hp, speed, radius, score value per kill, orb count
KINDS = {
    "scout": dict(hp=22, speed=165.0, radius=13, score=40, orbs=3,
                  color=(255, 105, 105),
                  attack=dict(interval=1.1, dmg=10, speed=430, shots=1, gap=0.0,
                              spread_deg=0, range=520)),
    "gunner": dict(hp=44, speed=120.0, radius=16, score=75, orbs=5,
                   color=(255, 195, 80),
                   attack=dict(interval=2.0, dmg=9, speed=640, shots=3, gap=0.2,
                               spread_deg=0, range=560)),
    "brute": dict(hp=150, speed=48.0, radius=24, score=150, orbs=9,
                  color=(190, 115, 255),
                  attack=dict(interval=2.4, dmg=16, speed=320, shots=5, gap=0.5,
                              spread_deg=26, range=620)),
    # fast interceptor: no ranged fire, hunts your predicted position and rams.
    # Exists specifically to break the "strafe in circles, take no damage" meta.
    "dart": dict(hp=16, speed=255.0, radius=10, score=60, orbs=4,
                 color=(195, 255, 120), ram=24,
                 attack=dict(interval=9999.0, dmg=0, speed=0, shots=0, gap=0.0,
                             spread_deg=0, range=0)),
}


class Enemy:
    def __init__(self, kind, pos):
        p = KINDS[kind]
        self.id = next_id()
        self.kind = kind
        self.pos = pygame.math.Vector2(pos)
        self.vel = pygame.math.Vector2(0, 0)
        self.angle = random.uniform(0, math.tau)
        self.radius = p["radius"]
        self.hp = float(p["hp"])
        self.max_hp = float(p["hp"])
        self.speed = p["speed"]
        self.color = p["color"]
        self.score = p["score"]
        self.orbs = p["orbs"]
        self.attack = p["attack"]
        self.orbit_dir = random.choice((-1, 1))
        self.fire_timer = random.uniform(0.7, 1.6)
        self.ram = p.get("ram", 0)
        self.dmg_taken = 0.0
        self.last_hit_by = None  # pid of last damaging player (kill credit)
        self.spawn_t = None  # set by Game.make_enemy
        self.hunt_t = 0.0
        self.burst_left = 0
        self.burst_gap = 0.0
        self.hit_flash = 0.0
        self.strafe_bias = random.uniform(-0.4, 0.4)

    @property
    def dead(self):
        return self.hp <= 0

    # ---- desired velocity per AI personality ----
    def desired_velocity(self, player):
        to_p = player.pos - self.pos
        dist = to_p.length()
        toward = norm(to_p)
        # perpendicular "orbit" axis
        tangent = pygame.math.Vector2(-toward.y, toward.x) * self.orbit_dir
        want = pygame.math.Vector2(0, 0)

        if self.kind == "scout":
            # aggressive: close to 250, orbit-strafe, occasional pokes
            if dist > 280:
                want = toward * 1.0 + tangent * 0.55
            elif dist < 190:
                want = toward * -0.7 + tangent * 0.5
            else:
                want = tangent * 1.0
            want += toward * self.strafe_bias
        elif self.kind == "gunner":
            # mid range orbit shooter
            if dist > 420:
                want = toward * 1.0
            elif dist < 300:
                want = toward * -0.7
            else:
                want = tangent * 1.0
        elif self.kind == "dart":
            # hunt: lead your current velocity hard + weave to stay off-balance
            lead = toward * (0.9 * min(dist, 600.0) / max(self.speed, 1.0)) * 1.15
            want = (player.pos + lead - self.pos) / max(1e-6, (player.pos + lead - self.pos).length())
            want += V2(-toward.y, toward.x) * (0.45 * math.sin(self.hunt_t * 5.0))
        else:  # brute
            # slow, deliberate, barely retreats
            if dist > 320:
                want = toward * 1.0
            elif dist < 220:
                want = toward * -0.5
            else:
                want = tangent * 0.25
        if want.length_squared() <= 1e-6:
            return want
        return want / want.length() * self.speed

    # ---- firing: lead the player a bit ----
    def aim_angle(self, player):
        a = self.attack
        dist = (player.pos - self.pos).length()
        lead = norm(player.vel) * (dist / a["speed"]) * 0.8
        want = player.pos + lead - self.pos
        return math.atan2(want.y, want.x)

    def update(self, world, dt):
        p = world.pick_target(self)
        if p is None:
            # no alive player: drift lazily (run is ending or between states)
            self.vel *= max(0.0, 1.0 - 1.5 * dt)
            self.pos += self.vel * dt
            return
        player = p

        self.hit_flash = max(0.0, self.hit_flash - dt)
        self.hunt_t += dt

        # steering
        want = self.desired_velocity(player)
        k = min(1.0, 2.6 * dt)
        self.vel += (want - self.vel) * k
        self.pos += self.vel * dt

        # heading (open space: no arena walls, culling happens in Game.update)
        if self.vel.length() > 20:
            want_a = math.atan2(self.vel.y, self.vel.x)
            d = angle_lerp(self.angle, want_a, min(1.0, 6 * dt))
            self.angle = d

        # ---- firing state machine ----
        a = self.attack
        dist = (player.pos - self.pos).length()
        if self.burst_left > 0:
            self.burst_gap -= dt
            if self.burst_gap <= 0:
                self._shoot(world, player, a, self._shoot_off)
                self.burst_left -= 1
                self._shoot_off -= self._step
                self.burst_gap = a["gap"]
        elif dist < a["range"]:
            self.fire_timer -= dt
            if self.fire_timer <= 0:
                self.fire_timer = a["interval"]
                if a["shots"] == 1:
                    self._shoot(world, player, a, 0.0)
                else:
                    self.burst_left = a["shots"]
                    self.burst_gap = 0.0
                    n = a["shots"] - 1
                    step = math.radians(a["spread_deg"]) / max(1, n)
                    total = step * n / 2.0
                    self._step = step
                    self._shoot_off = total

        # contact with player -> ram damage
        if dist < self.radius + player.radius + 2:
            d = norm(self.pos - player.pos)
            player.contact_hit(world, d, self.ram or CONTACT_DMG)
            self.pos += d * 26
            self.vel += d * 260

    def _shoot(self, world, player, a, off):
        ang = self.aim_angle(player) + off
        world.spawn_enemy_bolt(
            self.pos + pygame.math.Vector2(math.cos(ang), math.sin(ang)) * (self.radius + 6),
            ang, a["dmg"], a["speed"], self.color, 4)
        world.sfx.play("eshot")

    def draw(self, surf, ox=0, oy=0):
        x, y = int(self.pos.x + ox), int(self.pos.y + oy)
        a = self.angle
        r = self.radius
        flash = self.hit_flash > 0.1 and int(self.pos.x * 7) % 2 == 0
        base = (255, 255, 255) if flash else self.color
        if self.kind == "scout":
            poly = [(r * 1.5, 0), (-r, r * 0.8), (-r * 0.5, 0), (-r, -r * 0.8)]
        elif self.kind == "dart":
            poly = [(r * 1.9, 0), (-r * 0.7, r * 0.75), (-r * 1.1, 0), (-r * 0.7, -r * 0.75)]
        elif self.kind == "gunner":
            poly = [(r * 1.3, r * 0.6), (0, -r * 1.3), (-r * 1.2, r * 0.6), (-r * 1.2, -r * 0.6), (0, r * 1.3)]
        else:
            poly = [(r * math.cos(math.tau * i / 6), r * math.sin(math.tau * i / 6)) for i in range(6)]
        cos, sin = math.cos(a), math.sin(a)

        def pt(px, py):
            return (x + int(px * cos - py * sin), y + int(px * sin + py * cos))

        poly = [pt(*p) for p in poly]
        pygame.draw.polygon(surf, (30, 30, 46), poly)
        pygame.draw.polygon(surf, base, poly, 3)
        pygame.draw.circle(surf, base, (x, y), max(3, r // 3))

        # hp bar
        if self.hp < self.max_hp:
            w = int(r * 2.4)
            fr = max(0.0, self.hp / self.max_hp)
            bx, by = x - w // 2, y - r - 12
            pygame.draw.rect(surf, (60, 15, 20), (bx, by, w, 4))
            col = (120, 255, 120) if fr > 0.5 else (255, 200, 80) if fr > 0.25 else (255, 90, 90)
            pygame.draw.rect(surf, col, (bx, by, int(w * fr), 4))
