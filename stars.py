"""Parallax starfield with twinkle."""
import math
import random

import pygame

from config import W, H


class Starfield:
    def __init__(self, seed=None):
        rng = random.Random(seed)
        # (count, parallax speed, size, base brightness)
        self.layers = []
        spec = [(130, 0.05, 1, 80), (70, 0.13, 2, 130), (36, 0.28, 2, 200)]
        for count, speed, size, bright in spec:
            stars = []
            for _ in range(count):
                stars.append({
                    "x": rng.uniform(0, W), "y": rng.uniform(0, H),
                    "size": size, "base": bright,
                    "ph": rng.uniform(0, 6.28), "tw": rng.uniform(0.6, 2.4),
                })
            self.layers.append((speed, stars))

    def update(self, vel, dt):
        for speed, stars in self.layers:
            dx, dy = -vel.x * speed * dt, -vel.y * speed * dt
            if dx or dy:
                for s in stars:
                    s["x"] = (s["x"] + dx) % W
                    s["y"] = (s["y"] + dy) % H

    def draw(self, surf, t):
        for speed, stars in self.layers:
            twk = speed * 260 + 40
            for s in stars:
                b = int(s["base"] + twk * 0.5 * math.sin(t * s["tw"] + s["ph"]))
                b = max(30, min(255, b))
                pygame.draw.rect(surf, (b, b, min(255, b + 15)),
                                 (int(s["x"]), int(s["y"]), s["size"], s["size"]))


def make_nebula():
    """Pre-rendered translucent nebula blobs."""
    surf = pygame.Surface((W, H), pygame.SRCALPHA)
    blobs = [
        (210, 160, 250, (70, 40, 130)), (1060, 520, 300, (25, 60, 100)),
        (720, 130, 190, (100, 35, 90)), (380, 610, 240, (30, 55, 105)),
        (1160, 140, 150, (80, 45, 105)), (640, 420, 200, (45, 35, 95)),
    ]
    for bx, by, r, (cr, cg, cb) in blobs:
        for i in range(r, 0, -3):
            a = int(28 * (1 - i / r))
            pygame.draw.circle(surf, (cr, cg, cb, a), (bx, by), i)
    return surf
