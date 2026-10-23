"""Tiny procedural sound effects (no asset files needed).

All sounds are generated with numpy and wrapped in guards so the game
runs even when the audio device is unavailable (e.g. CI / headless).
"""
import math

import pygame

RATE = 22050


def _buffer(samples):
    return pygame.sndarray.make_sound(samples)


class Sfx:
    def __init__(self, mute=False):
        self.mute = mute
        self.banked = {}
        self.ok = False

    def init(self):
        if self.mute or not pygame.get_init():
            return
        try:
            cur = pygame.mixer.get_init()
        except Exception:
            cur = None
        if cur is None:
            try:
                pygame.mixer.init(RATE, -16, 1, 512)
            except Exception:
                return
        elif cur[2] != 1:
            # mixer came up stereo (e.g. default init) — we generate mono buffers
            pygame.mixer.quit()
            try:
                pygame.mixer.init(RATE, -16, 1, 512)
            except Exception:
                return
        try:
            import numpy as np
        except ImportError:
            return
        try:
            self.banked = self._make(np)
            self.ok = True
        except Exception as exc:  # stay loud enough to be diagnosed, soft enough to not crash
            import sys
            print(f"[audio] SFX bank build failed, continuing silent: {exc!r}",
                  file=sys.stderr)
            self.banked = {}
            self.ok = False

    @staticmethod
    def _make(np):
        s = {}

        def tone(f0, f1, dur, vol=0.22, decay=7.0):
            n = int(RATE * dur)
            t = np.linspace(0, dur, n)
            freq = np.linspace(f0, f1, n)
            phase = 2 * math.pi * np.cumsum(freq) / RATE
            wave = np.sign(np.sin(phase)) * 0.6 + np.sin(phase) * 0.4
            amp = vol * np.exp(-decay * t)
            return (wave * amp * 32767).astype(np.int16)

        def noise(dur, vol=0.25, decay=9.0):
            n = int(RATE * dur)
            t = np.linspace(0, dur, n)
            raw = np.random.uniform(-1, 1, n)
            # rough low-pass (np.roll keeps the length constant)
            for _ in range(3):
                raw = (raw + np.roll(raw, 1)) / 2
            env = vol * np.exp(-decay * t)
            return (raw * env * 32767).astype(np.int16)

        s["fire"] = _buffer(tone(880, 480, 0.07, vol=0.35, decay=26))
        s["scatter"] = _buffer(noise(0.16, vol=0.55, decay=22))
        s["missile"] = _buffer(tone(190, 640, 0.28, vol=0.35, decay=6))
        s["eshot"] = _buffer(tone(520, 240, 0.06, vol=0.20, decay=24))
        s["hit"] = _buffer(noise(0.05, vol=0.40, decay=40))
        s["explosion"] = _buffer(noise(0.55, vol=0.65, decay=7))
        s["shield_hit"] = _buffer(tone(700, 300, 0.12, vol=0.32, decay=16))
        s["damage"] = _buffer(tone(320, 90, 0.3, vol=0.5, decay=9))
        s["pickup"] = _buffer(tone(900, 1500, 0.06, vol=0.22, decay=20))
        s["wave"] = _buffer(tone(440, 880, 0.35, vol=0.28, decay=4))
        s["death"] = _buffer(noise(1.1, vol=0.7, decay=4))
        for k, snd in s.items():
            snd.set_volume(min(1.0, snd.get_volume()))
        return s

    def play(self, name, volume=1.0):
        if not self.ok or self.mute:
            return
        snd = self.banked.get(name)
        if snd is not None:
            try:
                snd.set_volume(min(1.0, volume))
                snd.play()
            except Exception:
                pass
