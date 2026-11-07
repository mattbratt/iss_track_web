"""ISS Track (web): the service that serves the engine over HTTP.

Nothing here computes an orbit, a pass or a brightness. Every figure comes
from the ISS Track integration's engine, imported from the pinned submodule
by vendor.py. This package decides when to ask the engine, and for whom.
"""
