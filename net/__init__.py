"""LAN co-op networking: host-authoritative, stdlib only.

One machine hosts the simulation (and plays). Clients send inputs (~60 Hz)
and receive full-state snapshots (~30 Hz) as newline-delimited JSON.
"""