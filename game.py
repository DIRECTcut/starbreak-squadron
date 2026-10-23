"""Core game: simulation, rendering, states, and the headless self-test harness."""
import math
import random
import time

import pygame

import config
from config import (W, H, FPS, SAMPLE_HZ, WAVE_BUMP_PER_PLAYER,
                    REVIVE_RADIUS, REVIVE_CHANNEL)
from entities import Bolt, Particle, norm
from player import Player
from enemies import Enemy
from waves import Waves
from weapons import WEAPONS, WeaponSpec
from audio import Sfx
from stars import Starfield, make_nebula
from hud import (draw_hud, draw_menu, draw_pause, draw_gameover,
                 draw_damage_overlay, draw_player_extras)
from telemetry import Session

V2 = pygame.math.Vector2


class Game:
    # -------------------------------------------------------------- setup
    def __init__(self, seed=None, autopilot=False, mute=False, bot="weave",
                 sessions_dir=config.SESSIONS_DIR, log_sessions=True):
        # bot may be a comma list: "weave" (one bot) or "weave,steady" (two bot
        # players). Human play: autopilot=False, bot="".
        self.bots = [b.strip() for b in str(bot).split(",") if b.strip()]
        autopilot = autopilot or bool(self.bots)
        if not pygame.get_init():
            try:
                pygame.mixer.pre_init(22050, -16, 1, 512)
            except Exception:
                pass
            pygame.init()
        if pygame.display.get_surface() is None:
            self.screen = pygame.display.set_mode((W, H), pygame.SRCALPHA)
        else:
            self.screen = pygame.display.get_surface()
        if self.screen is None:  # display unavailable: create offscreen fallback
            self.screen = pygame.Surface((W, H), pygame.SRCALPHA)
        pygame.display.set_caption("Astral Vector")
        self.clock = pygame.time.Clock()
        self.sfx = Sfx(mute)
        self.sfx.init()
        self.autopilot = autopilot
        self.bot = self.bots[0] if self.bots else ""
        self.seed = seed
        self.log_sessions = log_sessions
        self.sessions_dir = sessions_dir
        self.session = None
        self.sess_dt = 0.0                 # sim time since last session sample
        self.sess_sample_every = 1.0 / SAMPLE_HZ
        self.run_restarts = 0              # persists across in-run restarts
        self.net = None                    # Host object in net mode (client: None)
        self.net_inputs = {}               # pid -> ((x, y), wp|-1), host only
        self.run_id = 0                    # increments per new run (client resync)
        self.can_restart = True            # LAN clients cannot R-relaunch
        self._shot_path = None             # one-shot self-screenshot
        self._shot_t = None
        self._auto_quit = 0.0
        self.t = 0.0
        self.stars = Starfield(seed)
        self.nebula = make_nebula()
        self.state = "menu"
        self.reset()
        self.state = "menu"

    # -------------------------------------------------------------- reset
    def reset(self):
        self.net_inputs = {}
        self.fx = []                       # drained by the host per snapshot
        if self.net is not None:
            # LAN host: seat 0 = local seat (human or bot), pids 1..3 =
            # whichever remote pilots are connected.
            self.players = [Player((W / 2, H - 90), pid=0, name=("You" if not
                                                                 self.autopilot
                                                                 else "Bot 1"),
                                   bot=(self.bots[0] if self.bots else None))]
            for pid, c in sorted(self.net.clients.items()):
                x = W / 2 - 60.0 * len(self.players)
                self.players.append(Player((x, H - 60), pid=pid, name=c.name,
                                           bot=None))
        else:
            n = len(self.bots) if self.bots else 1
            self.players = []
            for i in range(n):
                ox = (i - (n - 1) / 2.0) * 90.0
                oy = 30.0 * (1 if i % 2 == 0 else -1) if n > 1 else 0.0
                name = "You" if (i == 0 and not self.autopilot) else f"Player {i + 1}"
                self.players.append(Player((W / 2 + ox, H / 2 + oy), pid=i, name=name,
                                           bot=self.bots[i] if i < len(self.bots) else None))
        self.enemies = []
        self.bolts = []
        self.orbs = []
        self.particles = []
        self.waves = Waves(self.seed)
        self.score = 0  # team score; kept in lockstep with per-player totals
        self.kills = 0
        self.rings = []  # (pos, radius, t_left, color) shockwaves
        self.shake_t = 0.0
        self.announce_text = None
        self.announce_t = 0.0
        self.death_t = 0.0
        self.stats = {
            "bullets_fired": 0,
            "orbs_collected": 0,
            "restarts": 0,
        }
        # per-sample event counters, drained by telemetry
        self.counters = {"near": 0, "pfired": 0, "phit": 0,
                         "efired": 0, "ehit": 0, "orbs": 0}

    @property
    def player(self):
        """Local/first player (HUD, solo code paths, report())."""
        return self.players[0]

    def _open_session(self):
        if not self.log_sessions:
            return
        self._close_session()
        mode = (f"bot-{'-'.join(self.bots)}" if self.autopilot
                else ("play" if len(self.players) == 1 else "coop"))
        self.session = Session(self.sessions_dir, mode,
                               {"seed": self.seed, "bot": "-".join(self.bots) or "human",
                                "players": len(self.players)})
        self.session.open()
        self.sess_dt = 0.0

    def _close_session(self):
        if self.session is not None:
            self.session.close(self)
            self.session = None

    def start(self):
        self.reset()
        self._open_session()
        self.announce("WAVE 1 INCOMING", 1.6)
        self.sfx.play("wave")
        self.state = "play"
        self.run_id += 1

    # -------------------------------------------------------------- net
    def net_player_joined(self, pid, name):
        """Remote pilot connected mid-menu/mid-run: seat them live."""
        if self._by_pid(pid) is not None:
            return
        x = W / 2 - 70.0 * len(self.players)
        self.players.append(Player((x, H - 90), pid=pid, name=name, bot=None))
        if self.state == "play":
            self.announce(name + " joined the run", 2.2)

    def net_player_left(self, pid):
        """Remote pilot left: their ship goes down (a revivable ghost)."""
        p = self._by_pid(pid)
        if p is None:
            return
        if self.state == "play" and p.alive:
            p.take_damage(self, 10000.0)
            self.announce(p.name + " lost their signal", 2.5)

    def to_menu(self):
        self._close_session()  # must read live stats before reset() zeroes them
        self.reset()
        self.state = "menu"

    # -------------------------------------------------------------- world API
    def make_enemy(self, kind, pos):
        e = Enemy(kind, pos)
        # scale with wave so late waves actually bite; each teammate adds half a
        # wave's worth of difficulty (more bodies to focus = harder).
        half_wave = WAVE_BUMP_PER_PLAYER * (len(self.players) - 1)
        m = max(0, self.waves.wave - 1 + half_wave)
        mult = 1.0 + 0.18 * m
        e.hp *= mult
        e.max_hp *= mult
        e.speed *= (1.0 + 0.06 * m)
        e.spawn_t = self.t
        return e

    def spawn_player_bolt(self, pos, angle, spec, target=None, owner=None):
        self.bolts.append(Bolt(pos, angle, spec, friendly=True, target=target,
                               owner_pid=owner.pid if owner else 0))

    def spawn_enemy_bolt(self, pos, angle, dmg, speed, color, radius=4):
        spec = WeaponSpec(
            slot="e", name="ENEMY", fire_interval=0, damage=dmg, bolt_speed=speed,
            bolt_radius=radius, color=color)
        self.bolts.append(Bolt(pos, angle, spec, friendly=False))
        self.counters["efired"] += 1

    def add_particle(self, pos, vel, life, color, size=3):
        self.particles.append(Particle(pos, vel, life, color, size))
        if len(self.particles) > config.PARTICLE_CAP:
            del self.particles[: len(self.particles) - config.PARTICLE_CAP]

    def explode(self, pos, color, count=26, speed=260.0):
        for _ in range(count):
            ang = random.uniform(0, math.tau)
            sp = random.uniform(speed * 0.2, speed)
            c = (min(255, color[0] + 60), min(255, color[1] + 60), min(255, color[2] + 60))
            self.add_particle(pos, (math.cos(ang) * sp, math.sin(ang) * sp),
                              random.uniform(0.3, 0.8), random.choice([color, c, (255, 220, 150)]),
                              random.uniform(2, 5))
        self.fx.append(["boom", int(pos[0]), int(pos[1])])

    def announce(self, text_s, t=2.2):
        self.announce_text = text_s
        self.announce_t = t

    def on_player_hit(self, amount, layer="hull", p=None):
        p = p or self.player
        self.shake_t = max(self.shake_t, min(0.5, 0.18 + amount / 300.0))
        sound = "shield_hit" if layer == "shield" else "damage"
        self.sfx.play(sound, volume=0.7 if layer == "shield" else 0.9)
        if self.session is not None:
            self.session.event(
                "hit", dmg=round(amount, 1), layer=layer,
                s=round(p.shield, 1), a=round(p.armor, 1), h=round(p.hull, 1),
                pid=p.pid)

    def on_damage_downed(self, p, amount):
        """Downed bodies are not killable; getting shot just breaks the channel."""
        self.shake_t = max(self.shake_t, 0.1)
        if self.session is not None:
            self.session.event("downed_hit", pid=p.pid, dmg=round(amount, 1))

    def on_player_death(self, p=None):
        p = p or self.player
        self.rings.append([pygame.math.Vector2(p.pos), 140, 0.7, (140, 210, 255)])
        self.explode(p.pos, (120, 200, 255), 60, 380)
        self.explode(p.pos, (255, 160, 90), 40, 300)
        self.shake_t = 1.0
        self.sfx.play("explosion", volume=1.0)
        self.sfx.play("death")
        if self.session is not None:
            self.session.event("down", pid=p.pid)

    def on_wave_start(self, n):
        self.announce(f"WAVE {n}", 2.0)
        self.sfx.play("wave")
        self.fx.append(["wave"])
        if self.session is not None:
            comp = {}
            for k in self.waves.pending:
                comp[k] = comp.get(k, 0) + 1
            self.session.wave(n, comp)

    def collect_orb(self, orb, p):
        self.score += orb.value
        p.score += orb.value
        p.repair(orb.value)
        self.stats["orbs_collected"] += 1
        self.counters["orbs"] += 1
        self.sfx.play("pickup")
        self.add_particle(orb.pos, (p.pos - orb.pos) * 0.5, 0.2, (255, 235, 150), 3)

    # ---- per-player world queries ----
    def pick_target(self, enemy):
        """Nearest living player (the one this enemy chases); None if none."""
        best, bd = None, 1e18
        for p in self.players:
            if not p.alive:
                continue
            d = (p.pos - enemy.pos).length_squared()
            if d < bd:
                best, bd = p, d
        return best

    def _nearest_alive(self, pos, cap=1e18):
        best, bd = None, cap
        for p in self.players:
            if not p.alive:
                continue
            d = (p.pos - pos).length_squared()
            if d < bd:
                best, bd = p, d
        return best

    def _by_pid(self, pid):
        for p in self.players:
            if p.pid == pid:
                return p
        return None

    # -------------------------------------------------------------- autopilot
    def autopilot_input(self, p):
        """Bots: 'weave' = competent evader; 'steady' = stands its ground
        (the difficulty probe — how long can the config keep a stationary player
        alive while it still kills?). Multi-bot: each bot keeps its own
        zone/phase so they don't stack on the same pixel."""
        n = len(self.players)
        if p.bot == "steady":
            cx = W / 2 + (p.pid - (n - 1) / 2.0) * 150.0 if n > 1 else W / 2
            d = V2(cx, H / 2) - p.pos
            if d.length() < 120:
                return V2(0, 0)
            return d / d.length()
        phase = p.pid * 2.1
        d = p.pos - V2(W / 2, H / 2)
        dist = d.length()
        if dist < 1e-6:
            return V2(math.cos(self.t + phase), math.sin(self.t + phase))
        dn = d / dist
        tang = V2(-dn.y, dn.x)
        weave = math.sin((self.t + phase) * 1.7)
        if dist > 220:
            want = -dn * 1.0 + tang * (0.7 * weave)
        else:
            want = tang * weave
        n = norm(want)
        dn_out = -dn
        return n if n.length_squared() > 0.01 else dn_out

    def player_input(self, p):
        if self.autopilot or p.bot is not None:
            return self.autopilot_input(p)
        if self.net is not None and p.pid != 0:
            # remote pilot: input arrived over the wire at 60 Hz (staged in
            # game.net_inputs by Host.tick). No key polling here.
            ni = self.net_inputs.get(p.pid)
            mv, wp = (ni[0], ni[1]) if ni else ((0.0, 0.0), -1)
            if wp is not None and wp >= 0 and p.weapon_idx != wp:
                p.weapon_idx = wp
                if self.session is not None:
                    self.session.weapon(wp)
            if not mv:
                return V2(0, 0)
            m = V2(mv[0], mv[1])
            if m.length() > 1.0:
                m = m.normalize()
            return m
        # local human player (pid 0)
        keys = pygame.key.get_pressed()
        x = (keys[pygame.K_d] or keys[pygame.K_RIGHT]) - (keys[pygame.K_a] or keys[pygame.K_LEFT])
        y = (keys[pygame.K_s] or keys[pygame.K_DOWN]) - (keys[pygame.K_w] or keys[pygame.K_UP])
        return norm(V2(x, y))

    # -------------------------------------------------------------- update
    def update(self, dt):
        self.t += dt
        self.shake_t = max(0.0, self.shake_t - dt * 2.2)
        self.announce_t = max(0.0, self.announce_t - dt)
        local = self.player
        if self.net is None:
            self.fx = []  # no host: nobody to play one-shot fx for
        self.stars.update(local.vel if local.alive else V2(0, 0), dt)

        # telemetry sampling (fixed cadence, independent of frame rate)
        if self.session is not None and self.state in ("play", "over"):
            self.sess_dt += dt
            if self.sess_dt >= self.sess_sample_every:
                self.session.sample(self)
                self.sess_dt = 0.0

        if self.state != "play":
            self._update_particles(dt)
            return

        # players: alive ships fly, downed ships wait for a teammate
        for p in self.players:
            if p.alive:
                p.update(self, self.player_input(p), dt)

        self._update_revives(dt)

        if not any(p.alive for p in self.players):
            # the run ends only when everyone is down
            self.death_t += dt
            if self.death_t > 1.5:
                self.state = "over"
                if self.session is not None:
                    self.session.death(self)
                if not self.autopilot:
                    self._close_session()
                return
        else:
            self.death_t = 0.0

        # enemies
        for e in self.enemies:
            e.update(self, dt)

        # soft separation between enemies
        for i, a in enumerate(self.enemies):
            for b in self.enemies[i + 1:]:
                d = b.pos - a.pos
                dist = d.length()
                min_d = a.radius + b.radius + 22
                if 0 < dist < min_d:
                    push = norm(d) * (min_d - dist) * 0.5
                    a.pos -= push
                    b.pos += push

        # projectiles
        for b in self.bolts:
            b.update(self, dt)
        self._resolve_bolt_collisions()

        # cull dead enemies -> rewards
        for e in [e for e in self.enemies if e.dead]:
            self.enemies.remove(e)
            self.kills += 1
            self.score += e.score
            killer = self._by_pid(e.last_hit_by)
            if killer is not None:
                killer.score += e.score
                killer.kills += 1
            if self.session is not None:
                ttm = (self.t - e.spawn_t) if e.spawn_t is not None else -1
                self.session.kill(e.kind, self.waves.wave, ttm, e.dmg_taken, e.max_hp,
                                  pid=e.last_hit_by)
            self.explode(e.pos, e.color, 18 + 8 * (e.radius // 8))
            self.shake_t = max(self.shake_t, 0.18)
            self.sfx.play("explosion", volume=0.7)
            for _ in range(e.orbs):
                self.orbs.append(_make_orb(e.pos))

        # orbs: each one is pulled toward the nearest living player
        for o in self.orbs:
            np_ = self._nearest_alive(o.pos, cap=280.0 ** 2)
            if np_ is not None:
                o.update(self, np_, dt)
            else:
                o.t += dt
                o.life -= dt
        self.orbs = [o for o in self.orbs if not o.dead]

        # particles + shockwave rings
        self._update_particles(dt)
        for r in self.rings:
            r[2] -= dt
        self.rings = [r for r in self.rings if r[2] > 0]

        # waves: keep spawning while at least one pilot is breathing
        if any(p.alive for p in self.players):
            self.waves.update(self, dt)

    # ---- revive channel ----
    def _update_revives(self, dt):
        for d in [pp for pp in self.players if pp.downed]:
            helper = self._nearest_alive(d.pos, cap=REVIVE_RADIUS ** 2)
            if helper is not None:
                d.revive_progress += dt
                if random.random() < 0.15:
                    self.add_particle(d.pos + V2(random.uniform(-14, 14), random.uniform(-14, 14)),
                                      (random.uniform(-30, 30), random.uniform(-70, -20)),
                                      0.4, (120, 255, 170), 2)
                if d.revive_progress >= REVIVE_CHANNEL:
                    d.revive()
                    self.explode(d.pos, (120, 255, 170), 14, 200)
                    self.sfx.play("wave", volume=0.5)
                    if self.session is not None:
                        self.session.event("revive", pid=d.pid, by=helper.pid)
            else:
                d.revive_progress = max(0.0, d.revive_progress - dt * 2.0)

    def _update_particles(self, dt):
        for pt in self.particles:
            pt.update(dt)
        self.particles = [pt for pt in self.particles if not pt.dead]

    def _resolve_bolt_collisions(self):
        dead_bolts = set()
        for b in self.bolts:
            if b.dead:
                continue
            if b.friendly:
                hit = False
                for e in self.enemies:
                    if e.dead:
                        continue
                    if (e.pos - b.pos).length() <= e.radius + b.radius:
                        owner = self._by_pid(b.owner_pid) or self.player
                        e.hp -= b.spec.damage
                        e.dmg_taken += b.spec.damage
                        e.hit_flash = 0.2
                        e.last_hit_by = b.owner_pid
                        owner.total_damage_dealt += b.spec.damage
                        owner.register_hit()
                        self.counters["phit"] += 1
                        self._spark(b.pos, e.color)
                        self.sfx.play("hit", volume=0.6)
                        if b.spec.aoe > 0:
                            self._aoe(b.pos, b.spec.aoe, b.spec.damage, e, owner)
                        hit = True
                        break
                if hit:
                    dead_bolts.add(id(b))
            else:
                victim = None
                for pp in self.players:
                    if pp.alive and (pp.pos - b.pos).length() <= pp.radius + b.radius:
                        victim = pp
                        break
                if victim is not None:
                    self.counters["ehit"] += 1
                    victim.take_damage(self, b.spec.damage)
                    self._spark(b.pos, b.spec.color)
                    dead_bolts.add(id(b))
                elif not getattr(b, "nm", False):
                    for pp in self.players:
                        if pp.alive and (pp.pos - b.pos).length() < 52.0:
                            b.nm = True
                            self.counters["near"] += 1
                            break
        if dead_bolts:
            self.bolts = [b for b in self.bolts if id(b) not in dead_bolts]

    def _spark(self, pos, color):
        for _ in range(5):
            ang = random.uniform(0, math.tau)
            sp = random.uniform(50, 180)
            self.add_particle(pos, (math.cos(ang) * sp, math.sin(ang) * sp),
                              0.25, color, 2)

    def _aoe(self, pos, radius, dmg, primary, owner=None):
        opid = owner.pid if owner is not None else 0
        for e in self.enemies:
            if e is primary or e.dead:
                continue
            d = (e.pos - pos).length()
            if d <= radius + e.radius:
                falloff = max(0.3, 1.0 - d / (radius + e.radius))
                splash = dmg * 0.6 * falloff
                e.hp -= splash
                e.dmg_taken += splash
                e.hit_flash = 0.2
                e.last_hit_by = opid
        self.rings.append([pygame.math.Vector2(pos), radius, 0.45, (255, 190, 130)])
        self.explode(pos, (255, 170, 110), 22, 240)
        self.shake_t = max(self.shake_t, 0.3)
        self.sfx.play("explosion", volume=0.8)

    # -------------------------------------------------------------- render
    def render(self):
        s = self.screen
        s.fill((10, 12, 24))
        s.blit(self.nebula, (0, 0))
        self.stars.draw(s, self.t)

        if self.state == "menu":
            draw_menu(s, self, self.t)
            return

        for o in self.orbs:
            o.draw(s)

        for b in self.bolts:
            c = b.spec.color
            x, y = int(b.pos.x), int(b.pos.y)
            pygame.draw.circle(s, tuple(v // 4 for v in c), (x, y), b.radius + 4)
            pygame.draw.circle(s, tuple(min(255, v + 60) for v in c), (x, y), b.radius)
            pygame.draw.circle(s, (255, 255, 255), (x, y), max(1, b.radius - 2))

        for e in self.enemies:
            e.draw(s)

        self._draw_aim_lock(s)

        for i, pp in enumerate(self.players):
            pp.draw(s, self.t, is_local=(i == 0))

        draw_player_extras(s, self)

        for pt in self.particles:
            pt.draw(s)

        for pos, radius, t_left, color in self.rings:
            f = min(1.0, t_left / 0.45)
            grow = 1.0 + (1.0 - f) * 0.9
            col = tuple(int(c * min(1.0, f + 0.2)) for c in color)
            pygame.draw.circle(s, col, (int(pos.x), int(pos.y)), int(radius * grow), 2)
            pygame.draw.circle(s, tuple(c // 2 for c in col), (int(pos.x), int(pos.y)), int(radius * grow * 0.7), 1)

        # screen shake: self-blit shift, then repaint exposed strips with fresh background
        if self.shake_t > 0:
            mag = int(14 * self.shake_t)
            ox = random.randint(-mag, mag)
            oy = random.randint(-mag, mag)
            if ox or oy:
                s.blit(s, (ox, oy))
                bands = []
                if oy > 0:
                    bands += [pygame.Rect(0, 0, W, oy), pygame.Rect(0, H - oy, W, oy)]
                elif oy < 0:
                    bands.append(pygame.Rect(0, H + oy, W, -oy))
                if ox > 0:
                    bands.append(pygame.Rect(0, 0, ox, H))
                elif ox < 0:
                    bands.append(pygame.Rect(W + ox, 0, -ox, H))
                for band in bands:
                    old_clip = s.get_clip()
                    s.set_clip(band)
                    s.fill((10, 12, 24))
                    s.blit(self.nebula, (0, 0))
                    self.stars.draw(s, self.t)
                    s.set_clip(old_clip)

        draw_hud(s, self)
        draw_damage_overlay(s, self)
        if self.state == "pause":
            draw_pause(s)
        elif self.state == "over":
            draw_gameover(s, self, self.t)

    def report(self):
        p = self.player
        wv = self.waves
        return {
            "score": self.score,
            "kills": self.kills,
            "n_players": len(self.players),
            "alive_players": sum(1 for pp in self.players if pp.alive),
            "wave": wv.wave,
            "spawned": wv.spawned_ever,
            "enemies_live": len(self.enemies),
            "bolts": len(self.bolts),
            "particles": len(self.particles),
            "shield": int(p.shield),
            "armor": int(p.armor),
            "hull": int(p.hull),
            "dmg_dealt": int(p.total_damage_dealt),
            "dmg_taken": int(p.total_damage_taken),
            "orbs": self.stats["orbs_collected"],
            "restarts": self.run_restarts,
        }

    def _draw_aim_lock(self, s):
        for p in self.players:
            self._draw_aim_lock_for(s, p)

    def _draw_aim_lock_for(self, s, p):
        if not p.alive:
            return
        tgt = p.target(self.enemies)
        if tgt is None:
            return
        ax, ay = int(p.pos.x), int(p.pos.y)
        bx, by = int(tgt.pos.x), int(tgt.pos.y)
        c = (95, 225, 255)
        line = [pygame.math.Vector2(ax, ay), pygame.math.Vector2(bx, by)]
        try:
            pygame.draw.line(s, c, line, 1)
        except Exception:
            pass
        r = tgt.radius + 10 + 2 * math.sin(self.t * 8)
        pts = []
        for i in range(4):
            ang = math.tau * i / 4 + self.t * 2.5
            pts.append((bx + int(math.cos(ang) * r), by + int(math.sin(ang) * r)))
        pygame.draw.polygon(s, c, pts, 2)
        pygame.draw.circle(s, c, (bx, by), int(r), 1)

    # -------------------------------------------------------------- input
    def handle_event(self, ev):
        if ev.type != pygame.KEYDOWN:
            return None
        # Cmd+Q (macOS) quits anywhere
        if ev.key == pygame.K_q and ev.mod & pygame.KMOD_META:
            return "quit"
        if ev.key == pygame.K_F11:  # optional fullscreen toggle
            pygame.display.toggle_fullscreen()
            return None
        k = ev.key
        if self.state == "menu":
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                self.start()
        elif self.state == "play":
            if k in (pygame.K_p, pygame.K_ESCAPE):
                self.state = "pause"
            elif k == pygame.K_1:
                self._select_weapon(0)
            elif k == pygame.K_2:
                self._select_weapon(1)
            elif k == pygame.K_3:
                self._select_weapon(2)
            elif k == pygame.K_r:
                self.start()
        elif self.state == "pause":
            if k in (pygame.K_p, pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
                self.state = "play"
        elif self.state == "over":
            if k in (pygame.K_r, pygame.K_RETURN, pygame.K_KP_ENTER):
                self.start()
            elif k == pygame.K_ESCAPE:
                self.to_menu()

    def _select_weapon(self, idx):
        if self.player.weapon_idx != idx:
            self.player.weapon_idx = idx
            if self.session is not None:
                self.session.weapon(idx)

    # -------------------------------------------------------------- main loops
    def run(self):
        running = True
        run_t = 0.0
        while running:
            dt = min(1.0 / 20.0, self.clock.tick(FPS) / 1000.0)
            run_t += dt
            if self._shot_path is not None and self._shot_t is not None and \
                    run_t >= self._shot_t:
                pygame.image.save(self.screen, self._shot_path)
                print("screenshot saved:", self._shot_path)
                self._shot_path = None
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                else:
                    if self.handle_event(ev) == "quit":
                        running = False
            if running:
                if self.net is not None:
                    self.net.tick(dt)        # joins, inputs, snapshots
                if self.state != "pause":
                    self.update(dt)
                self.render()
                pygame.display.flip()
        if self.net is not None:
            self.net.close()
        self._close_session()
        pygame.quit()

    def run_selftest(self, frames, screenshot=None):
        """Headless autopilot flight: verifies sim, combat, waves, economy.

        Metrics are aggregated across every run spawned by in-test restarts,
        so the verdict never depends on where the last restart fell.
        Returns (ok: bool, report: dict).
        """
        self.start()
        dt = 1.0 / 60.0
        t0 = time.perf_counter()
        last_report = {}
        probe_death_frame = None
        agg = {"spawned": 0, "wave": 0, "score": 0, "kills": 0,
               "dmg": 0.0, "dealt": 0.0, "orbs": 0}

        def collect():
            wv, p0 = self.waves, self.players[0]
            agg["spawned"] = max(agg["spawned"], wv.spawned_ever)
            agg["wave"] = max(agg["wave"], wv.wave)
            agg["score"] += self.score
            agg["kills"] += self.kills
            agg["dmg"] += p0.total_damage_taken
            agg["dealt"] += p0.total_damage_dealt
            agg["orbs"] += self.stats["orbs_collected"]
        try:
            for i in range(frames):
                if i % 150 == 0:
                    for pi, pp in enumerate(self.players):
                        if pp.bot == "weave" and pp.alive:
                            pp.weapon_idx = (i // 150 + pi) % len(WEAPONS)
                self.update(dt)
                if self.state != "play":
                    self.run_restarts += 1
                    if probe_death_frame is None:
                        probe_death_frame = i
                    collect()          # score the run that just died
                    self.start()
                if i % 600 == 0 and i > 0:
                    self.render()
                    last_report = self.report()
                    print("[selftest] " + _fmt_report(i, last_report))
                if i == frames - 1:
                    last_report = self.report()
                    self.render()
        finally:
            pygame.event.pump()
        collect()                       # and the final (partial) run
        self._close_session()
        elapsed = time.perf_counter() - t0
        if screenshot:
            pygame.image.save(self.screen, screenshot)
        real_fps = frames / max(1e-6, elapsed)

        p = self.player
        checks = {
            "no_crash": True,
            "enemies_spawned": agg["spawned"] > 0,
            "enemies_hit_player": agg["dmg"] > 0.0,
            "waves_progressed": agg["wave"] >= 1,
        }
        if self.bots == ["steady"]:
            # difficulty probe (solo steady): the stationary player must die
            # reasonably fast, but never *instantly* (broken damage = instant
            # death). Never applied to co-op crews — the weave partner tanks.
            if probe_death_frame is not None:
                t_dead = probe_death_frame / 60.0
                checks["probe_died_between_5s_and_window"] = 5.0 <= t_dead <= frames / 60.0
                print(f"[selftest]   steady-bot first death at t={t_dead:.1f}s "
                      f"(target: under ~45s means the config strains players)")
            else:
                checks["probe_died_between_5s_and_window"] = False
                print("[selftest]   steady bot SURVIVED the whole window — likely too easy")
        else:
            checks["player_killed_enemies"] = agg["dealt"] > 0.0
            checks["salvage_collected"] = agg["orbs"] > 0
        ok = all(checks.values())
        print(f"[selftest] frames={frames} wall={elapsed:.1f}s real_fps={real_fps:.0f}")
        for name, passed in checks.items():
            print(f"[selftest]   check {name}: {'PASS' if passed else 'FAIL'}")
        print("[selftest] " + _fmt_report(frames, {**last_report, "restarts": self.run_restarts}))
        print(f"[selftest] RESULT: {'PASS' if ok else 'FAIL'}")
        return ok, {**last_report, "restarts": self.run_restarts,
                    "checks": checks, "real_fps": real_fps}


def _make_orb(pos):
    from entities import Orb
    return Orb(pos, random.choice((15, 15, 20, 30)))


def _fmt_report(frame, r):
    return (f"frame={frame} wave={r.get('wave')} score={r.get('score')} "
            f"kills={r.get('kills')} spawned={r.get('spawned')} live={r.get('enemies_live')} "
            f"shield={r.get('shield')} armor={r.get('armor')} hull={r.get('hull')} "
            f"dmg_dealt={r.get('dmg_dealt')} dmg_taken={r.get('dmg_taken')} "
            f"orbs={r.get('orbs')} restarts={r.get('restarts')}")
