"""LAN host: authoritative sim wrapper around a running Game.

The host machine runs the real simulation (a plain Game instance). This
class adds a listen socket and client handling:
  - connect up to MAX_REMOTE remote pilots (pids 1..3; the host is pid 0)
  - read their inputs (staged, applied by the game loop on the main thread)
  - push a full-state snapshot (~30 Hz) to each client

Threading: only connecting/reading happens in background threads. All
game-state reads/writes (snapshot build, send, join/drop application) happen
on the game's main thread inside ``tick(dt)`` called from the run loop.
"""
import socket
import threading
import time

from net.protocol import encode, LineBuffer, MAX_REMOTE, SNAP_HZ


class _Client:
    __slots__ = ("sock", "addr", "name", "pid", "stream", "mv", "wp", "slock")

    def __init__(self, sock, addr, name, pid):
        self.sock = sock
        self.addr = addr
        self.name = name
        self.pid = pid
        self.stream = LineBuffer()
        self.mv = [0.0, 0.0]        # latest movement vector (unnormalized)
        self.wp = -1                # pending weapon choice (-1 = none)
        self.slock = threading.Lock()  # one writer per connection


class Host:
    def __init__(self, game, port=0, name="You"):
        self.game = game
        self.name = name
        self.clients = {}           # pid -> _Client (remote only)
        self.events = []            # ("join", pid, name) / ("drop", pid)
        self.seq = 0
        self._snap_every = 1.0 / SNAP_HZ
        self._next_snap_t = 0.0
        self._lock = threading.Lock()
        # net diagnostics: 5 s heartbeat (rate, costs, snapshot gap)
        self._st_t = 0.0
        self._st_snaps = 0
        self._st_bytes = 0
        self._st_tick_ms = 0.0
        self._st_bcast_ms = 0.0
        self._st_gap_ms = 0.0
        self._last_snap_wall = None

        self.lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.lsock.bind(("0.0.0.0", port))
        self.lsock.listen(8)
        self.port = self.lsock.getsockname()[1]
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True,
                         name="net-host").start()
        game.net = self

    # ---------------- accept / read (background threads) ----------------
    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.lsock.accept()
            except OSError:
                break
            threading.Thread(target=self._serve_conn, args=(conn, addr),
                             daemon=True).start()

    def _free_pid(self):
        with self._lock:
            used = {c.pid for c in self.clients.values()}
            for pid in range(1, MAX_REMOTE + 1):
                if pid not in used:
                    return pid
        return None

    def _serve_conn(self, conn, addr):
        import socket as _sock
        buf = LineBuffer()
        pid = None
        try:
            conn.settimeout(20.0)     # idle handshakes time out
            while self.running:
                try:
                    data = conn.recv(4096)
                except _sock.timeout:
                    if pid is None:
                        break         # never finished the handshake -> drop
                    continue          # idle lull on a live link: keep waiting
                if not data:
                    break
                for msg in buf.feed(data):
                    k = msg.get("k")
                    if k == "join" and pid is None:
                        name = str(msg.get("name", "Pilot"))[:16] or "Pilot"
                        pid = self._free_pid()
                        if pid is None:
                            conn.sendall(encode({"k": "bye",
                                                 "reason": "room is full"}))
                            return
                        self._register(conn, addr, name, pid)
                        # tell this client who is in the room
                        roster = [self.game.players[0].name
                                  if self.game.players else self.name]
                        for p, c in sorted(self.clients.items()):
                            if p != pid:
                                roster.append(c.name)
                        # index by host pid order: this client is pid-th entry
                        insert_at = min(pid, len(roster))
                        roster.insert(insert_at, name)
                        c = self.clients.get(pid)
                        if c is not None:
                            with c.slock:
                                conn.sendall(encode(
                                    {"k": "roster", "pid": pid,
                                     "names": roster}))
                        # main thread seats the player (and refreshes the
                        # other clients' standby screens)
                        self.events.append(("join", pid, name))
                    elif k == "in" and pid is not None:
                        c = self.clients.get(pid)
                        if c is None:
                            continue
                        mv = msg.get("mv") or [0.0, 0.0]
                        try:
                            c.mv = [float(mv[0]), float(mv[1])]
                        except (TypeError, ValueError, IndexError):
                            c.mv = [0.0, 0.0]
                        wp = msg.get("wp", -1)
                        c.wp = int(wp) if isinstance(wp, int) else -1
                    elif k == "bye" and pid is not None:
                        break
        except OSError:
            pass
        finally:
            if pid is not None:
                self._unregister(pid, from_reader=True)
            try:
                conn.close()
            except OSError:
                pass

    def _register(self, conn, addr, name, pid):
        with self._lock:
            c = _Client(conn, addr, name, pid)
            self.clients[pid] = c
        return c

    # seat order: host seat first, then remote pids ascending
    def roster_names(self):
        names = [self.game.players[0].name
                 if self.game.players else self.name]
        for p, c in sorted(self.clients.items()):
            names.append(c.name)
        return names

    def _send_roster(self, c):
        try:
            with c.slock:
                c.sock.sendall(encode(
                    {"k": "roster", "pid": c.pid, "names": self.roster_names()}))
        except OSError:
            pass

    def _unregister(self, pid, from_reader=False):
        with self._lock:
            if pid in self.clients:
                del self.clients[pid]
            if from_reader:
                self.events.append(("drop", pid))

    def _drop(self, pid):
        with self._lock:
            c = self.clients.pop(pid, None)
            if c is not None:
                self.events.append(("drop", pid))
                try:
                    c.sock.close()
                except OSError:
                    pass

    # ---------------- main-thread work (called from the run loop) ----
    def tick(self, dt):
        """Apply join/drop events + inputs and push snapshots."""
        t0 = time.perf_counter()
        # join/drop -> the game's player list
        roster_changed = False
        while self.events:
            ev = self.events.pop(0)
            if ev[0] == "join":
                self.game.net_player_joined(ev[1], ev[2])
            else:
                self.game.net_player_left(ev[1])
            roster_changed = True
        if roster_changed:
            for c in list(self.clients.values()):
                self._send_roster(c)
        # inputs -> game.net_inputs {pid: ((x, y), wp|-1)}
        inputs = {}
        for pid, c in self.clients.items():
            inputs[pid] = (tuple(c.mv), c.wp)
            c.wp = -1          # one-shot weapon change
        self.game.net_inputs = inputs
        # snapshots
        self._next_snap_t -= dt
        if self.clients and self._next_snap_t <= 0.0:
            self._next_snap_t += self._snap_every
            nbytes, bms = self._broadcast()
            now = time.monotonic()
            if self._last_snap_wall is not None:
                self._st_gap_ms = (now - self._last_snap_wall) * 1000.0
            self._last_snap_wall = now
            self._st_snaps += 1
            self._st_bytes += nbytes
            self._st_bcast_ms = bms
        self._st_tick_ms += (time.perf_counter() - t0) * 1000.0
        self._st_t += dt
        if self._st_t >= 5.0:
            print("[net:host] clients=%d snaps=%.1f/s snap_bytes=%d "
                  "bcast=%.1fms gap=%.1fms tick=%.1fms"
                  % (len(self.clients), self._st_snaps / self._st_t,
                     (self._st_bytes // max(1, self._st_snaps)),
                     self._st_bcast_ms, self._st_gap_ms,
                     self._st_tick_ms / self._st_t), flush=True)
            self._st_t = 0.0
            self._st_snaps = 0
            self._st_bytes = 0
            self._st_tick_ms = 0.0
            self._st_bcast_ms = 0.0
            self._st_gap_ms = 0.0

    def _broadcast(self):
        from net.protocol import build_snapshot
        t0 = time.perf_counter()
        line = encode(build_snapshot(self.game, self.seq))
        self.seq += 1
        for c in list(self.clients.values()):
            try:
                with c.slock:
                    c.sock.settimeout(0.25)
                    c.sock.sendall(line)
            except OSError:
                # stuck or gone: stop paying for this seat
                self._drop(c.pid)
        return len(line), (time.perf_counter() - t0) * 1000.0

    def close(self):
        self.running = False
        bye = encode({"k": "bye", "reason": "host closed"})
        with self._lock:
            for c in list(self.clients.values()):
                try:
                    c.sock.settimeout(0.2)
                    c.sock.sendall(bye)
                except OSError:
                    pass
                try:
                    c.sock.close()
                except OSError:
                    pass
            self.clients.clear()
        self.game.net_inputs = {}
        self.game.net = None
        try:
            self.lsock.close()
        except OSError:
            pass