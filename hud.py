import math

import pygame

_fonts = {}


def _font(size, bold=False):
    key = (size, bold)
    f = _fonts.get(key)
    if f is None:
        f = pygame.font.Font(None, size)
        f.set_bold(bold)
        _fonts[key] = f
    return f


def text(surf, s, size, color, x, y, align="left", bold=False):
    img = _font(size, bold).render(s, True, color)
    if align == "left":
        r = img.get_rect(topleft=(x, y))
    elif align == "right":
        r = img.get_rect(topright=(x, y))
    else:
        r = img.get_rect(center=(x, y))
    surf.blit(img, r)


def bar(surf, x, y, w, h, frac, color, label, value_text):
    frac = max(0.0, min(1.0, frac))
    text(surf, label, 15, (150, 165, 190), x, y - 14)
    text(surf, value_text, 15, (150, 165, 190), x + w, y - 14, align="right")
    pygame.draw.rect(surf, (25, 30, 45), (x, y, w, h))
    pygame.draw.rect(surf, (70, 80, 105), (x, y, w, h), 1)
    if frac > 0:
        inner = (x + 1, y + 1, int((w - 2) * frac), max(1, h - 2))
        pygame.draw.rect(surf, tuple(c // 3 for c in color), (x, y, w, h))
        pygame.draw.rect(surf, color, inner)


def draw_hud(surf, g):
    from config import W, H, SHIELD_MAX, ARMOR_MAX, HULL_MAX
    from weapons import WEAPONS

    p = g.player
    # top-left: wave + score
    text(surf, f"WAVE {max(1, g.waves.wave)}", 34, (220, 230, 250), 26, 22, bold=True)
    text(surf, f"SCORE {g.score}", 22, (140, 200, 160), 26, 56)

    # bottom-left: defense layers
    bx, by = 26, H - 108
    bw = 240
    bar(surf, bx, by, bw, 11, p.shield / SHIELD_MAX, (80, 205, 255),
        "SHIELD", f"{int(p.shield)}")
    bar(surf, bx, by + 26, bw, 11, p.armor / ARMOR_MAX, (255, 175, 70),
        "ARMOR", f"{int(p.armor)}")
    hcol = (120, 235, 120) if p.hull > 35 else (255, 90, 90)
    bar(surf, bx, by + 52, bw, 11, p.hull / HULL_MAX, hcol,
        "HULL", f"{int(p.hull)}")
    # regen pip
    if p.shield < SHIELD_MAX and p.shield_timer <= 0:
        text(surf, "REGEN", 13, (80, 205, 255), bx + bw + 10, by)

    # bottom-right: weapons
    for i in reversed(range(len(WEAPONS))):
        spec = WEAPONS[i]
        active = i == p.weapon_idx
        rw, rh, gap = 170, 24, 6
        x = W - 26 - (rw + gap) * (len(WEAPONS) - i)
        y = H - 26 - rh - (len(WEAPONS) - 1 - i) * (rh + gap)
        col = spec.color
        bg = (40, 48, 70) if active else (22, 26, 40)
        pygame.draw.rect(surf, bg, (x, y, rw, rh))
        pygame.draw.rect(surf, col if active else (70, 80, 105), (x, y, rw, rh), 2 if active else 1)
        label = f"[{spec.slot}] {spec.name}"
        text(surf, label, 17, col if active else (120, 130, 155),
             x + 10, y + rh // 2, align="left")

    # kills (top-right)
    text(surf, f"KILLS {g.kills}", 20, (150, 160, 185), W - 26, 22, align="right")

    # announcement
    if g.announce_t > 0 and g.announce_text:
        a = min(1.0, g.announce_t)
        text(surf, g.announce_text, 64, (120 + 100 * a, 160 + 80 * a, 255),
             W // 2, H // 2 - 120, align="center", bold=True)


def draw_player_extras(surf, g, ox=0, oy=0):
    """In-world identity: name tag + compact defense bars over each ship,
    revive-progress ring over downed bodies."""
    from config import SHIELD_MAX, ARMOR_MAX, HULL_MAX, REVIVE_CHANNEL

    for i, p in enumerate(g.players):
        x, y = int(p.pos.x) + ox, int(p.pos.y) + oy
        if p.downed:
            frac = p.revive_progress / REVIVE_CHANNEL
            r = 26
            pygame.draw.circle(surf, (255, 160, 90), (x, y), r, 1)
            if frac > 0:
                pygame.draw.arc(surf, (120, 255, 170),
                                pygame.Rect(x - r, y - r, 2 * r, 2 * r),
                                -math.pi / 2, -math.pi / 2 + 2 * math.pi * frac, 4)
            text(surf, f"{p.name} DOWN", 15, (255, 170, 90), x, y + r + 8, align="center")
            text(surf, f"REVIVE {int(frac * 100)}%", 14, (150, 255, 180), x, y + r + 24,
                 align="center")
            continue
        # name tag: local player cyan, teammates gold
        col = (170, 235, 255) if i == 0 else (255, 225, 150)
        label = p.name + ("  [YOU]" if i == 0 and len(g.players) > 1 else "")
        if getattr(p, "target_manual", False):
            label += "  [LOCK]"
        text(surf, label, 15, col, x, y - 42, align="center", bold=(i == 0))
        # compact stacked bars
        bx, bw, by = x - 22, 44, y - 32
        for frac, c in ((p.shield / SHIELD_MAX, (80, 205, 255)),
                        (p.armor / ARMOR_MAX, (255, 175, 70)),
                        (p.hull / HULL_MAX, (120, 235, 120) if p.hull > 35 else (255, 90, 90))):
            frac = max(0.0, min(1.0, frac))
            if frac > 0:
                pygame.draw.rect(surf, c, (bx, by, int(bw * frac), 3))
            by += 5


def draw_menu(surf, g, t):
    from config import W, H
    # darken
    ov = pygame.Surface((W, H), pygame.SRCALPHA)
    ov.fill((4, 6, 14, 170))
    surf.blit(ov, (0, 0))

    cy = H // 2
    text(surf, "ASTRAL VECTOR", 92, (150, 215, 255), W // 2, cy - 130, align="center", bold=True)
    text(surf, "a pocket Everspace-style spaceflight shooter", 22, (120, 140, 175),
         W // 2, cy - 72, align="center")
    lines = [
        "WASD / ARROWS ......... flight (inertial thrust)",
        "AUTO .................. weapons auto-lock the nearest ship",
        "[  /  ] ............... manual lock: previous / next target",
        "T ..................... back to auto-lock",
        "1 / 2 / 3 ............. pulse blaster / scattergun / homing missiles",
        "P ..................... pause",
        "                   ",
        "shields regen when unscathed. salvage repairs armor + hull.",
        "                   ",
        "DIFFICULTY: %s" % (g.difficulty.upper()
                            if getattr(g, "difficulty", None) else "STANDARD"),
        "1=easy   2=standard   3=hard   (choose in the menu)",
    ]
    for i, line in enumerate(lines):
        if line.strip():
            c = (255, 225, 150) if line.startswith("DIFFICULTY") else (160, 175, 205)
            text(surf, line, 22, c, W // 2, cy - 10 + i * 30, align="center",
                 bold=line.startswith("DIFFICULTY"))
    if int(t * 2) % 2 == 0:
        text(surf, "PRESS  ENTER  TO  LAUNCH", 30, (255, 235, 140), W // 2, cy + 230,
             align="center", bold=True)


def draw_pause(surf, g=None):
    from config import W, H
    ov = pygame.Surface((W, H), pygame.SRCALPHA)
    ov.fill((4, 6, 14, 140))
    surf.blit(ov, (0, 0))
    text(surf, "PAUSED", 72, (200, 215, 240), W // 2, H // 2 - 190, align="center", bold=True)
    lines = [
        "WASD / ARROWS ....... flight (inertial thrust)",
        "1 / 2 / 3 ........... pulse / scatter / homing missiles",
        "[  /  ] ............. manual lock: previous / next target",
        "T ................... back to auto-lock (nearest ship)",
        "P / ESC ............. pause / resume",
        "R ................... restart the run",
    ]
    for i, line in enumerate(lines):
        text(surf, line, 22, (160, 175, 205), W // 2, H // 2 - 90 + i * 30, align="center")
    if g is not None and getattr(g, "difficulty", None):
        text(surf, "difficulty: %s" % g.difficulty.upper(), 22, (140, 155, 185),
             W // 2, H // 2 - 90 + len(lines) * 30 + 12, align="center")
    text(surf, "P / ENTER to resume", 24, (140, 155, 185), W // 2, H // 2 + 210, align="center")


def draw_gameover(surf, g, t):
    from config import W, H
    ov = pygame.Surface((W, H), pygame.SRCALPHA)
    ov.fill((10, 4, 8, 185))
    surf.blit(ov, (0, 0))
    coop = len(g.players) > 1
    cy = H // 2
    text(surf, "FLEET DOWN" if coop else "SHIP DESTROYED", 72,
         (255, 110, 110), W // 2, cy - 100, align="center", bold=True)
    text(surf, f"FINAL TEAM SCORE  {g.score}", 36, (230, 240, 255), W // 2, cy - 40,
         align="center", bold=True)
    text(surf, f"waves reached {max(1, g.waves.wave)}   -   kills {g.kills}", 24,
         (150, 165, 195), W // 2, cy - 5, align="center")
    # per-pilot standings
    board = sorted(g.players, key=lambda p: -p.score)
    for i, p in enumerate(board):
        ly = cy + 32 + i * 26
        name = p.name + ("  [YOU]" if i == 0 and coop else "")
        text(surf, f"{i + 1}. {name}", 22, (255, 225, 150) if i == 0 else (190, 200, 225),
             W // 2 - 130, ly, align="left")
        text(surf, f"score {p.score:5d}   kills {p.kills:3d}   acc {int(p.accuracy() * 100):3d}%",
             22, (170, 180, 205), W // 2 + 170, ly, align="right")
    hint = ("R to relaunch    ESC for menu" if getattr(g, "can_restart", True)
            else "waiting for the host to relaunch    CMD+Q to leave")
    if int(t * 2) % 2 == 0:
        text(surf, hint, 24, (255, 235, 140), W // 2, cy + 32 + len(board) * 26 + 8,
             align="center", bold=True)


def draw_damage_overlay(surf, g):
    from config import W, H
    ov = None
    p = g.player
    if p.hit_flash > 0 and not p.alive:
        pass  # death has its own look
    if p.hit_flash > 0 and p.alive:
        a = int(110 * min(1.0, p.hit_flash / 0.35))
        ov = pygame.Surface((W, H), pygame.SRCALPHA)
        # red border flash (keeps center readable)
        ov.fill((255, 40, 40, 0))
        for i, alpha in ((0, a), (14, a // 2), (30, a // 3)):
            pygame.draw.rect(ov, (255, 40, 40, alpha), (i, i, W - 2 * i, H - 2 * i), 5 if i == 0 else 26)
    elif p.alive and p.hull <= 30:
        a = int(26 + 20 * (0.5 + 0.5 * math.sin(g.t * 5)))
        ov = pygame.Surface((W, H), pygame.SRCALPHA)
        pygame.draw.rect(ov, (255, 40, 40, a), (0, 0, W, H), 6)
    if ov is not None:
        surf.blit(ov, (0, 0))
