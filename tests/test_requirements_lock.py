"""Regression checks for resolved runtime dependency security fixes."""

from pathlib import Path


def test_runtime_lock_pins_fixed_soupsieve() -> None:
    """Keep the runtime lock on the vendor version that fixes #590 CVEs."""
    lockfile = Path("requirements.txt").read_text(encoding="utf-8").splitlines()
    soupsieve_pin = next(line for line in lockfile if line.startswith("soupsieve=="))

    assert soupsieve_pin == "soupsieve==2.9"
