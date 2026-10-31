"""
EBU conformance check for AnalyzeDynamicRange.py.

Runs the official EBU loudness test signals through the analysis and compares
integrated loudness and LRA against the expected values published in
EBU Tech 3341 (integrated, tolerance ±0.1 LU) and EBU Tech 3342 (LRA,
tolerance ±1 LU).

The test signals are not redistributed with this repository. Download the
"EBU Loudness Test Set" from https://tech.ebu.ch/publications/ebu_loudness_test_set
and unpack it into a folder, then run:

    python tests/ebu_conformance.py <folder>

Files that are missing are skipped. Exit code is 0 when every checked file
is within tolerance, 1 otherwise.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import AnalyzeDynamicRange as adr  # noqa: E402

# (file name, expected integrated LUFS or None, expected LRA LU or None)
# Expected values per EBU Tech 3341 / 3342. Stereo signals unless noted.
_CASES = [
    # Tech 3341 – integrated loudness (tolerance ±0.1 LU)
    ("seq-3341-1-16bit.wav", -23.0, None),
    ("seq-3341-2-16bit.wav", -33.0, None),
    ("seq-3341-3-16bit-v02.wav", -23.0, None),
    ("seq-3341-4-16bit-v02.wav", -23.0, None),
    ("seq-3341-5-16bit-v02.wav", -23.0, None),
    ("seq-3341-6-6channels-WAVEEX-16bit.wav", -23.0, None),   # 5.1
    ("seq-3341-7_seq-3342-5-24bit.wav", -23.0, 5.0),
    ("seq-3341-2011-8_seq-3342-6-24bit-v02.wav", -23.0, 15.0),
    # Tech 3342 – loudness range (tolerance ±1 LU)
    ("seq-3342-1-16bit.wav", None, 10.0),
    ("seq-3342-2-16bit.wav", None, 5.0),
    ("seq-3342-3-16bit.wav", None, 20.0),
    ("seq-3342-4-16bit.wav", None, 15.0),
]

_TOL_I = 0.1
_TOL_LRA = 1.0


def _analyze_quiet(path):
    """Run analyze() with console output suppressed."""
    with contextlib.redirect_stdout(io.StringIO()):
        return adr.analyze(path)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    folder = sys.argv[1]

    print(f"AnalyzeDynamicRange {adr.__version__} - EBU conformance\n")
    print(f"{'File':<44} {'I exp':>6} {'I got':>7} {'dI':>6}  "
          f"{'LRA exp':>7} {'LRA got':>7} {'dLRA':>6}  Result")
    print("-" * 104)

    n_checked = n_failed = 0
    for name, exp_i, exp_lra in _CASES:
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            print(f"{name:<44} {'(missing - skipped)'}")
            continue

        r = _analyze_quiet(path)
        got_i, got_lra = r["integrated_lufs"], r["lra_lu"]
        ok = True
        cols = []
        if exp_i is not None:
            d = got_i - exp_i
            ok &= abs(d) <= _TOL_I
            cols.append(f"{exp_i:6.1f} {got_i:7.2f} {d:+6.2f}")
        else:
            cols.append(f"{'':6} {'':7} {'':6}")
        if exp_lra is not None:
            d = got_lra - exp_lra
            ok &= abs(d) <= _TOL_LRA
            cols.append(f"{exp_lra:7.1f} {got_lra:7.2f} {d:+6.2f}")
        else:
            cols.append(f"{'':7} {'':7} {'':6}")

        n_checked += 1
        n_failed += not ok
        print(f"{name:<44} {cols[0]}  {cols[1]}  {'PASS' if ok else 'FAIL'}")

    print("-" * 104)
    print(f"{n_checked} checked, {n_checked - n_failed} passed, "
          f"{n_failed} failed")
    sys.exit(1 if n_failed or not n_checked else 0)


if __name__ == "__main__":
    main()
