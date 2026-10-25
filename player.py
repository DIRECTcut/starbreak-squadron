"""The player ship: inertial flight, weapon system, layered defense, revive state."""
import math

import pygame

from config import (
    W, H, PLAYER_RADIUS, PLAYER_ACCEL, PLAYER_DRAG, PLAYER_MAX_SPEED,
    AIM_RANGE, SHIELD_MAX, SHIELD_REGEN, SHIELD_REGEN_DELAY,
    ARMOR_MAX, HULL_MAX, CONTACT_DMG, CONTACT_CD,
    REVIVE_HULL_FRAC,
)
from weapons import WEAPONS
from entities import norm, next_id


class Player:
    def __init__(self, pos, pid=0, name="You", bot=None):
        self.id = next_id()
        self.pid = pid
        self.name = name
        self.bot = bot  # "weave" | "steady" | None (human / net client)
        self.pos = pygame.math.Vector2(pos)
        self.vel = pygame.math.Vector2(0, 0)
        self.radius = PLAYER_RADIUS
        self.heading = -math.pi / 2
        self.move_dir = pygame.math.Vector2(0, 0)

        # layered defense: shield -> armor -> hull
        self.shield = float(SHIELD_MAX)
        self.armor = float(ARMOR_MAX)
        self.hull = float(HULL_MAX)
        self.shield_timer = SHIELD_REGEN_DELAY  # start regenerating immediately

        self.weapon_idx = 0
        self.target_rank = None       # None = auto (nearest); int = manual rank
        self.fire_cd = 0.0
        self.hit_flash = 0.0
        self.contact_cd = 0.0
        self.alive = True
        self.downed = False
        self.revive_progress = 0.0
        self.total_damage_taken = 0.0
        self.total_damage_dealt = 0.0
        self.absorbed = {"shield": 0.0, "armor": 0.0, "hull": 0.0}
        self.hits_taken = 0
        self.shots_fired = 0
        self.shots_hit = 0
        self.kills = 0
        self.score = 0

    # ---- defense pipeline ----
    def take_damage(self, world, amount):
        """Apply damage: shield (no overshoot) -> armor -> hull. Returns True if killed/downed."""
        if self.downed:
            # the downed body is not killable; hitting it interrupts the revive
            self.revive_progress = 0.0
            world.on_damage_downed(self, amount)
            return False
        self.total_damage_taken += amount
        self.shield_timer = SHIELD_REGEN_DELAY
        self.hit_flash = max(self.hit_flash, 0.35)
        amt, layer = amount, None
        if self.shield > 0:
            a = min(self.shield, amt)
            self.absorbed["shield"] += a
            if layer is None:
                layer = "shield"
            self.shield -= a
            amt -= a
        if amt > 0 and self.armor > 0:
            a = min(self.armor, amt)
            self.absorbed["armor"] += a
            if layer is None:
                layer = "armor"
            self.armor -= a
            amt -= a
        if amt > 0:
            a = min(self.hull, amt)
            self.absorbed["hull"] += a
            if layer is None:
                layer = "hull"
            self.hull = max(0.0, self.hull - amt)
        self.hits_taken += 1
        world.on_player_hit(amount, layer or "hull", self)
        if self.hull <= 0:
            self.alive = False
            self.downed = True
            self.revive_progress = 0.0
            self.vel = pygame.math.Vector2(0, 0)
            world.on_player_death(self)
        return not self.alive

    def revive(self):
        """Revived by a teammate: half hull, no shield/armor."""
        self.downed = False
        self.alive = True
        self.hull = HULL_MAX * REVIVE_HULL_FRAC
        self.shield = 0.0
        self.armor = 0.0
        self.shield_timer = SHIELD_REGEN_DELAY
        self.revive_progress = 0.0
        self.vel = pygame.math.Vector2(0, 0)
        self.hit_flash = 0.0

    def repair(self, pts):
        """Salvage economy: score first, partial repair second."""
        pts = pts * 0.4  # salvage is mostly score; repair is a bonus
        a = min(pts, SHIELD_MAX - self.shield)
        self.shield += a
        pts -= a
        a = min(pts, ARMOR_MAX - self.armor)
        self.armor += a
        pts -= a
        a = min(pts, HULL_MAX - self.hull)
        self.hull += a
        pts -= a

    # ---- targeting: auto-lock, with manual next/prev pinning ----
    def target(self, enemies):
        cands = [e for e in enemies
                 if not e.dead and (e.pos - self.pos).length_squared() < AIM_RANGE ** 2]
        if not cands:
            return None
        # rank is evaluated live against a distance ordering, so "next/prev"
        # always means the next-/prev-nearest ship, and a locked target that
        # dies hands the rank straight to whatever becomes that distance slot
        cands.sort(key=lambda e: e.pos.distance_squared_to(self.pos))
        if self.target_rank is None:
            return cands[0]          # auto: nearest
        return cands[self.target_rank % len(cands)]

    def cycle_target(self, delta):
        """Step the manual lock. From auto, the first press pins the ship we
        were already auto-aiming (no jump); further presses step away from it."""
        if self.target_rank is None:
            self.target_rank = 0
        else:
            self.target_rank += delta    # negative wraps via % when selected

    def clear_target(self):
        self.target_rank = None        # back to auto-lock

    # ---- update ----
    def update(self, world, move_dir, dt):
        if self.downed:
            return  # downed ships are inert (channel runs in Game.update)
        self.move_dir = move_dir
        self.hit_flash = max(0.0, self.hit_flash - dt)
        self.contact_cd = max(0.0, self.contact_cd - dt)
        self.fire_cd -= dt

        # inertial flight
        self.vel += move_dir * PLAYER_ACCEL * dt
        self.vel *= max(0.0, 1.0 - PLAYER_DRAG * dt)
        if self.vel.length() > PLAYER_MAX_SPEED:
            self.vel = self.vel / self.vel.length() * PLAYER_MAX_SPEED
        self.pos += self.vel * dt

        # arena bounds
        m = 18
        if self.pos.x < m:
            self.pos.x, self.vel.x = m, max(0.0, self.vel.x)
        elif self.pos.x > W - m:
            self.pos.x, self.vel.x = W - m, min(0.0, self.vel.x)
        if self.pos.y < m:
            self.pos.y, self.vel.y = m, max(0.0, self.vel.y)
        elif self.pos.y > H - m:
            self.pos.y, self.vel.y = H - m, min(0.0, self.vel.y)

        # shield regeneration (delayed)
        self.shield_timer -= dt
        if self.shield < SHIELD_MAX and self.shield_timer <= 0:
            self.shield = min(SHIELD_MAX, self.shield + SHIELD_REGEN * dt)

        # heading tracks aim, falls back to motion
        tgt = self.target(world.enemies)
        if tgt is not None:
            want = math.atan2(tgt.pos.y - self.pos.y, tgt.pos.x - self.pos.x)
        elif self.vel.length() > 30:
            want = math.atan2(self.vel.y, self.vel.x)
        else:
            want = self.heading
        d = math.atan2(math.sin(want - self.heading), math.cos(want - self.heading))
        self.heading = self.heading + d * min(1.0, 10.0 * dt)

        # autofire (Everspace-style auto-lock fire)
        spec = WEAPONS[self.weapon_idx]
        if tgt is not None and self.fire_cd <= 0 and self.alive:
            self._fire(world, spec, tgt)
            self.fire_cd = spec.fire_interval

        # thrust particles
        if move_dir.length_squared() > 0.01:
            back = self.pos - norm(move_dir) * 22
            world.add_particle(back, -move_dir * 120, 0.25, (120, 200, 255), 3)
            world.add_particle(back, -move_dir * 60, 0.2, (255, 160, 60), 2)

    # ---- firing ----
    def _fire(self, world, spec, target):
        muzzle = self.pos + pygame.math.Vector2(
            math.cos(self.heading), math.sin(self.heading)) * 24
        for i in range(spec.pellets):
            if spec.pellets > 1:
                t = i / (spec.pellets - 1) - 0.5
                off = math.radians(spec.spread_deg) * t
            else:
                off = 0.0
            world.spawn_player_bolt(muzzle, self.heading + off, spec, target, owner=self)
        world.sfx.play(spec.sound)
        world.stats["bullets_fired"] += spec.pellets
        world.counters["pfired"] += spec.pellets
        self.shots_fired += spec.pellets

    def register_hit(self):
        self.shots_hit += 1

    def accuracy(self):
        return self.shots_hit / self.shots_fired if self.shots_fired else 0.0

    # ---- contact damage ----
    def contact_hit(self, world, direction, dmg=CONTACT_DMG):
        if self.downed:
            self.revive_progress = 0.0  # ramming a downed body interrupts revive
            return
        if self.contact_cd <= 0 and self.alive:
            self.contact_cd = CONTACT_CD
            self.vel -= direction * 220
            self.take_damage(world, dmg)

    def draw(self, surf, t, is_local=True):
        x, y = int(self.pos.x), int(self.pos.y)
        a = self.heading
        cos, sin = math.cos(a), math.sin(a)

        def pt(dx, dy):
            return (x + int(dx * cos - dy * sin), y + int(dx * sin + dy * cos))

        # downed ship: smoldering wreck + revive progress ring (drawn in Game)
        if self.downed:
            pulse = 4 + 2 * int(abs(math.sin(self.revive_progress * 1.2)))
            pygame.draw.circle(surf, (255, 120, 60), (x, y), 10, 2)
            pygame.draw.circle(surf, (255, 120, 60), (x, y), 4)
            pygame.draw.line(surf, (255, 90, 60), (x - 8, y - 8), (x + 8, y + 8), 2)
            pygame.draw.line(surf, (255, 90, 60), (x + 8, y - 8), (x - 8, y + 8), 2)
            return

        body = [(28, 0), (-14, 13), (-8, 0), (-14, -13)]
        poly = [pt(*p) for p in body]
        glow = getattr(self, "_glow", None)
        if glow is None:
            glow = pygame.Surface((64, 64), pygame.SRCALPHA)
            for i in range(26, 0, -2):
                a = int(60 * (1 - i / 26))
                pygame.draw.circle(glow, (40, 120, 200, a), (32, 32), i)
            self._glow = glow
        surf.blit(glow, (x - 32, y - 32))
        body_col = (150, 210, 245) if self.hit_flash > 0.15 and int(t * 30) % 2 else (170, 225, 250)
        pygame.draw.polygon(surf, (50, 90, 140), poly)
        pygame.draw.polygon(surf, body_col, poly, 2)
        pygame.draw.circle(surf, (210, 245, 255), (x, y), 4)

        # wing accents
        for sgn in (1, -1):
            wing = [pt(-4, 6 * sgn), pt(-13, 12 * sgn), pt(-2, 7 * sgn)]
            pygame.draw.polygon(surf, (90, 150, 210), wing)