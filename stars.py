"""Parallax starfield with twinkle.

Stars are an infinite tiled field: each layer keeps a fixed set of stars and
renders them shifted by the camera offset, wrapped into the viewport. Flying
arbitrarily far never grows the star list (constant memory, no pop-in walls).
"""
import math
import random

import pygame

from config import W, H


class Starfield:
    def __init__(self, seed=None):
        rng = random.Random(seed)
        # (count, parallax speed, size, base brightness)
        self.layers = []
        spec = [(130, 0.10, 1, 80), (70, 0.25, 2, 130), (36, 0.50, 2, 200)]
        tw = 0.6
        for count, speed, size, bright in spec:
            stars = []
            for _ in range(count):
                stars.append({
                    "x": rng.uniform(0, W), "y": rng.uniform(0, H),
                    "size": size, "base": bright,
                    "ph": rng.uniform(0, 6.28), "tw": rng.uniform(0.6, 2.4),
                })
            self.layers.append((speed, stars))

    # kept for API compatibility (starfield is now camera-driven)
    def update(self, vel, dt):
        pass

    def draw(self, surf, t, camx=0.0, camy=0.0):
        """Draw the tiled starfield. camx/camy = world center of the viewport.

        Each layer shifts by cam*speed and wraps into the screen: flying any
        distance renders in a constant number of blits (constant memory)."""
        for speed, stars in self.layers:
            twk = speed * 260 + 40
            cx = (int(camx * speed)) % W
            cy = (int(camy * speed)) % H
            for s in stars:
                b = int(s["base"] + twk * 0.5 * math.sin(t * s["tw"] + s["ph"]))
                b = max(30, min(255, b))
                x = (int(s["x"]) - cx) % W
                y = (int(s["y"]) - cy) % H
                pygame.draw.rect(surf, (b, b, min(255, b + 15)),
                                 (x, y, s["size"], s["size"]))


def make_nebula():
    """Pre-rendered translucent nebula blobs (deep-space backdrop)."""
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
