"""Global tunables for Astral Vector."""
import os

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

W, H = 1280, 720
FPS = 60

# ---- Player ship ----
PLAYER_RADIUS = 14
PLAYER_ACCEL = 950.0        # px/s^2
PLAYER_DRAG = 2.6           # exponential drag coefficient
PLAYER_MAX_SPEED = 400.0
AIM_RANGE = 580.0           # auto-lock acquisition range

# ---- Defense layers (Everspace-style: shield -> armor -> hull) ----
SHIELD_MAX = 100
SHIELD_REGEN = 18.0              # per second
SHIELD_REGEN_DELAY = 4.0         # seconds after last hit before regen
ARMOR_MAX = 100
HULL_MAX = 100

CONTACT_DMG = 12                # ramming enemies hurts
CONTACT_CD = 0.6                # cooldown between contact hits

# ---- Co-op / revive ----
REVIVE_RADIUS = 70.0           # stay this close to a downed ship to channel
REVIVE_CHANNEL = 3.0           # seconds of continuous presence needed
REVIVE_HULL_FRAC = 0.5         # revived with this fraction of max hull, 0 shield/armor
WAVE_BUMP_PER_PLAYER = 0.5     # extra "synthetic waves" of difficulty per teammate

# Difficulty levels: multipliers over the standard baseline, calibrated from
# collected playtest sessions (steady no-aim bot dies ~10s; active solo
# humans reach wave 1-10 over 25-350s; duos reach wave 5 in ~120s).
DIFFICULTIES = {
    "easy":     dict(hp=0.80, dmg=0.70, speed=0.90, count=0.75),
    "standard": dict(hp=1.00, dmg=1.00, speed=1.00, count=1.00),
    "hard":     dict(hp=1.30, dmg=1.40, speed=1.08, count=1.25),
}
DIFF_ORDER = ["easy", "standard", "hard"]

# Difficulty levels: multipliers on the playtest-verified standard baseline
# (no-aim pilot dies ~10-16 s; active solo runs 25-350 s; duos reach wave 5).
#   hp    : enemy HP (dart included)
#   dmg   : attack damage and dart ram
#   speed : enemy movement speed
#   count : enemies per wave
DIFFICULTIES = {
    "easy":     dict(hp=0.75, dmg=0.70, speed=0.85, count=0.75),
    "standard": dict(hp=1.00, dmg=1.00, speed=1.00, count=1.00),
    "hard":     dict(hp=1.30, dmg=1.35, speed=1.10, count=1.25),
}
DIFF_ORDER = ["easy", "standard", "hard"]

# ---- Misc ----
BOLT_MARGIN = 90                # cull bullets beyond this offscreen margin
PARTICLE_CAP = 900
SPAWN_MARGIN = 50               # enemies spawn offscreen this far

# ---- Session telemetry ----
SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
SAMPLE_HZ = 5                 # state samples per second in session logs
