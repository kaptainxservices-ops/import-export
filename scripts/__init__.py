"""Operational scripts. A package so they can import from one another.

`check_writes.py` reuses `load_samples.read_email` rather than keeping a second copy of
the .eml parsing — two copies would drift, and the one that drifted would be the one
proving the writes are safe.
"""
