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

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

import config  # noqa: E402
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


class HostLoopbackTest(unittest.TestCase):
    def _connect(self, host):
        return socket.create_connection(("127.0.0.1", host.port), timeout=5)

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

            h.tick(0.01)  # main thread seats the join
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
                if mv == (1.0, 1.0):
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
                if mv == (1.0, 1.0):
                    break
            self.assertEqual(mv, (1.0, 1.0))
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
            h.tick(0.01)
            s2.close()
        finally:
            h.close()

    def test_room_full_rejects_a_fourth(self):
        g = make_game(seed=12, sessions=None, bot="")
        h = Host(g, port=0)
        socks = []
        try:
            for i in range(3):
                s = self._connect(h)
                s.sendall(encode({"k": "join", "name": "P%d" % i}))
                _Reader(s).wait_for(lambda m: m.get("k") == "roster")
                h.tick(0.01)
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