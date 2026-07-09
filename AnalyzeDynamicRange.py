# -*- coding: utf-8 -*-
"""
AnalyzeDynamicRange.py - Analyse the dynamic range / loudness of film audio.

Computes broadcast-standard loudness metrics according to ITU-R BS.1770-4
and EBU R128 (the K-weighting filter and gating are implemented natively,
so no extra packages beyond numpy/scipy/soundfile are required):

    - Integrated Loudness (LUFS)        gated programme loudness
    - Loudness Range (LRA, in LU)        EBU Tech 3342, the key dynamic-
                                          range metric for film
    - True Peak (dBTP)                    4x oversampled inter-sample peak
    - Momentary / Short-term loudness    time-series (400 ms / 3 s windows)
    - DR score                            peak-to-RMS based crest metric

Channel handling
    By default a standard channel order is assumed for the loudness sum:
        mono   : [C]
        stereo : [L, R]
        >= 5 ch: [L, R, C, LFE, Ls, Rs, ...] - the LFE is excluded and
                 the surround channels are weighted +1.5 dB per BS.1770.
    Use --layout / --lfe-channel to override.

Requirements
    pip install numpy scipy soundfile
    pip install matplotlib   # optional, only needed for --plot

Usage
    python AnalyzeDynamicRange.py film.wav
    python AnalyzeDynamicRange.py film.wav --per-channel
    python AnalyzeDynamicRange.py film.wav --exclude-surround
    python AnalyzeDynamicRange.py film.wav --plot loudness.png
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import soundfile as sf
from scipy.signal import lfilter, resample_poly


# Absolute silence floor used for log conversions to avoid log10(0).
_EPS = 1e-12

# EBU R128 / BS.1770 gating constants.
_ABS_GATE_LUFS = -70.0      # absolute gate
_REL_GATE_LU = 10.0         # relative gate below ungated mean (integrated)
_LRA_REL_GATE_LU = 20.0     # relative gate for LRA (EBU Tech 3342)


def _biquad_kweighting(sr):
    """Return the two BS.1770-4 K-weighting biquads for a sample rate.

    The K-weighting consists of a high-shelf "pre-filter" (stage 1) and an
    RLB high-pass (stage 2).  Coefficients are derived analytically from the
    analogue prototypes so that the filter is valid for any sample rate.

    Returns
        ((b1, a1), (b2, a2)) numerator/denominator pairs for the two stages.
    """
    # Stage 1: high-shelf pre-filter.
    f0 = 1681.9744509555319
    g_db = 3.99984385397
    q = 0.7071752369554193

    k = np.tan(np.pi * f0 / sr)
    vh = 10.0 ** (g_db / 20.0)
    vb = vh ** 0.4996667741545416
    a0 = 1.0 + k / q + k * k
    b_stage1 = [
        (vh + vb * k / q + k * k) / a0,
        2.0 * (k * k - vh) / a0,
        (vh - vb * k / q + k * k) / a0,
    ]
    a_stage1 = [
        1.0,
        2.0 * (k * k - 1.0) / a0,
        (1.0 - k / q + k * k) / a0,
    ]

    # Stage 2: RLB high-pass filter.
    f0 = 38.13547087613982
    q = 0.5003270373253953
    k = np.tan(np.pi * f0 / sr)
    a_stage2 = [
        1.0,
        2.0 * (k * k - 1.0) / (1.0 + k / q + k * k),
        (1.0 - k / q + k * k) / (1.0 + k / q + k * k),
    ]
    b_stage2 = [1.0, -2.0, 1.0]

    return (b_stage1, a_stage1), (b_stage2, a_stage2)


def _k_weight(data, sr):
    """Apply the BS.1770 K-weighting filter to multi-channel data.

    Parameters
        data  Array of shape (n_samples, n_channels) in float.
        sr    Sample rate in Hz.

    Returns
        K-weighted array of the same shape.
    """
    (b1, a1), (b2, a2) = _biquad_kweighting(sr)
    stage1 = lfilter(b1, a1, data, axis=0)
    return lfilter(b2, a2, stage1, axis=0)


def _channel_weights(n_ch, layout=None, lfe_channel=None,
                     exclude_surround=False):
    """Determine the BS.1770 channel gain weights for the loudness sum.

    Surround channels receive +1.5 dB (factor 1.41); the LFE is excluded.

    Parameters
        n_ch              Number of channels in the file.
        layout            Optional explicit layout: one of "mono", "stereo",
                          "5.1", "7.1" or None for auto-detection.
        lfe_channel       Optional 0-based index of the LFE channel to
                          exclude.
        exclude_surround  When True, the surround channels are excluded from
                          the loudness sum (gain 0.0) instead of being
                          weighted +1.5 dB - useful for a front/dialogue-only
                          analysis.

    Returns
        numpy array of length n_ch with the per-channel weights.
    """
    # Surround channels are weighted +1.5 dB, or muted when excluded.
    surround = 0.0 if exclude_surround else 1.41
    weights = np.ones(n_ch, dtype=np.float64)

    if layout is None:
        if n_ch >= 6:
            layout = "5.1" if n_ch == 6 else "7.1" if n_ch == 8 else "auto"
        else:
            layout = "auto"

    # Standard SMPTE/ITU order: L R C LFE Ls Rs [Lrs Rrs].
    if layout == "5.1" and n_ch >= 6:
        weights[3] = 0.0                 # LFE excluded
        weights[4] = surround            # Ls
        weights[5] = surround            # Rs
    elif layout == "7.1" and n_ch >= 8:
        weights[3] = 0.0                 # LFE excluded
        weights[4:8] = surround          # Ls Rs Lrs Rrs
    elif layout == "auto":
        # Weight any channel beyond the front L/R/C as surround.
        if n_ch > 3:
            weights[3:] = surround

    if lfe_channel is not None and 0 <= lfe_channel < n_ch:
        weights[lfe_channel] = 0.0

    return weights


def _block_mean_square(weighted, sr, win_s, step_s):
    """Compute the per-block, per-channel mean square over a sliding window.

    Parameters
        weighted  The K-weighted signal of shape (n_samples, n_channels).
        sr        Sample rate in Hz.
        win_s     Window length in seconds.
        step_s    Hop size in seconds.

    Returns
        The per-block, per-channel mean square of shape (n_blocks, n_ch).
        Channel weighting is applied later by the caller together with the
        channel weights.
    """
    n_samples = weighted.shape[0]
    win = int(round(win_s * sr))
    step = max(int(round(step_s * sr)), 1)
    if win <= 0 or n_samples < win:
        return np.empty((0, weighted.shape[1]))

    z_list = []
    for start in range(0, n_samples - win + 1, step):
        block = weighted[start:start + win]
        z_list.append(np.mean(block * block, axis=0))
    if not z_list:
        return np.empty((0, weighted.shape[1]))
    return np.vstack(z_list)


def _loudness_from_z(z, weights):
    """Convert per-block, per-channel mean square to block loudness (LUFS)."""
    summed = z @ weights
    return -0.691 + 10.0 * np.log10(np.maximum(summed, _EPS))


def _integrated_loudness(z, weights):
    """Gated integrated loudness (LUFS) from 400 ms block mean squares."""
    if z.shape[0] == 0:
        return float("nan")

    block_loudness = _loudness_from_z(z, weights)

    # Absolute gate at -70 LUFS.
    abs_mask = block_loudness > _ABS_GATE_LUFS
    if not np.any(abs_mask):
        return float("nan")

    # Relative gate: -10 LU below the mean of the absolute-gated blocks.
    mean_z = np.mean(z[abs_mask], axis=0)
    gate = -0.691 + 10.0 * np.log10(max(float(mean_z @ weights), _EPS)) \
        - _REL_GATE_LU
    rel_mask = abs_mask & (block_loudness > gate)
    if not np.any(rel_mask):
        return float("nan")

    final_z = np.mean(z[rel_mask], axis=0)
    return -0.691 + 10.0 * np.log10(max(float(final_z @ weights), _EPS))


def _loudness_range(short_term_loudness):
    """Loudness Range (LU) per EBU Tech 3342 from short-term loudness."""
    lv = short_term_loudness[short_term_loudness > _ABS_GATE_LUFS]
    if lv.size < 2:
        return float("nan")

    gate = np.mean(lv) - _LRA_REL_GATE_LU
    gated = lv[lv >= gate]
    if gated.size < 2:
        return float("nan")

    return float(np.percentile(gated, 95) - np.percentile(gated, 10))


def _true_peak_dbtp(data, sr):
    """Estimate the true peak (dBTP) via 4x oversampling per channel.

    Returns
        (overall_dbtp, per_channel_dbtp) where per_channel_dbtp is a list.
    """
    # 4x oversampling is the BS.1770 Annex 2 recommendation up to 96 kHz.
    factor = 4 if sr <= 96000 else 2
    oversampled = resample_poly(data, factor, 1, axis=0)
    if oversampled.ndim == 1:
        oversampled = oversampled[:, np.newaxis]

    peaks = np.max(np.abs(oversampled), axis=0)
    per_channel = [20.0 * np.log10(max(float(p), _EPS)) for p in peaks]
    overall = max(per_channel)
    return overall, per_channel


def _dr_score(data, sr, block_s=3.0, top_fraction=0.2):
    """TT Dynamic Range style DR score (crest factor of the loudest blocks).

    A higher value means more dynamic range; heavily compressed material
    yields low values (DR <= 6).

    Parameters
        data          Mono or multi-channel float array.
        sr            Sample rate in Hz.
        block_s       Block length in seconds.
        top_fraction  Fraction of the loudest blocks (by RMS) to evaluate.

    Returns
        The DR score, rounded to the nearest integer.
    """
    mono = data.mean(axis=1) if data.ndim > 1 else data
    block_len = max(int(round(block_s * sr)), 1)
    n_blocks = len(mono) // block_len
    if n_blocks == 0:
        return float("nan")

    blocks = mono[:n_blocks * block_len].reshape(n_blocks, block_len)
    rms = np.sqrt(np.mean(blocks * blocks, axis=1))
    peak = np.max(np.abs(blocks), axis=1)

    n_top = max(1, int(round(n_blocks * top_fraction)))
    top_idx = np.argsort(rms)[-n_top:]

    rms_top = np.sqrt(np.mean(rms[top_idx] ** 2))
    peak_top = np.mean(peak[top_idx])
    if rms_top < _EPS:
        return float("nan")

    return round(20.0 * np.log10(peak_top / rms_top))


def _rms_dbfs(data):
    """Overall RMS level in dBFS across all channels."""
    rms = np.sqrt(np.mean(np.square(data)))
    return 20.0 * np.log10(max(float(rms), _EPS))


def analyze(path, layout=None, lfe_channel=None, per_channel=False,
            exclude_surround=False):
    """Analyse the dynamic range / loudness of an audio file.

    Parameters
        path              Path to the audio file.
        layout            Optional channel layout override.
        lfe_channel       Optional 0-based LFE channel index to exclude.
        per_channel       When True, integrated loudness is also reported for
                          each channel individually.
        exclude_surround  When True, the surround channels are excluded from
                          the loudness sum (front/dialogue-only analysis).

    Returns
        Dict of computed metrics plus the short-term loudness time-series.
    """
    data, sr = sf.read(path, dtype="float64", always_2d=True)
    n_ch = data.shape[1]

    print(f"File         : {path}")
    print(f"Sample rate  : {sr} Hz")
    print(f"Channels     : {n_ch}")
    print(f"Duration     : {len(data) / sr:.1f} s\n")

    # Reference weights (surrounds at +1.5 dB) identify the channel roles
    # independently of whether the surrounds are excluded from this analysis.
    base_weights = _channel_weights(n_ch, layout=layout,
                                    lfe_channel=lfe_channel)
    weights = _channel_weights(n_ch, layout=layout, lfe_channel=lfe_channel,
                               exclude_surround=exclude_surround)

    lfe_ch = [i + 1 for i, w in enumerate(base_weights) if w == 0.0]
    surround_ch = [i + 1 for i, w in enumerate(base_weights)
                   if abs(w - 1.41) < 1e-6]
    if lfe_ch:
        print(f"Excluded ch  : {lfe_ch} (LFE, not part of loudness sum)")
    if surround_ch:
        if exclude_surround:
            print(f"Surround ch  : {surround_ch} (excluded from analysis)")
        else:
            print(f"Surround ch  : {surround_ch} (weighted +1.5 dB)")
    print()

    weighted = _k_weight(data, sr)

    # 400 ms momentary blocks (75% overlap) drive the integrated loudness.
    z_400 = _block_mean_square(weighted, sr, win_s=0.4, step_s=0.1)
    integrated = _integrated_loudness(z_400, weights)
    momentary = _loudness_from_z(z_400, weights) if z_400.shape[0] else \
        np.array([])

    # 3 s short-term blocks drive the LRA and the time-series plot.
    z_3s = _block_mean_square(weighted, sr, win_s=3.0, step_s=0.1)
    short_term = _loudness_from_z(z_3s, weights) if z_3s.shape[0] else \
        np.array([])
    lra = _loudness_range(short_term)

    true_peak, tp_per_ch = _true_peak_dbtp(data, sr)
    dr = _dr_score(data, sr)
    rms = _rms_dbfs(data)

    print("=== Dynamic Range / Loudness ===")
    print(f"  Integrated loudness : {integrated:8.2f} LUFS")
    print(f"  Loudness range (LRA): {lra:8.2f} LU")
    print(f"  True peak           : {true_peak:8.2f} dBTP")
    print(f"  DR score            : {dr:8.0f}")
    print(f"  RMS level           : {rms:8.2f} dBFS")
    if momentary.size:
        print(f"  Momentary max       : {np.max(momentary):8.2f} LUFS")
    if short_term.size:
        print(f"  Short-term max      : {np.max(short_term):8.2f} LUFS")
        print(f"  Short-term min      : "
              f"{np.min(short_term[short_term > _ABS_GATE_LUFS]):8.2f} LUFS")
    print()

    if true_peak > -1.0:
        print(f"  [WARN] True peak exceeds -1 dBTP - risk of clipping on "
              f"downstream conversion.")
    if not np.isnan(lra) and lra < 5.0:
        print(f"  [INFO] Low LRA ({lra:.1f} LU) - heavily compressed for film.")

    if per_channel:
        print("\n=== Per-channel integrated loudness ===")
        for ch in range(n_ch):
            ch_weight = np.zeros(n_ch)
            # Measure each channel on its own (unity weight, surround removed).
            ch_weight[ch] = 1.0
            ch_int = _integrated_loudness(z_400, ch_weight)
            tag = ""
            if base_weights[ch] == 0.0:
                tag = " (LFE, excluded from sum)"
            elif abs(base_weights[ch] - 1.41) < 1e-6:
                tag = " (surround, excluded from sum)" if exclude_surround \
                    else " (surround)"
            print(f"  Channel {ch + 1:2d}: {ch_int:8.2f} LUFS  "
                  f"true peak {tp_per_ch[ch]:7.2f} dBTP{tag}")

    return {
        "sr": sr,
        "n_channels": n_ch,
        "integrated_lufs": integrated,
        "lra_lu": lra,
        "true_peak_dbtp": true_peak,
        "dr_score": dr,
        "rms_dbfs": rms,
        "short_term": short_term,
        "momentary": momentary,
        "step_s": 0.1,
    }


def _plot(result, out_path):
    """Render the short-term loudness time-series to an image file."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        sys.exit("[ERR] --plot requires matplotlib. Install with "
                 "'pip install matplotlib'.")

    short_term = result["short_term"]
    if short_term.size == 0:
        sys.exit("[ERR] Not enough audio to plot a short-term loudness curve.")

    # Short-term windows are 3 s long, hopped by step_s; centre the curve.
    t = np.arange(short_term.size) * result["step_s"] + 1.5

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(t, short_term, lw=0.8, color="#1f77b4", label="Short-term loudness")
    ax.axhline(result["integrated_lufs"], color="#d62728", ls="--", lw=1.0,
               label=f"Integrated {result['integrated_lufs']:.1f} LUFS")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Loudness (LUFS)")
    ax.set_title(f"Film loudness over time  -  LRA "
                 f"{result['lra_lu']:.1f} LU, true peak "
                 f"{result['true_peak_dbtp']:.1f} dBTP")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    print(f"\n  [PLOT] {out_path}")


def main():
    ap = argparse.ArgumentParser(
        description="Analyse the dynamic range and loudness of a film audio "
                    "file (ITU-R BS.1770-4 / EBU R128).")
    ap.add_argument("audio",
                    help="Path to the audio file to analyse")
    ap.add_argument("--layout", choices=["mono", "stereo", "5.1", "7.1"],
                    default=None,
                    help="Channel layout override (default: auto-detect)")
    ap.add_argument("--lfe-channel", type=int, default=None, metavar="N",
                    help="0-based index of the LFE channel to exclude")
    ap.add_argument("--per-channel", action="store_true",
                    help="Also report loudness and true peak per channel")
    ap.add_argument("--exclude-surround", action="store_true",
                    help="Exclude the surround channels from the analysis "
                         "(front/dialogue-only loudness)")
    ap.add_argument("--plot", metavar="FILE", default=None,
                    help="Render the short-term loudness curve to an image")
    args = ap.parse_args()

    result = analyze(args.audio, layout=args.layout,
                     lfe_channel=args.lfe_channel,
                     per_channel=args.per_channel,
                     exclude_surround=args.exclude_surround)

    if args.plot:
        _plot(result, args.plot)


if __name__ == "__main__":
    main()
