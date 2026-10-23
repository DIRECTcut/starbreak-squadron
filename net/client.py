"""LAN client: send inputs at 60 Hz, rebuild the world from 30 Hz snapshots.

The client runs a normal pygame window and reuses the game's render path.
``ClientGame`` subclasses ``Game`` with ``update()`` replaced by snapshot
application: player/enemy positions are interpolated ~100 ms behind the host
clock so movement reads smoothly at 60 fps even though only 30 full states
per second arrive over the wire.

Client-side cosmetics (engine sfx, stars, particle decay, damage flashes)
run locally; anything gameplay-relevant (damage, kills, drops, waves) only
ever comes from the host.
"""
import math
import random
import threading
import time
from collections import deque

import pygame

from config import W, H, FPS
from weapons import WEAPONS, WeaponSpec
from player import Player
from enemies import Enemy
from entities import Bolt, Orb
from net.protocol import encode, LineBuffer
from game import Game

LAN_KINDS = ["scout", "gunner", "brute", "dart"]
_SNAP_LAG = 0.10          # seconds behind the host clock we render
ENEMY_BOLT_SPEC = WeaponSpec(slot="e", name="E", fire_interval=1.0,
                             damage=0, bolt_speed=400, bolt_radius=4,
                             color=(255, 150, 120))
V2 = pygame.math.Vector2


class Remote:
    """Socket + background reader. One reader thread, one input writer."""

    def __init__(self, addr, port, name, timeout=5.0):
        self.sock = socket_connect(addr, port, timeout)
        self.sock.setsockopt(__import__("socket").IPPROTO_TCP,
                             __import__("socket").TCP_NODELAY, 1)
        self.name = name
        self.stream = LineBuffer()
        self.snaps = deque(maxlen=6)     # recent snapshots (host t asc)
        self.roster = None               # {"pid":..., "names":[...]}
        self.bye = None                  # set when the connection dies
        self._send_lock = threading.Lock()
        self._pending_out = b""

        self.sock.settimeout(0.25)
        self.sock.sendall(encode({"k": "join", "name": name}))

    def start(self):
        threading.Thread(target=self._recv_loop, daemon=True).start()

    def _recv_loop(self):
        import socket as _sock
        while True:
            try:
                data = self.sock.recv(65536)
            except _sock.timeout:
                continue          # >250ms with no data: still connected, keep waiting
            except OSError:
                if self.bye is None:
                    self.bye = "connection lost"
                break
            if not data:
                self.bye = self.bye or "host left"
                break
            for msg in self.stream.feed(data):
                self._handle(msg)
            if self.bye:
                break

    def _handle(self, msg):
        k = msg.get("k")
        if k == "roster":
            self.roster = msg
        elif k == "snap":
            self.snaps.append(msg)
        elif k == "bye":
            self.bye = msg.get("reason", "host left")

    def send_input(self, mv, wp):
        """Send the latest input. Non-blocking; stale bytes get dropped."""
        line = encode({"k": "in",
                       "mv": [round(float(mv[0]), 2), round(float(mv[1]), 2)],
                       "wp": int(wp) if wp is not None else -1})
        try:
            with self._send_lock:
                self._pending_out = self._pending_out[-512:] + line
                self.sock.settimeout(0.05)
                self.sock.sendall(self._pending_out)
                self._pending_out = b""
        except OSError:
            pass  # buffer full: the next input supersedes this one

    def take_snaps(self):
        while self.snaps:
            s = self.snaps.popleft()
            yield s

    def close(self):
        try:
            self.sock.sendall(encode({"k": "bye"}))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def socket_connect(addr, port, timeout):
    import socket
    s = socket.create_connection((addr, port), timeout=timeout)
    s.setblocking(False)
    return s


class ClientGame(Game):
    """Render-only game: update() applies host snapshots, never simulates."""

    def __init__(self, addr, port, name, mute=False, bot=""):
        super().__init__(autopilot=False, mute=mute, bot=bot,
                         log_sessions=False)
        self.can_restart = False        # render hides "R to relaunch"
        self.remote = Remote(addr, port, name)
        self.remote.start()
        self.remote_pid = None
        self._roster_done = False
        self.run_id = -1                # force world rebuild on first snap
        self._players = {}              # pid -> Player (long-lived identity)
        self._enemies = {}              # id  -> Enemy
        self._server_t = 0.0
        self._seen_bolts = set()
        self._prev_local_def = None
        self._local_bot = bot
        self._pending_wp = None

    # ---------------------------------------------------------- snapshot in
    def _consume(self):
        latest = self.remote.snaps[-1] if self.remote.snaps else None
        if latest is not None:
            self._server_t = float(latest.get("t", 0.0))
        for s in self.remote.take_snaps():
            self._apply(s)

    def _local(self):
        return self._players.get(self.remote_pid)

    def _local_name(self):
        if self.remote.roster:
            names = self.remote.roster["names"]
            if self.remote_pid is not None and self.remote_pid < len(names):
                return names[self.remote_pid]
        return None

    def _apply(self, s):
        if self.remote_pid is None and self.remote.roster is not None:
            self.remote_pid = int(self.remote.roster["pid"])
            self._roster_done = True

        if s["rs"] != self.run_id:
            self._world_reset()
            self.run_id = s["rs"]

        self.state = s["st"]
        self.score, self.kills = s["sc"]
        self.waves.wave = s["w"]
        self.shake_t = max(0.0, s["shk"])
        an = s["an"]
        if an[0] and an[1] > 0.0:
            self.announce_text = an[0]
            self.announce_t = an[1]

        self._apply_fx(s["fx"])
        self._apply_players(s["pl"])
        self._apply_enemies(s["en"])
        self._apply_bolts(s["bl"])
        self._apply_orbs(s["or"])

    def _world_reset(self):
        self._enemies.clear()
        self._seen_bolts.clear()
        self.bolts = []
        self.orbs = []
        self.particles = []
        self.rings = []
        self._prev_local_def = None

    # ---- fx: local cosmetics triggered by host events
    def _apply_fx(self, fx):
        lp = self._local()
        me = lp.pos.copy() if lp is not None else V2(-999, -999)
        for ev in fx:
            if not ev:
                continue
            if ev[0] == "boom":
                pos = V2(ev[1], ev[2])
                self.explode(pos, (255, 180, 90), count=14, speed=260)
                d = max(0.0, me.distance_to(pos) - 200.0)
                self.sfx.play("explosion", volume=max(0.25, 1.0 - d / 900.0))
            elif ev[0] == "wave":
                self.sfx.play("wave", volume=0.7)

    def _apply_players(self, rows):
        for row in rows:
            (pid, x, y, vx, vy, sh, ar, hu, alive, downed, rp, wp_i, hd,
             name, score, kills, fired, hit, flash, stimer) = row
            p = self._players.get(pid)
            if p is None:
                p = Player((x, y), pid, name, None)
                self._players[pid] = p
                p._net_prev = V2(x, y)
                p._net_prev_t = self._server_t
                p._net_t = self._server_t
            else:
                p._net_prev = p.pos.copy()
                p._net_prev_t = p._net_t
                p._net_t = self._server_t
            p.pos.update(x, y)
            p.vel.update(vx, vy)
            p.shield, p.armor, p.hull = float(sh), float(ar), float(hu)
            p.alive, p.downed = bool(alive), bool(downed)
            p.revive_progress = rp
            p.weapon_idx = wp_i
            p.heading = hd
            p.name = name
            p.score, p.kills = int(score), int(kills)
            p.shots_fired, p.shots_hit = int(fired), int(hit)
            p.hit_flash = max(0.0, flash)
            p.shield_timer = stimer
            p.bot = self._local_bot if pid == self.remote_pid else None
            # local-only: detect defense drops for hit feedback + sfx
            if pid == self.remote_pid:
                prev = self._prev_local_def
                cur = (sh, ar, hu)
                if prev is not None and p.alive:
                    if cur[0] < prev[0]:
                        p.hit_flash = max(p.hit_flash, 0.3)
                        self.sfx.play("shield_hit", volume=0.55)
                    elif cur[1] < prev[1]:
                        p.hit_flash = max(p.hit_flash, 0.45)
                        self.sfx.play("damage", volume=0.75)
                        self.shake_t = max(self.shake_t, 0.07)
                    elif cur[2] < prev[2]:
                        p.hit_flash = max(p.hit_flash, 0.8)
                        self.sfx.play("damage", volume=1.0)
                        self.shake_t = max(self.shake_t, 0.14)
                self._prev_local_def = cur
        # player list order: local first (HUD/property/extras key off index 0)
        local = self._local()
        self.players = [local] if local is not None else []
        for pid in sorted(self._players):
            p = self._players[pid]
            if p is not self.players[0]:
                self.players.append(p)
        for p in self.players:
            self._interp_pos(p)

    def _interp_pos(self, p):
        t0 = getattr(p, "_net_prev_t", 0.0)
        t1 = getattr(p, "_net_t", 0.0)
        if t1 <= t0:
            return
        a = (self._server_t - _SNAP_LAG - t0) / (t1 - t0)
        a = max(0.0, min(1.0, a))
        prev = getattr(p, "_net_prev", None)
        if prev is not None:
            p.pos = prev.lerp(p.pos, a)

    def _apply_enemies(self, rows):
        seen = set()
        for row in rows:
            eid, ki, x, y, hp, mhp, ang, flash = row
            seen.add(eid)
            e = self._enemies.get(eid)
            if e is None:
                kind = LAN_KINDS[ki] if 0 <= ki < len(LAN_KINDS) else "scout"
                e = Enemy(kind, (x, y))
                e.id = eid
                self._enemies[eid] = e
            e.pos.update(x, y)
            e.hp, e.max_hp = hp, mhp
            e.angle = ang
            e.hit_flash = 0.15 if flash else e.hit_flash
        for eid in [i for i in self._enemies if i not in seen]:
            self._enemies.pop(eid)
        self.enemies = list(self._enemies.values())

    def _apply_bolts(self, rows):
        bolts = []
        for row in rows:
            bid, owner, slot, x, y, vx, vy, r, c0, c1, c2 = row
            if 0 <= slot < len(WEAPONS):
                spec = WEAPONS[slot]
            else:
                spec = ENEMY_BOLT_SPEC
            ang = math.atan2(vy, vx)
            b = Bolt((x, y), ang, spec, owner >= 0)
            b.vel.update(vx, vy)
            b.id = bid
            b.radius = r
            b.owner_pid = owner
            bolts.append(b)
            if owner == self.remote_pid:
                if bid not in self._seen_bolts:
                    self.sfx.play(spec.sound, volume=0.5)
                self._seen_bolts.add(bid)
            else:
                self._seen_bolts.add(bid)
            if len(self._seen_bolts) > 400:
                self._seen_bolts = set(list(self._seen_bolts)[-150:])
        self.bolts = bolts

    def _apply_orbs(self, rows):
        self.orbs = [Orb((x, y), v) for (x, y, v) in rows]

    # ---------------------------------------------------------- frame
    def update(self, dt):
        self.t += dt
        self._consume()
        self.fx = []   # the host is the only source of fx events
        lp = self._local()
        vel = lp.vel if (lp is not None and lp.alive) else V2(0, 0)
        self.stars.update(vel, dt)
        self._update_particles(dt)
        for r in self.rings:
            r[2] -= dt
        self.rings = [r for r in self.rings if r[2] > 0]
        self.shake_t = max(0.0, self.shake_t - dt)
        self.announce_t = max(0.0, self.announce_t - dt)
        if self.state == "play" and lp is not None and lp.alive and \
                (lp.move_dir.x or lp.move_dir.y):
            if random.random() < 0.5:
                tail = lp.pos - lp.vel * 0.05
                self.add_particle(
                    tail + V2(random.uniform(-3, 3), random.uniform(-3, 3)),
                    -lp.vel * 0.25 + V2(random.uniform(-15, 15),
                                        random.uniform(-15, 15)),
                    0.3, (90, 220, 255), 2)
        # cheap bolt drift between snapshots (homing already baked in)
        for b in self.bolts:
            b.pos += b.vel * dt
            b.life -= dt

    # ---------------------------------------------------------- input
    def compute_input(self):
        lp = self._local()
        if lp is None:
            return (0.0, 0.0)
        if self._local_bot:
            return self.autopilot_input(lp)
        keys = pygame.key.get_pressed()
        mv = V2(0, 0)
        if keys[pygame.K_LEFT] or keys[pygame.K_a]:
            mv.x -= 1
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
            mv.x += 1
        if keys[pygame.K_UP] or keys[pygame.K_w]:
            mv.y -= 1
        if keys[pygame.K_DOWN] or keys[pygame.K_s]:
            mv.y += 1
        if mv.length() > 1:
            mv = mv.normalize()
        return (mv.x, mv.y)

    # ---------------------------------------------------------- run loop
    def run(self):
        running = True
        run_t = 0.0
        while running:
            dt = min(1.0 / 20.0, self.clock.tick(FPS) / 1000)
            run_t += dt
            if self._shot_path is not None and self._shot_t is not None \
                    and run_t >= self._shot_t:
                pygame.image.save(self.screen, self._shot_path)
                print("screenshot saved:", self._shot_path)
                self._shot_path = None
            if self._auto_quit and run_t >= self._auto_quit:
                running = False
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    quit_keys = (event.key in (pygame.K_q, pygame.K_w) and
                                 (event.mod & (pygame.KMOD_META |
                                               pygame.KMOD_CTRL)))
                    if quit_keys:
                        running = False
                    elif self._local_bot is None and self.state == "play":
                        if event.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                            self._pending_wp = event.key - pygame.K_1
                        elif event.key == pygame.K_ESCAPE:
                            running = False
                    elif event.key == pygame.K_ESCAPE and self.state in \
                            ("menu", "over"):
                        running = False
            if running:
                # input keeps flowing even in the wait/standby screen so the
                # host's idle timeout can't see us as dead
                if self.remote_pid is not None:
                    wp = self._pending_wp
                    self._pending_wp = None
                    self.remote.send_input(self.compute_input(), wp)
                self.update(dt)
                self.render()
                self._draw_lan_overlay()
                pygame.display.flip()
            if self.remote.bye is not None:
                running = False
        self.remote.close()
        pygame.quit()

    # ---------------------------------------------------------- render
    def _dark(self, a):
        ov = pygame.Surface((W, H), pygame.SRCALPHA)
        ov.fill((0, 10, 30, int(255 * a)))
        self.screen.blit(ov, (0, 0))

    def _center_text(self, font, text, col, y, bold=False):
        from hud import text as hud_text
        hud_text(self.screen, text, font.get_height(), col, W // 2, y,
                 align="center", bold=bold)

    def _draw_lan_overlay(self):
        from hud import _font
        font_s, font_m, font_big = _font(16), _font(20), _font(34, True)

        if self.remote.bye:
            self._dark(0.6)
            self._center_text(font_big, "CONNECTION LOST", (255, 120, 110),
                              H // 2 - 30, bold=True)
            self._center_text(font_m, (self.remote.bye or "host left").upper() +
                              "   -   ESC to close window", (230, 245, 255),
                              H // 2 + 14)
            return
        if self.state == "menu":
            if self.remote_pid is not None:
                nm = self._local_name() or "?"
                self._dark(0.45)
                self._center_text(font_big, "STANDBY",
                                  (150, 230, 255), H // 2 - 110, bold=True)
                self._center_text(font_m, "connected as   " + nm,
                                  (129, 244, 255), H // 2 - 66)
                roster = (self.remote.roster or {}).get("names", ["?"])
                y = H // 2 - 24
                for i, n in enumerate(roster):
                    tag = "   (you)" if i == self.remote_pid else ""
                    col = (129, 244, 255) if i == self.remote_pid \
                          else (235, 245, 255)
                    self._center_text(font_s, "%d.   %s%s" % (i + 1, n, tag),
                                      col, y)
                    y += 28
                self._center_text(font_s,
                                  "waiting for the host to launch    CMD/CTRL+Q to leave",
                                  (148, 170, 196), y + 20)
            else:
                self._center_text(font_m, "connecting to host...",
                                  (148, 170, 196), H // 2)
            return

    def render(self):
        from hud import _font
        font_m, font_big = _font(20), _font(34, True)

        if self.state == "menu":
            s = self.screen
            s.blit(self.nebula, (0, 0))
            s.fill((2, 10, 26, 120))
            return
        super().render()
        if self.state == "pause":
            # pause is host-controlled: no solo key hints
            self._center_text(font_big, "PAUSED BY HOST", (255, 235, 140),
                              H // 2 - 26, bold=True)
            self._center_text(font_m, "waiting for the host to resume",
                              (230, 240, 255), H // 2 + 22)
