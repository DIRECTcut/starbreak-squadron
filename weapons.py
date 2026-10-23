"""Weapon definitions."""
from dataclasses import dataclass


@dataclass(frozen=True)
class WeaponSpec:
    slot: str
    name: str
    fire_interval: float      # seconds between shots
    damage: float             # per pellet/bolt
    bolt_speed: float
    bolt_radius: int
    pellets: int = 1
    spread_deg: float = 0.0
    homing: bool = False
    turn_rate: float = 0.0    # rad/s (homing only)
    aoe: float = 0.0          # splash radius at impact (0 = none)
    color: tuple = (255, 255, 255)
    sound: str = "fire"


WEAPONS = [
    WeaponSpec(
        slot="1", name="PULSE-7",
        fire_interval=0.11, damage=7.0, bolt_speed=760, bolt_radius=4,
        color=(95, 225, 255), sound="fire",
    ),
    WeaponSpec(
        slot="2", name="SCATTER-V",
        fire_interval=0.55, damage=5.0, bolt_speed=500, bolt_radius=4,
        pellets=5, spread_deg=17,
        color=(255, 175, 70), sound="scatter",
    ),
    WeaponSpec(
        slot="3", name="HOMER-X",
        fire_interval=0.5, damage=26.0, bolt_speed=250, bolt_radius=6,
        homing=True, turn_rate=3.6, aoe=95.0,
        color=(255, 95, 125), sound="missile",
    ),
]
