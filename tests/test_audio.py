"""SFX regression tests — audio must not fail silently."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import pygame  # noqa: E402
from audio import Sfx  # noqa: E402

EXPECTED = [
    "fire", "scatter", "missile", "eshot", "hit", "explosion",
    "shield_hit", "damage", "pickup", "wave", "death",
]


def _mixer_available():
    if not pygame.get_init():
        pygame.init()
    if not pygame.mixer.get_init():
        try:
            pygame.mixer.init(22050, -16, 1, 512)
        except Exception:
            return False
    return pygame.mixer.get_init() is not None


class SfxTest(unittest.TestCase):
    def test_bank_builds_when_mixer_available(self):
        if not _mixer_available():
            self.skipTest("no audio device available")
        s = Sfx(mute=False)
        s.init()
        self.assertTrue(s.ok, "SFX bank silently failed to build")
        self.assertCountEqual(sorted(s.banked), sorted(EXPECTED))

    def test_play_no_audio_device_is_safe(self):
        """Missing/muted audio must never raise (CI / headless hosts)."""
        s = Sfx(mute=True)
        s.init()
        for name in EXPECTED + ["no_such_sound"]:
            s.play(name)  # must not raise
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
