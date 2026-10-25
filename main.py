#!/usr/bin/env python3
"""Astral Vector — entry point.

Play (solo or co-op bots):
    python main.py

LAN multiplayer (host runs the sim; co-pilots connect from the same network):
    python main.py --host                 # on the host machine
    python main.py --client 192.168.1.20  # on each co-pilot machine

Headless self-test (autopilot bot flies, fires and fights; no window needed):
    python main.py --selftest 1800 --seed 1 --screenshot shot.png
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def default_name():
    """Pilot name: short hostname so co-pilots can tell ships apart."""
    import socket
    return socket.gethostname().split(".")[0][:14] or "Pilot"


def local_ip():
    """Best-guess LAN IP for the printed connection hint."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description="Astral Vector")
    ap.add_argument("--selftest", type=int, default=0, metavar="FRAMES",
                    help="run headless autopilot simulation for FRAMES frames and exit")
    ap.add_argument("--seed", type=int, default=None, help="RNG seed")
    ap.add_argument("--screenshot", default=None, metavar="PNG",
                    help="save a screenshot (used with --selftest)")
    ap.add_argument("--bot", default="",
                    help="autopilot pilot(s), comma-joined: weave=evading, "
                         "steady=stands still (difficulty probe). "
                         'e.g. "weave,steady" flies two bot players. In LAN '
                         "mode a --bot ships one pilot automatically")
    ap.add_argument("--mute", action="store_true", help="disable sound")
    ap.add_argument("--difficulty", default="standard",
                    choices=["easy", "standard", "hard"],
                    help="enemy scaling (menu keys 1/2/3 also work)")
    ap.add_argument("--no-log", action="store_true",
                    help="do not write a session log")
    ap.add_argument("--host", action="store_true",
                    help="run as the LAN host (runs the authoritative sim)")
    ap.add_argument("--client", metavar="IP",
                    help="connect to a LAN host running --host")
    ap.add_argument("--port", type=int, default=7747,
                    help="LAN game port (default 7747)")
    ap.add_argument("--name", default="",
                    help="your pilot name in LAN mode (default: hostname)")
    ap.add_argument("--auto-start", action="store_true",
                    help="host flag: launch the run immediately instead of "
                         "waiting for ENTER (used by the scripted playtest)")
    ap.add_argument("--auto-quit", type=int, default=0, metavar="SECONDS",
                    help="client flag: close the client window after SECONDS")
    args = ap.parse_args()

    bot = args.bot or ("weave" if args.selftest else "")

    if args.selftest:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

    name = args.name or default_name()
    shot_t = 7.0  # both windows auto-save a screenshot ~7s into the run
    import pygame  # single function-level import (host + client branches use it)

    if args.client:
        from net.client import ClientGame
        g = ClientGame(addr=args.client, port=args.port, name=name,
                       mute=args.mute, bot=bot)
        g._shot_path = args.screenshot
        g._shot_t = shot_t if args.screenshot else None
        g._auto_quit = float(args.auto_quit or 0)
        try:
            g.run()
        finally:
            pygame.quit()
        return

    from game import Game
    if args.host:
        from net.host import Host
        g = Game(seed=args.seed, autopilot=bool(bot), mute=args.mute,
                 bot=bot, log_sessions=not args.no_log)
        g.select_difficulty(args.difficulty)
        g.local_name = name               # seat-0 ship takes the --name flag
        if g.players:
            g.players[0].name = name
        g._shot_path = args.screenshot
        g._shot_t = shot_t if args.screenshot else None
        h = Host(g, port=args.port, name=name)
        if args.auto_start:
            g.start()
        print("Astral Vector LAN host:  port %d  (local IP %s)"
              % (h.port, local_ip()))
        print("co-pilots connect with:  python main.py --client %s --port %d"
              % (local_ip(), h.port))
        print("they will see a STANDBY screen; press ENTER to launch the run")
        try:
            g.run()
        finally:
            pygame.quit()
        return

    g = Game(seed=args.seed, autopilot=bool(args.selftest), mute=args.mute,
             bot=bot, log_sessions=not args.no_log)
    g.select_difficulty(args.difficulty)
    if args.selftest:
        ok, _ = g.run_selftest(args.selftest, screenshot=args.screenshot)
        pygame_quit()
        sys.exit(0 if ok else 1)
    try:
        g.run()
    finally:
        pygame_quit()


def pygame_quit():
    try:
        import pygame
        pygame.quit()
    except Exception:
        pass


if __name__ == "__main__":
    main()