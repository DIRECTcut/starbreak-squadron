"""LAN protocol loopback tests.

Host sim + real sockets, everything in one process: verifies join handshake,
roster delivery, input staging into the sim, 30 Hz snapshots, and drop
handling (the leaving pilot's ship goes down, the pid is reusable).
"""
import os
import socket
import sys
import time
import unittest
from unittest.mock import patch

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

import config  # noqa: E402
from net.client import ClientGame, Remote  # noqa: E402
from net.host import Host  # noqa: E402
from net.protocol import LineBuffer, encode, build_snapshot  # noqa: E402
from tests.test_game import make_game, DT  # noqa: E402


class _Reader:
    """Line reader over a connected socket, waits for a matching message."""

    def __init__(self, sock, timeout=5.0):
        self.sock = sock
        self.buf = LineBuffer()
        self.sock.settimeout(timeout)

    def wait_for(self, pred, timeout=5.0, tick=None, dt=1.0 / 60.0):
        """Wait for a matching message, optionally driving `tick(dt)` along."""
        self.sock.settimeout(0.05)
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            if tick is not None:
                tick(dt)
            try:
                data = self.sock.recv(65536)
            except OSError:  # timeout between messages
                data = b""
            for msg in self.buf.feed(data):
                if pred(msg):
                    return msg
        raise AssertionError("timed out waiting for message")


class LineBufferTest(unittest.TestCase):
    def test_split_across_feeds(self):
        b = LineBuffer()
        self.assertEqual(b.feed(b'{"k":"joi'), [])
        got = b.feed(b'n","name":"x"}\n{"k":"in","mv":[1,0]}\n')
        self.assertEqual(got, [{"k": "join", "name": "x"},
                               {"k": "in", "mv": [1, 0]}])
        self.assertEqual(b.feed(b"partial"), [])  # stays buffered

    def test_corrupt_line_dropped(self):
        b = LineBuffer()
        out = b.feed(b"nonsense\n{\"k\":\"ok\"}\n")
        self.assertEqual(out, [{"k": "ok"}])


class SnapshotTest(unittest.TestCase):
    def test_snapshot_shape(self):
        g = make_game(seed=9, sessions=None, bot="")
        g.start()
        snap = build_snapshot(g, 3)
        self.assertEqual(snap["k"], "snap")
        self.assertEqual(snap["seq"], 3)
        self.assertEqual(snap["rs"], 1)
        self.assertEqual(snap["st"], "play")
        self.assertEqual(len(snap["pl"]), 1)
        row = snap["pl"][0]
        # pid, x,y,vx,vy, shield,armor,hull, alive,downed, revive, wp, heading,
        # name, score, kills, fired, hit, hit_flash, shield_timer
        self.assertEqual(len(row), 20)
        self.assertEqual(row[0], 0)    # host seat pid
        self.assertEqual(row[8], 1)    # alive
        self.assertEqual(row[13], "You")


class ClientStartupTest(unittest.TestCase):
    def _client(self):
        # Keep the real Game/ClientGame constructors and Remote message queue;
        # only isolate socket I/O and the asynchronous reader for deterministic
        # delivery (the host need not send a snapshot before the first frame).
        with patch("net.client.socket_connect"), patch.object(Remote, "start"):
            client = ClientGame("127.0.0.1", 7747, "Loop", mute=True)
        self.addCleanup(client.remote.close)
        return client

    def _deliver(self, client, msg):
        for decoded in LineBuffer().feed(encode(msg)):
            client.remote._handle(decoded)

    def test_frames_before_first_snapshot_then_standby(self):
        client = self._client()
        for _ in range(3):
            client.update(DT)  # regression: inherited solo Player has no _net_t
            client.render()
            client._draw_lan_overlay()
        self.assertEqual(client.players, [])
        self.assertIsNone(client._local())
        self.assertEqual(client.compute_input(), (0.0, 0.0))

        self._deliver(client, {"k": "roster", "pid": 1,
                               "names": ["You", "Loop"]})
        for _ in range(3):
            client.update(DT)  # roster alone still supplies no player state
        self.assertEqual(client.players, [])

        host_game = make_game(seed=19, sessions=None, bot="")
        host_game.net_player_joined(1, "Loop")
        self._deliver(client, build_snapshot(host_game, 0))
        client.update(DT)
        client.render()
        client._draw_lan_overlay()
        self.assertEqual(client.state, "menu")
        self.assertEqual([p.pid for p in client.players], [1, 0])
        self.assertIs(client.player, client._local())
        self.assertEqual(client.player.name, "Loop")
        self.assertTrue(all(hasattr(p, "_net_t") for p in client.players))

    def test_human_weapon_keys_reach_remote_in_run_loop(self):
        client = self._client()  # default bot="", just like main.py human mode
        host_game = make_game(seed=21, sessions=None, bot="")
        host_game.start()
        host_game.net_player_joined(1, "Loop")
        self._deliver(client, {"k": "roster", "pid": 1,
                               "names": ["You", "Loop"]})
        self._deliver(client, build_snapshot(host_game, 0))
        client.update(DT)
        client.remote.sock.sendall.reset_mock()  # exclude the join handshake
        events = [[pygame.event.Event(pygame.KEYDOWN, key=key, mod=0)]
                  for key in (pygame.K_2, pygame.K_3, pygame.K_1)]
        events += [[], [pygame.event.Event(pygame.QUIT)]]
        with patch("pygame.event.get", side_effect=events), \
                patch("pygame.quit"), \
                patch.object(client, "render", wraps=client.render) as render:
            client.run()
        sent = []
        wire = LineBuffer()
        for call in client.remote.sock.sendall.call_args_list:
            sent.extend(wire.feed(call.args[0]))
        inputs = [msg for msg in sent if msg["k"] == "in"]
        self.assertEqual([msg["wp"] for msg in inputs], [1, 2, 0, -1])
        self.assertEqual(render.call_count, 4)
        # Weapon state remains host-owned, not optimistically changed by keys.
        self.assertEqual(client.player.weapon_idx, 0)

    def test_snapshots_predict_all_pilots_and_refresh_existing_players(self):
        client = self._client()
        host_game = make_game(seed=20, sessions=None, bot="")
        host_game.start()
        host_game.net_player_joined(1, "Loop")
        host_game.t = 12.0
        host_game.players[0].vel.update(120, -60)
        host_game.players[1].vel.update(-90, 30)
        self._deliver(client, {"k": "roster", "pid": 1,
                               "names": ["You", "Loop"]})
        self._deliver(client, build_snapshot(host_game, 0))
        client.update(DT)
        players = dict(client._players)
        for source in host_game.players:
            actual = players[source.pid]
            self.assertEqual(actual._net_t, 12.0)
            self.assertLess(actual.pos.distance_to(source.pos + source.vel * DT),
                            1e-6)

        for _ in range(20):
            client.update(DT)
        for source in host_game.players:
            self.assertLess(players[source.pid].pos.distance_to(
                source.pos + source.vel * 0.10), 1e-6)

        host_game.t = 13.0
        for source in host_game.players:
            source.pos += pygame.math.Vector2(50, -20)
            source.vel *= -1
        self._deliver(client, build_snapshot(host_game, 1))
        client.update(DT)
        for source in host_game.players:
            actual = client._players[source.pid]
            self.assertIs(actual, players[source.pid])
            self.assertEqual(actual._net_t, 13.0)
            self.assertLess(actual.pos.distance_to(source.pos + source.vel * DT),
                            1e-6)
        client.render()


class HostLoopbackTest(unittest.TestCase):
    def _connect(self, host):
        return socket.create_connection(("127.0.0.1", host.port), timeout=5)

    def _seat_join(self, host):
        # Roster delivery precedes queuing the join event in the reader thread.
        end = time.monotonic() + 2.0
        while not any(ev[0] == "join" for ev in host.events):
            self.assertLess(time.monotonic(), end, "join event not queued")
            time.sleep(0.002)
        host.tick(0.01)

    def test_join_roster_input_snapshot_drop(self):
        g = make_game(seed=11, sessions=None, bot="")  # no bot -> "You" seat
        h = Host(g, port=0)
        try:
            s = self._connect(h)
            r = _Reader(s)
            s.sendall(encode({"k": "join", "name": "Loop"}))

            roster = r.wait_for(lambda m: m.get("k") == "roster")
            self.assertEqual(roster["pid"], 1)
            self.assertEqual(roster["names"], ["You", "Loop"])

            self._seat_join(h)  # main thread seats the join
            self.assertEqual(len(g.players), 2)
            self.assertEqual(g.players[1].name, "Loop")
            self.assertEqual(g.players[1].pid, 1)

            s.sendall(encode({"k": "in", "mv": [1, 1], "wp": 1, "t": 1}))
            mv, wp, t_op = (0.0, 0.0), -1, 0
            end = time.perf_counter() + 2.0
            while time.perf_counter() < end:
                time.sleep(0.002)      # give the reader thread the CPU
                h.tick(0.01)
                mv, wp, t_op = g.net_inputs.get(1, ((0.0, 0.0), -1, 0))
                if mv == (1.0, 1.0) and wp == 1:
                    break
            self.assertEqual(mv, (1.0, 1.0))
            self.assertEqual(wp, 1)
            self.assertEqual(t_op, 1)  # target-cycle op staged

            g.start()  # reset() clears staged inputs; the client re-sends at 60 Hz
            s.sendall(encode({"k": "in", "mv": [1, 1], "wp": 1, "t": 1}))
            mv, wp, t_op = (0.0, 0.0), -1, 0
            end = time.perf_counter() + 2.0
            while time.perf_counter() < end:
                time.sleep(0.002)
                h.tick(0.01)
                mv, wp, t_op = g.net_inputs.get(1, ((0.0, 0.0), -1, 0))
                # Movement persists across start(); wait for the new switch
                # too, rather than mistaking the previous vector for receipt.
                if mv == (1.0, 1.0) and wp == 1:
                    break
            self.assertEqual(mv, (1.0, 1.0))
            self.assertEqual(wp, 1)
            g.update(DT)  # input applied: weapon switch + movement + target lock
            self.assertEqual(g.players[1].weapon_idx, 1)
            self.assertGreater(g.players[1].vel.length(), 0.0)
            self.assertEqual(g.players[1].target_rank, 0)  # auto: first press pins rank 0

            snap = r.wait_for(lambda m: m.get("k") == "snap" and m["st"] == "play",
                              tick=h.tick)
            self.assertEqual(snap["st"], "play")
            self.assertEqual(len(snap["pl"]), 2)
            self.assertEqual(snap["rs"], 1)

            # the pending weapon switch + target op are one-shot
            h.tick(0.01)
            self.assertEqual(g.net_inputs[1][1], -1)
            self.assertEqual(g.net_inputs[1][2], 0)

            # disconnect -> ship goes down, pid is reusable
            s.close()
            for _ in range(50):
                time.sleep(0.005)  # let the reader thread see the FIN
                h.tick(0.01)
                if not g.players[1].alive:
                    break
            self.assertFalse(g.players[1].alive)
            self.assertFalse(list(h.clients.values()))

            s2 = self._connect(h)
            r2 = _Reader(s2)
            s2.sendall(encode({"k": "join", "name": "Again"}))
            roster2 = r2.wait_for(lambda m: m.get("k") == "roster")
            self.assertEqual(roster2["pid"], 1)
            self.assertEqual(roster2["names"], ["You", "Again"])
            self._seat_join(h)
            s2.close()
        finally:
            h.close()

    def test_weapon_choice_survives_later_inputs_until_consumed_and_rendered(self):
        g = make_game(seed=22, sessions=None, bot="")
        h = Host(g, port=0)
        self.addCleanup(h.close)
        g.start()
        client = ClientGame("127.0.0.1", h.port, "Loop", mute=True)
        self.addCleanup(client.remote.close)

        def wait_until(pred, tick=None):
            end = time.monotonic() + 2.0
            while time.monotonic() < end:
                if tick is not None:
                    tick()
                if pred():
                    return
                time.sleep(0.002)
            self.fail("timed out waiting for LAN input/snapshot")

        def receive():
            h.tick(DT)
            client.update(DT)

        # Finish the handshake before driving the sim, so the first snapshot
        # includes this pilot rather than racing the host's join event.
        wait_until(lambda: client.remote.roster is not None and bool(h.events))
        wait_until(lambda: client._local() is not None, tick=receive)
        pid = client.remote_pid
        # No host tick during each burst: TCP may deliver multiple 60 Hz
        # inputs before the sim consumes them. None encodes the wp=-1 no-op.
        for seq, (choices, expected) in enumerate([
                ((1, None, None), 1),
                ((2, 0, None), 0),  # latest choice wins, including slot zero
                ((2, None, None), 2)], start=1):
            with self.subTest(choices=choices):
                for i, wp in enumerate(choices):
                    client.remote.send_input((seq, i), wp)
                # Seeing the final movement proves the preceding lines were
                # read, including the no-op after the first switch.
                wait_until(lambda: h.clients[pid].mv == [seq, 2])
                h.tick(DT)
                self.assertEqual(g.net_inputs[pid][1], expected)
                g.update(DT)
                self.assertEqual(g._by_pid(pid).weapon_idx, expected)
                self.assertEqual(g.players[0].weapon_idx, 0)
                h.tick(0.05)  # post-simulation snapshot + consume only once
                self.assertEqual(g.net_inputs[pid][1], -1)
                wait_until(lambda: client.player.weapon_idx == expected,
                           tick=lambda: client.update(DT))
                self.assertIs(client.player, client._local())
                client.render()
                for slot in range(3):
                    x = config.W - 26 - 176 * (3 - slot)
                    y = config.H - 50 - (2 - slot) * 30
                    bg = (40, 48, 70) if slot == expected else (22, 26, 40)
                    self.assertEqual(tuple(client.screen.get_at((x + 3, y + 3)))[:3],
                                     bg)

    def test_room_full_rejects_a_fourth(self):
        g = make_game(seed=12, sessions=None, bot="")
        h = Host(g, port=0)
        socks = []
        try:
            for i in range(3):
                s = self._connect(h)
                s.sendall(encode({"k": "join", "name": "P%d" % i}))
                _Reader(s).wait_for(lambda m: m.get("k") == "roster")
                self._seat_join(h)
                socks.append(s)
            self.assertEqual(len(g.players), 4)
            s4 = self._connect(h)
            s4.sendall(encode({"k": "join", "name": "Nope"}))
            msg = _Reader(s4).wait_for(lambda m: m.get("k") == "bye")
            self.assertEqual(msg["reason"], "room is full")
            s4.close()
        finally:
            for s in socks:
                try:
                    s.close()
                except OSError:
                    pass
            h.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)