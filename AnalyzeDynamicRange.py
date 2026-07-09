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

Channel handling
    By default a Microsoft wave format channel order is assumed for the loudness sum:
        mono   : [C]
        stereo : [L, R]
        5.1    : [L, R, C, LFE, Ls, Rs]
        6.1    : [L, R, C, LFE, Rc, Ls, Rs]
        7.1    : [L, R, C, LFE, Lrs, Rrs, Ls, Rs]
    The LFE is excluded and surround channels are weighted +1.5 dB per BS.1770.
    Use --layout / --lfe-channel to override.

Requirements
    pip install numpy scipy soundfile
    pip install matplotlib   # optional, only needed for --plot

Usage
    python AnalyzeDynamicRange.py film.wav
    python AnalyzeDynamicRange.py film.wav --per-channel
    python AnalyzeDynamicRange.py film.wav --exclude-surround
    python AnalyzeDynamicRange.py film.wav --plot
    python AnalyzeDynamicRange.py film.wav --plot loudness_analysis.png
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import gc
import glob
import math
import os
import sys
import time

import numpy as np
import soundfile as sf
from scipy.interpolate import PchipInterpolator
from scipy.signal import butter, lfilter, resample_poly, sosfilt, sosfiltfilt, welch


# Absolute silence floor used for log conversions to avoid log10(0).
_EPS = 1e-12

# EBU R128 / BS.1770 gating constants.
_ABS_GATE_LUFS = -70.0      # absolute gate
_REL_GATE_LU = 10.0         # relative gate below ungated mean (integrated)
_LRA_REL_GATE_LU = 20.0     # relative gate for LRA (EBU Tech 3342)

# Downsampling for loudness analysis.
# K-weighting filters top out at ~1.7 kHz; 16 kHz offers a generous safety
# margin while reducing the working-set by 3× at a typical 48 kHz source.
_LOUDNESS_SR = 16000

# LFE metrics only require signal content up to 120 Hz.  A 4 kHz working rate
# (Nyquist = 2 kHz) is more than sufficient and reduces the LFE array size by
# 4× vs. _LOUDNESS_SR, making the lowpass filter and band analysis ~4× faster.
# Surround RMS is still measured at _LOUDNESS_SR to avoid an implicit lowpass
# on L, R and Center channels that would skew the relative level comparison.
_LFE_SR = 4000


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

    Each channel is filtered in a separate thread.  lfilter releases the GIL
    during its C-level computation, so threads run in parallel on multi-core
    systems.

    Parameters
        data  Array of shape (n_samples, n_channels) in float.
        sr    Sample rate in Hz.

    Returns
        K-weighted array of the same shape and dtype as *data*.
    """
    (b1, a1), (b2, a2) = _biquad_kweighting(sr)
    n_ch = data.shape[1]

    def _filter_ch(ch):
        s1 = lfilter(b1, a1, data[:, ch])
        return lfilter(b2, a2, s1)

    with ThreadPoolExecutor(max_workers=n_ch) as ex:
        cols = list(ex.map(_filter_ch, range(n_ch)))

    out = np.empty_like(data)
    for ch, col in enumerate(cols):
        out[:, ch] = col
    return out.astype(data.dtype, copy=False)


def _channel_weights(n_ch, layout=None, lfe_channel=None,
                     exclude_surround=False):
    """Determine the BS.1770 channel gain weights for the loudness sum.

    Surround channels receive +1.5 dB (factor 1.41); the LFE is excluded.

    Parameters
        n_ch              Number of channels in the file.
        layout            Optional explicit layout: one of "mono", "stereo",
                          "5.1", "6.1", "7.1" or None for auto-detection.
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
    weights = np.ones(n_ch, dtype=np.float32)

    if layout is None:
        if n_ch >= 6:
            layout = "5.1" if n_ch == 6 else "6.1" if n_ch == 7 else "7.1" if n_ch == 8 else "auto"
        else:
            layout = "auto"

    # Channel order: L R C LFE [Rc] [Lrs Rrs] Ls Rs.
    if layout == "5.1" and n_ch >= 6:
        weights[3] = 0.0                 # LFE excluded
        weights[4] = surround            # Ls
        weights[5] = surround            # Rs
    elif layout == "6.1" and n_ch >= 7:
        weights[3] = 0.0                 # LFE excluded
        weights[4] = surround            # Rc (Rear Center)
        weights[5] = surround            # Ls
        weights[6] = surround            # Rs
    elif layout == "7.1" and n_ch >= 8:
        weights[3] = 0.0                 # LFE excluded
        weights[4] = surround            # Lrs
        weights[5] = surround            # Rrs
        weights[6] = surround            # Ls
        weights[7] = surround            # Rs
    elif layout == "auto":
        # Weight any channel beyond the front L/R/C as surround.
        if n_ch > 3:
            weights[3:] = surround

    if lfe_channel is not None and 0 <= lfe_channel < n_ch:
        weights[lfe_channel] = 0.0

    return weights


def _block_mean_square(weighted, sr, win_s, step_s):
    """Compute the per-block, per-channel mean square over a sliding window.

    Uses a prefix-sum-of-squares array so that every block mean is a single
    vector subtraction instead of a full slice reduction.  This removes the
    Python loop and lowers complexity from O(n_samples × n_blocks) to
    O(n_samples + n_blocks), cutting runtime by a factor of 10–50× for long
    files.

    Memory: one additional array of shape (n_samples + 1, n_ch) at the same
    dtype as *weighted* is allocated temporarily; it is freed on return.

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
    n_ch = weighted.shape[1]
    win = int(round(win_s * sr))
    step = max(int(round(step_s * sr)), 1)
    if win <= 0 or n_samples < win:
        return np.empty((0, n_ch), dtype=weighted.dtype)

    # Prefix sum of squares S where S[k] = sum(weighted[0..k-1]^2).
    # S[0] = 0 (prepended zero row) ensures a uniform formula for all blocks,
    # including the first one that starts at sample 0.
    # Always use float64 here: a float32 cumsum over millions of samples loses
    # precision via catastrophic cancellation when quiet windows follow loud
    # ones, causing the absolute gate (-70 LUFS) to misclassify blocks.
    S = np.empty((n_samples + 1, n_ch), dtype=np.float64)
    S[0] = 0.0
    np.cumsum(np.square(weighted, dtype=np.float64), axis=0, out=S[1:])

    starts = np.arange(0, n_samples - win + 1, step, dtype=np.intp)
    if starts.size == 0:
        return np.empty((0, n_ch), dtype=np.float64)

    # sum(weighted[start..start+win-1]^2) = S[start+win] - S[start]
    return (S[starts + win] - S[starts]) / win


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


def _rms_dbfs(data):
    """Overall RMS level in dBFS across all channels."""
    rms = np.sqrt(np.mean(np.square(data)))
    return 20.0 * np.log10(max(float(rms), _EPS))


def _downsample(data, sr, target_sr):
    """Downsample *data* to *target_sr* with anti-aliasing via resample_poly.

    Uses the GCD of the two rates to keep the up/down integer ratio as small
    as possible, which keeps the FIR filter short and fast.  Each channel is
    resampled in a separate thread so that multi-core CPUs are used; SciPy
    releases the GIL during the FIR convolution, making thread parallelism
    effective here.

    Parameters
        data       Audio array of shape (n_samples, n_channels) or 1D.
        sr         Original sample rate in Hz (int).
        target_sr  Target sample rate in Hz (int).

    Returns
        Tuple (resampled_data, target_sr).  Returns (data, sr) unchanged when
        sr is already at or below target_sr.
    """
    if sr <= target_sr:
        return data, sr
    g = math.gcd(int(sr), int(target_sr))
    up = target_sr // g
    down = sr // g

    if data.ndim == 1:
        return resample_poly(data, up, down).astype(data.dtype), target_sr

    n_ch = data.shape[1]
    out_len = len(resample_poly(data[:1, 0], up, down))  # probe output length

    def _resample_ch(ch):
        return resample_poly(data[:, ch], up, down)

    with ThreadPoolExecutor(max_workers=n_ch) as ex:
        cols = list(ex.map(_resample_ch, range(n_ch)))

    result = np.empty((out_len, n_ch), dtype=data.dtype)
    for ch, col in enumerate(cols):
        result[:, ch] = col
    return result, target_sr


_FREQ_RESPONSE_TARGET_SR = 800
_FREQ_RESPONSE_F_MIN = 2.0
_FREQ_RESPONSE_F_MAX = 200.0


def _frequency_response(audio_data, sr, fraction=24,
                        f_min=_FREQ_RESPONSE_F_MIN,
                        f_max=_FREQ_RESPONSE_F_MAX):
    """Calculate the fractional-octave-smoothed frequency response in dB.

    The input is first downsampled to _FREQ_RESPONSE_TARGET_SR Hz so that
    the Welch periodogram achieves the finest possible frequency resolution
    in the 1–200 Hz band of interest (bin spacing ≈ 0.006 Hz at 800 Hz /
    2^17 segment length).  The result is normalised to a 0 dB peak so
    curves from different channels overlay directly.

    Parameters
        audio_data  Audio signal (1D float array).
        sr          Original sample rate in Hz.
        fraction    Octave fraction for smoothing (24 = 1/242 octave).
        f_min       Lowest band centre frequency in Hz.
        f_max       Highest band centre frequency in Hz.

    Returns
        Tuple (band_freqs, band_db) — band centre frequencies in Hz and the
        corresponding 0 dB-normalised power level per band.
    """
    target_sr = _FREQ_RESPONSE_TARGET_SR
    f_max = min(f_max, target_sr / 2.0 * 0.95)

    # Downsample with anti-aliasing via resample_poly if the source rate is
    # higher than the analysis target rate.
    if sr > target_sr:
        g = math.gcd(sr, target_sr)
        audio_ds = resample_poly(audio_data, target_sr // g, sr // g)
        analysis_sr = target_sr
    else:
        audio_ds = audio_data
        analysis_sr = sr

    nperseg = min(len(audio_ds), 2 ** 17)
    freqs, psd = welch(audio_ds, fs=analysis_sr, nperseg=nperseg,
                       window='hann', noverlap=nperseg // 2,
                       scaling='density')

    # Build 1/fraction-octave centre-frequency grid
    half_bw = 2.0 ** (1.0 / (2.0 * fraction))
    n_bands = int(np.ceil(np.log2(f_max / f_min) * fraction)) + 1
    band_freqs = f_min * 2.0 ** (np.arange(n_bands) / fraction)
    band_freqs = band_freqs[band_freqs <= f_max]

    # Average PSD within each band (searchsorted avoids an O(N) mask per band)
    band_psd = np.full(len(band_freqs), np.nan)
    for i, fc in enumerate(band_freqs):
        i_low = np.searchsorted(freqs, fc / half_bw)
        i_high = np.searchsorted(freqs, fc * half_bw, side='right')
        if i_high > i_low:
            band_psd[i] = np.mean(psd[i_low:i_high])

    # Convert to dB and normalise to 0 dB peak
    valid = ~np.isnan(band_psd)
    band_db = np.full(len(band_freqs), np.nan)
    band_db[valid] = 10.0 * np.log10(np.maximum(band_psd[valid], _EPS))
    if np.any(valid):
        band_db[valid] -= np.max(band_db[valid])

    # Interpolate onto a dense log-spaced grid so the plotted curve is smooth.
    # PCHIP preserves monotonicity between support points and avoids the
    # oscillations that cubic splines can produce near step transitions.
    valid_mask = ~np.isnan(band_db)
    n_valid = np.sum(valid_mask)
    if n_valid >= 4:
        log_x = np.log(band_freqs[valid_mask])
        interp = PchipInterpolator(log_x, band_db[valid_mask])
        fine_log_x = np.linspace(np.log(f_min), np.log(f_max), 500)
        return np.exp(fine_log_x), interp(fine_log_x)

    return band_freqs[valid_mask], band_db[valid_mask]



_LFE_LOWPASS_HZ = 120.0
_LFE_LOWPASS_ORDER = 4

# Frequency bands for LFE band analysis.
# Each entry: (display label, lower cutoff Hz or None, upper cutoff Hz,
#              geometric-mean centre frequency Hz for centroid calculation).
_LFE_BANDS = [
    ("<20 Hz",    None,  20.0,  10.0),
    ("20-40 Hz",  20.0,  40.0,  28.3),
    ("40-80 Hz",  40.0,  80.0,  56.6),
    ("80-120 Hz", 80.0, 120.0,  98.0),
]

# The LFE activity threshold is set this many dB below the main-mix
# integrated loudness.  Frequency-band statistics are computed only over
# windows that exceed this threshold so that long silent passages do not
# distort the results.
_LFE_ACTIVITY_OFFSET_DB = 15.0

_SURROUND_HIGHPASS_HZ = 80.0
_SURROUND_HIGHPASS_ORDER = 4


def _lfe_lowpass(data_lfe, sr):
    """Apply a zero-phase Butterworth low-pass filter at _LFE_LOWPASS_HZ to
    the LFE channel before any metric is computed.

    A zero-phase (forward-backward) filter is used so that no phase distortion
    is introduced, which keeps time-domain peak measurements accurate.

    Parameters
        data_lfe  LFE channel data (1D float array).
        sr        Sample rate in Hz.

    Returns
        Filtered copy of data_lfe.
    """
    nyquist = sr / 2.0
    if _LFE_LOWPASS_HZ >= nyquist:
        return data_lfe

    sos = butter(_LFE_LOWPASS_ORDER, _LFE_LOWPASS_HZ / nyquist,
                 btype='low', output='sos')
    return sosfiltfilt(sos, data_lfe)



def _lra_label(lra):
    if lra >= 35:
        return "extreme dynamic"
    if lra >= 30:
        return "high dynamic"
    if lra >= 25:
        return "dynamic"
    if lra >= 20:
        return "moderate"
    if lra >= 15:
        return "compressed"
    return "heavily compressed"


def _lfe_activity_label(pct):
    if pct >= 20:
        return "overused"
    if pct >= 15:
        return "very active"
    if pct >= 10:
        return "active"
    if pct >= 5:
        return "moderate"
    return "restrained"


def _sub_bass_ratio_label(db):
    if db >= 0:
        return "seismic"
    if db >= -3:
        return "deep"
    if db >= -6:
        return "moderate"
    return "upper-bass"


def _lfe_band_analysis(data_lfe, sr, integrated_main):
    """Analyse the LFE channel by frequency band.

    Divides the already-low-passed LFE signal into four bands
    (< 20 Hz, 20–40 Hz, 40–80 Hz, 80–120 Hz) and computes activity,
    P95 and peak short-term levels, and the peak-to-P95 spread for
    each band.  All level metrics are relative to the main-mix
    integrated loudness so that results are comparable across films
    with different overall loudness.

    A sliding 400 ms window with 100 ms hop is used throughout.
    Only windows in which the full-band LFE short-term RMS exceeds
    ``integrated_main − _LFE_ACTIVITY_OFFSET_DB`` (dBFS) are
    included in the P95 / Peak / centroid statistics; this prevents
    long silent passages from distorting the results.

    Parameters
        data_lfe        Filtered LFE channel (1D, already low-passed
                        at _LFE_LOWPASS_HZ).
        sr              Sample rate in Hz.
        integrated_main Integrated loudness of the main mix in LUFS.

    Returns
        Dict with keys ``bands``, ``threshold_dBFS``,
        ``sub_bass_ratio_db``, ``infra_ratio_db``,
        ``spectral_centroid_hz``, or None if the signal is too short.
    """
    nyquist = sr / 2.0
    win = int(round(0.4 * sr))
    step = max(int(round(0.1 * sr)), 1)
    n = len(data_lfe)

    starts = np.arange(0, n - win + 1, step, dtype=np.intp)
    if starts.size == 0:
        return None

    # Activity threshold: main integrated loudness − offset, in linear RMS.
    threshold_dBFS = integrated_main - _LFE_ACTIVITY_OFFSET_DB
    threshold_rms = 10.0 ** (threshold_dBFS / 20.0)

    # Vectorised sliding-window mean-square via prefix sums (float64 to
    # avoid subnormal-float slowdowns during IIR filter computation).
    def _win_ms(sig):
        sq = np.square(sig.astype(np.float64, copy=False))
        S = np.empty(n + 1, dtype=np.float64)
        S[0] = 0.0
        np.cumsum(sq, out=S[1:])
        return (S[starts + win] - S[starts]) / win

    # Global activity mask: windows where full-band LFE exceeds threshold.
    global_rms = np.sqrt(_win_ms(data_lfe))
    global_mask = global_rms >= threshold_rms
    n_global_active = int(global_mask.sum())
    n_windows = len(global_mask)

    # Overall LFE activity as fraction of total runtime.
    global_activity_pct = 100.0 * n_global_active / n_windows if n_windows > 0 else 0.0

    band_results = []
    avg_ms_per_band = []   # mean-square over globally-active windows, per band

    for label, f_low, f_high, _fc in _LFE_BANDS:
        # Design a 2nd-order Butterworth band filter.
        if f_low is None:
            wn = min(f_high, nyquist * 0.995) / nyquist
            sos = butter(2, wn, btype='low', output='sos')
        else:
            wn = [f_low / nyquist,
                  min(f_high, nyquist * 0.995) / nyquist]
            sos = butter(2, wn, btype='bandpass', output='sos')

        # Zero-phase filtering – cast to float64 to prevent subnormal
        # float32 arithmetic in scipy's IIR state variables.
        band_data = sosfiltfilt(sos, data_lfe.astype(np.float64, copy=False))
        band_ms = _win_ms(band_data)
        del band_data

        # Per-band activity: among globally-active windows, how many also
        # exceed the threshold in this band?  Uses the same window set as
        # P95 / Peak / energy statistics so all columns are consistent.
        if n_global_active > 0:
            band_rms_active = np.sqrt(band_ms[global_mask])
            act_pct = 100.0 * float(np.sum(band_rms_active >= threshold_rms)) / n_global_active
        else:
            act_pct = 0.0

        # Level statistics over globally-active windows only.
        if n_global_active > 0:
            active_ms = band_ms[global_mask]
            active_db = 20.0 * np.log10(np.maximum(np.sqrt(active_ms), _EPS))
            p95 = float(np.percentile(active_db, 95))
            peak = float(np.max(active_db))
            avg_ms = float(np.mean(active_ms))
        else:
            p95 = peak = avg_ms = float("nan")

        nan = float("nan")
        band_results.append({
            "label":       label,
            "activity_pct": act_pct,
            "p95_dBFS":    p95,
            "p95_rel":     (p95 - integrated_main) if not np.isnan(p95) else nan,
            "peak_dBFS":   peak,
            "peak_rel":    (peak - integrated_main) if not np.isnan(peak) else nan,
            "spread_db":   (peak - p95) if not np.isnan(peak) else nan,
        })
        avg_ms_per_band.append(avg_ms if not np.isnan(avg_ms) else 0.0)

    # ---- Cross-band ratios (energy over globally-active windows) ----

    e_infra, e_sub, e_bass, e_upper = avg_ms_per_band

    e_20_120 = e_sub + e_bass + e_upper
    sub_bass_ratio = (10.0 * np.log10(e_sub / (e_bass + e_upper))
                      if (e_bass + e_upper) > _EPS and e_sub > _EPS
                      else float("nan"))
    infra_ratio = (10.0 * np.log10(e_infra / e_20_120)
                   if e_20_120 > _EPS and e_infra > _EPS
                   else float("nan"))

    # Spectral centroid: energy-weighted mean of geometric-band centres
    # over the audible LFE range (20–120 Hz only).
    centers = [b[3] for b in _LFE_BANDS[1:]]   # 28.3, 56.6, 98.0
    energies = [e_sub, e_bass, e_upper]
    total_e = sum(energies)
    centroid = (sum(c * e for c, e in zip(centers, energies)) / total_e
                if total_e > _EPS else float("nan"))

    return {
        "bands":                band_results,
        "threshold_dBFS":       threshold_dBFS,
        "global_activity_pct":  global_activity_pct,
        "n_global_active":      n_global_active,
        "n_windows":            n_windows,
        "sub_bass_ratio_db":    sub_bass_ratio,
        "infra_ratio_db":       infra_ratio,
        "spectral_centroid_hz": centroid,
    }


def _surround_highpass(data_ch, sr):
    """Apply a zero-phase Butterworth high-pass filter at _SURROUND_HIGHPASS_HZ.

    Parameters
        data_ch  Single channel audio data (1D float array).
        sr       Sample rate in Hz.

    Returns
        Filtered copy of data_ch.
    """
    nyquist = sr / 2.0
    if _SURROUND_HIGHPASS_HZ >= nyquist:
        return data_ch

    sos = butter(_SURROUND_HIGHPASS_ORDER, _SURROUND_HIGHPASS_HZ / nyquist,
                 btype='high', output='sos')
    return sosfiltfilt(sos, data_ch)


def _surround_rms_relative_to_center(data, sr, effective_layout,
                                      lfe_filtered=None):
    """Measure all channel RMS levels relative to the center channel.

    Filter applied per channel type:
        L / R              : unfiltered
        LFE                : low-pass at _LFE_LOWPASS_HZ (uses lfe_filtered
                             if supplied to avoid redundant computation)
        Ls / Rs / Rc / Lrs / Rrs : high-pass at _SURROUND_HIGHPASS_HZ

    Surround high-pass uses a single forward pass (sosfilt) instead of the
    zero-phase forward-backward pass (sosfiltfilt).  For RMS-only metrics the
    startup transient is negligible (< 1000 samples vs. tens of millions) and
    the speed gain is roughly 2x per channel.

    The center channel (index 2) is the unfiltered reference.

    Channel mapping per layout (0-based):
        all    : L = 0, R = 1, C = 2 (reference)
        >= 5.1 : LFE = 3
        5.1    : Ls = 4, Rs = 5
        6.1    : Rc = 4, Ls = 5, Rs = 6
        7.1    : Lrs = 4, Rrs = 5, Ls = 6, Rs = 7

    Parameters
        data              Full audio array of shape (n_samples, n_channels).
        sr                Sample rate in Hz.
        effective_layout  Resolved layout string: "5.1", "6.1", "7.1", or
                          "auto".
        lfe_filtered      Optional pre-filtered LFE channel (1D array).
                          When supplied the LFE low-pass step is skipped.

    Returns
        Tuple (center_dbfs, results) where results is a list of
        (label, rms_dbfs, rel_db) ordered by channel index, or
        (nan, []) if the center channel is not present.
    """
    n_ch = data.shape[1]
    center_idx = 2

    if center_idx >= n_ch:
        return float("nan"), []

    # channel_map entries: (0-based index, display label, filter)
    # filter is one of: 'none' | 'lowpass' | 'highpass'
    channel_map = [
        (0, "L  ", "none"),
        (1, "R  ", "none"),
    ]

    if n_ch >= 4:
        channel_map.append((3, "LFE", "lowpass"))

    if effective_layout == "7.1":
        channel_map += [
            (4, "Lrs ", "highpass"),
            (5, "Rrs ", "highpass"),
            (6, "Ls", "highpass"),
            (7, "Rs", "highpass"),
        ]
    elif effective_layout == "6.1":
        channel_map += [
            (4, "Rc ", "highpass"),
            (5, "Ls ", "highpass"),
            (6, "Rs ", "highpass"),
        ]
    elif n_ch >= 6:
        channel_map += [
            (4, "Ls ", "highpass"),
            (5, "Rs ", "highpass"),
        ]

    channel_map = [(i, lbl, flt) for i, lbl, flt in channel_map if i < n_ch]

    # Pre-compute the high-pass SOS once for all surround channels.
    nyquist = sr / 2.0
    hp_sos = None
    if any(flt == "highpass" for _, _, flt in channel_map):
        if _SURROUND_HIGHPASS_HZ < nyquist:
            hp_sos = butter(_SURROUND_HIGHPASS_ORDER,
                            _SURROUND_HIGHPASS_HZ / nyquist,
                            btype='high', output='sos')

    center_rms = np.sqrt(np.mean(np.square(data[:, center_idx])))
    center_dbfs = 20.0 * np.log10(max(float(center_rms), _EPS))

    results = []
    for ch_idx, label, flt in channel_map:
        if flt == "lowpass":
            # Re-use externally filtered LFE when available.
            ch_data = (lfe_filtered if lfe_filtered is not None
                       else _lfe_lowpass(data[:, ch_idx], sr))
        elif flt == "highpass":
            # Single forward pass is sufficient for RMS (~2x faster than
            # zero-phase sosfiltfilt; startup transient is negligible).
            ch_data = (sosfilt(hp_sos, data[:, ch_idx])
                       if hp_sos is not None else data[:, ch_idx])
        else:
            ch_data = data[:, ch_idx]
        rms = np.sqrt(np.mean(np.square(ch_data)))
        rms_dbfs = 20.0 * np.log10(max(float(rms), _EPS))
        results.append((label, rms_dbfs, rms_dbfs - center_dbfs))

    return center_dbfs, results


def analyze(path, layout=None, lfe_channel=None, per_channel=False,
            exclude_surround=False, debug=False):
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
    _t0 = time.perf_counter()
    _timings: list[tuple[str, float]] = []

    # _t0_box is a one-element list so that _tick can update the reference
    # time via mutation (Python closures cannot rebind a nonlocal variable
    # without the nonlocal keyword, which would be less readable here).
    _t0_box = [_t0]

    def _tick(label: str) -> None:
        """Append (label, elapsed_since_last_tick) to _timings."""
        now = time.perf_counter()
        _timings.append((label, now - _t0_box[0]))
        _t0_box[0] = now

    data, sr = sf.read(path, dtype="float32", always_2d=True)
    n_ch = data.shape[1]
    duration_s = len(data) / sr
    _tick("File read")

    # Resolve effective layout once so it can be reused throughout.
    if layout is not None:
        effective_layout = layout
    elif n_ch == 6:
        effective_layout = "5.1"
    elif n_ch == 7:
        effective_layout = "6.1"
    elif n_ch >= 8:
        effective_layout = "7.1"
    else:
        effective_layout = "auto"

    print(f"File         : {path}")
    print(f"Sample rate  : {sr} Hz")
    print(f"Channels     : {n_ch}")
    print(f"Duration     : {duration_s:.1f} s")
    if sr > _LOUDNESS_SR:
        print(f"Loudness SR  : {_LOUDNESS_SR} Hz  "
              f"(downsampled from {sr} Hz for loudness / RMS / LFE analysis)")
    print()

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

    # Resolve LFE channel index (0-based) early so it can be used before and
    # after the downsampling step.
    lfe_idx = None
    if lfe_ch:
        lfe_idx = lfe_ch[0] - 1   # convert 1-based to 0-based
    elif lfe_channel is not None and 0 <= lfe_channel < n_ch:
        lfe_idx = lfe_channel

    # -----------------------------------------------------------------------
    # Downsample to _LOUDNESS_SR and release the full-resolution array.
    # All metrics are computed from the compact representation.
    # -----------------------------------------------------------------------
    data_ds, sr_ds = _downsample(data, sr, _LOUDNESS_SR)
    del data
    gc.collect()
    _tick(f"Downsample {sr} → {sr_ds} Hz")

    # -----------------------------------------------------------------------
    # Frequency-response for plotting.
    # Use the downsampled data so the plot can be generated without retaining the full-resolution audio.
    # -----------------------------------------------------------------------
    freq_audio: dict = {}
    left_col = 0
    center_col = 2 if n_ch > 2 else 0
    freq_audio["left"] = data_ds[:, left_col].copy()
    freq_audio["center"] = data_ds[:, center_col].copy()
    if lfe_idx is not None and lfe_idx < data_ds.shape[1]:
        freq_audio["lfe"] = data_ds[:, lfe_idx].copy()
    freq_audio["sr"] = sr_ds

    # -----------------------------------------------------------------------
    # K-weighted loudness (all at _LOUDNESS_SR)
    # -----------------------------------------------------------------------
    weighted = _k_weight(data_ds, sr_ds)
    _tick("K-weighting")

    # 400 ms momentary blocks (75% overlap) drive the integrated loudness.
    z_400 = _block_mean_square(weighted, sr_ds, win_s=0.4, step_s=0.1)
    integrated = _integrated_loudness(z_400, weights)
    momentary = _loudness_from_z(z_400, weights) if z_400.shape[0] else \
        np.array([])
    _tick("Block mean square 400 ms + integrated loudness")

    # 3 s short-term blocks drive the LRA and the time-series plot.
    z_3s = _block_mean_square(weighted, sr_ds, win_s=3.0, step_s=0.1)
    short_term = _loudness_from_z(z_3s, weights) if z_3s.shape[0] else \
        np.array([])
    lra = _loudness_range(short_term)

    _tick("Block mean square 3 s + LRA")

    del weighted
    gc.collect()

    rms = _rms_dbfs(data_ds)
    _tick("RMS")

    print("=== Dynamic Range / Loudness ===")
    print(f"  Integrated loudness : {integrated:8.1f} LUFS")
    print(f"  Loudness range (LRA): {lra:8.1f} LU"
          f"  ({_lra_label(lra)})" if not np.isnan(lra) else
          f"  Loudness range (LRA): {'n/a':>8}")
    print(f"  RMS level           : {rms:8.1f} dBFS")
    if momentary.size:
        print(f"  Momentary max       : {np.max(momentary):8.1f} LUFS")
    if short_term.size:
        print(f"  Short-term max      : {np.max(short_term):8.1f} LUFS")
        print(f"  Short-term min      : "
              f"{np.min(short_term[short_term > _ABS_GATE_LUFS]):8.1f} LUFS")
    print()

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
            print(f"  Channel {ch + 1:2d}: {ch_int:8.1f} LUFS{tag}")

    # -----------------------------------------------------------------------
    # LFE channel analysis (at _LFE_SR – much cheaper than _LOUDNESS_SR)
    # -----------------------------------------------------------------------
    # Only the LFE channel is downsampled to _LFE_SR.  The lowpass cut-off is
    # 120 Hz, well below the _LFE_SR Nyquist (2 kHz), so filtering at _LFE_SR
    # produces identical results while operating on an array 4× shorter.
    # Surround and other channels are intentionally kept at _LOUDNESS_SR so
    # that no implicit lowpass distorts the relative RMS comparison.
    lfe_band_result = None
    lfe_data_lfe_sr = None  # kept alive until after _surround_rms_relative_to_center

    if lfe_idx is not None:
        lfe_raw_lfe_sr, sr_lfe = _downsample(
            data_ds[:, lfe_idx].reshape(-1, 1), sr_ds, _LFE_SR)
        lfe_raw_lfe_sr = lfe_raw_lfe_sr[:, 0]
        lfe_data_lfe_sr = _lfe_lowpass(lfe_raw_lfe_sr, sr_lfe)
        del lfe_raw_lfe_sr
        lfe_band_result = _lfe_band_analysis(lfe_data_lfe_sr, sr_lfe, integrated)
        # Short-term RMS curve for the LFE plot strip (3 s window, 0.1 s hop).
        lfe_2d = lfe_data_lfe_sr.reshape(-1, 1)
        lfe_ms = _block_mean_square(lfe_2d, sr_lfe, win_s=3.0, step_s=0.1)
        lfe_rms_db = 10.0 * np.log10(np.maximum(lfe_ms[:, 0], 1e-20))
    else:
        lfe_rms_db = None
    _tick("LFE band analysis")

    if lfe_band_result is not None:
        ba = lfe_band_result
        ref_str = (f"{integrated:.1f} LUFS" if not np.isnan(integrated)
                   else "n/a")
        print(f"\n=== LFE Band Analysis (rel. to {ref_str} main mix) ===")
        print(f"  Activity threshold  : {ba['threshold_dBFS']:.1f} dBFS "
              f"(integrated − {_LFE_ACTIVITY_OFFSET_DB:.0f} dB)")
        print(f"  LFE active (total)  : {ba['global_activity_pct']:.1f} % of runtime")
        print(f"  Band activity below : % of LFE-active windows where band exceeds threshold")
        print()
        hdr = (f"  {'Band':<12}  {'Act. in active':>14}  "
               f"{'P95':>9}  {'Peak':>9}  {'Peak−P95':>9}")
        print(hdr)
        print(f"  {'-'*12}  {'-'*14}  {'-'*9}  {'-'*9}  {'-'*9}")
        for b in ba["bands"]:
            act = f"{b['activity_pct']:.1f} %"
            p95 = (f"{b['p95_rel']:+.1f} dB"
                   if not np.isnan(b["p95_rel"]) else "  n/a")
            peak = (f"{b['peak_rel']:+.1f} dB"
                    if not np.isnan(b["peak_rel"]) else "  n/a")
            spread = (f"{b['spread_db']:.1f} dB"
                      if not np.isnan(b["spread_db"]) else "  n/a")
            warn = ""
            if (b["label"] == "<20 Hz"
                    and b["activity_pct"] > 10.0
                    and not np.isnan(ba["infra_ratio_db"])
                    and ba["infra_ratio_db"] > -20.0):
                warn = "  [WARN]"
            print(f"  {b['label']:<12}  {act:>14}  {p95:>9}  {peak:>9}"
                  f"  {spread:>9}{warn}")
        print()
        if not np.isnan(ba["sub_bass_ratio_db"]):
            r = ba["sub_bass_ratio_db"]
            print(f"  Sub-bass ratio  (20–40 / 40–120 Hz): {r:+.1f} dB")
        if not np.isnan(ba["infra_ratio_db"]):
            r = ba["infra_ratio_db"]
            note = ("[WARN] significant infrasound" if r > -20 else
                    "[INFO] notable infrasound" if r > -30 else "[OK]")
            print(f"  Infrasound ratio (<20 / 20–120 Hz) : {r:+.1f} dB"
                  f"  {note}")
        if not np.isnan(ba["spectral_centroid_hz"]):
            print(f"  Spectral centroid (active windows) : "
                  f"{ba['spectral_centroid_hz']:.0f} Hz")
        bass_lbl = (_sub_bass_ratio_label(ba["sub_bass_ratio_db"])
                    if not np.isnan(ba["sub_bass_ratio_db"]) else "n/a")
        print(f"\n  Summary:")
        print(f"    LFE activity   : {_lfe_activity_label(ba['global_activity_pct'])}")
        print(f"    LFE depth      : {bass_lbl}")

    # Surround channel RMS relative to center (at _LOUDNESS_SR).
    # L, R, Center and surround channels are measured from the full 16 kHz
    # representation so that no implicit low-pass is applied to them.
    # The pre-filtered LFE signal (at _LFE_SR) is passed separately; its RMS
    # is sample-rate-independent after the 120 Hz low-pass.
    center_dbfs, surround_results = _surround_rms_relative_to_center(
        data_ds, sr_ds, effective_layout,
        lfe_filtered=lfe_data_lfe_sr)
    del lfe_data_lfe_sr
    _tick("Surround RMS relative to Center")

    if surround_results:
        print("\n=== Channel RMS relative to Center ===")
        print(f"  Low-pass  (LFE)     : {_LFE_LOWPASS_HZ:.0f} Hz "
              f"(Butterworth order {_LFE_LOWPASS_ORDER}, zero-phase)")
        print(f"  High-pass (surround): {_SURROUND_HIGHPASS_HZ:.0f} Hz "
              f"(Butterworth order {_SURROUND_HIGHPASS_ORDER}, single-pass)")
        print(f"  C (reference)       : {center_dbfs:8.1f} dBFS")
        for label, rms_dbfs, rel_db in surround_results:
            print(f"  {label:<20}: {rel_db:+8.1f} dB")

    # -----------------------------------------------------------------------
    # Pipeline timing summary (only with --debug)
    # -----------------------------------------------------------------------
    if debug:
        total_s = sum(t for _, t in _timings)
        col = max(len(lbl) for lbl, _ in _timings)
        print("\n=== Pipeline Timing ===")
        for lbl, t in _timings:
            bar_len = max(1, int(round(t / total_s * 40)))
            bar = "█" * bar_len
            print(f"  {lbl:<{col}}  {t:7.3f} s  {bar}")
        print(f"  {'─' * col}  {'─' * 7}")
        print(f"  {'Total':<{col}}  {total_s:7.3f} s")

    return {
        "sr": sr,
        "n_channels": n_ch,
        "integrated_lufs": integrated,
        "lra_lu": lra,
        "rms_dbfs": rms,
        "short_term": short_term,
        "momentary": momentary,
        "step_s": 0.1,
        "lfe_band_analysis": lfe_band_result,
        "lfe_rms_db": lfe_rms_db,
        "center_rms_dbfs": center_dbfs,
        "surround_rms": surround_results,
        # Compact excerpt for the frequency-response plot.  The full-resolution
        # audio is no longer retained after the downsample step above.
        "freq_audio": freq_audio,
    }


def _plot(result, out_path):
    """Render loudness analysis: time-series, histogram, metrics panel, and
    frequency response."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.gridspec import GridSpec
    except ImportError:
        sys.exit("[ERR] --plot requires matplotlib. Install with "
                 "'pip install matplotlib'.")

    short_term = result["short_term"]
    if short_term.size == 0:
        sys.exit("[ERR] Not enough audio to plot a short-term loudness curve.")

    # Short-term windows are 3 s long, hopped by step_s; centre the curve.
    t = np.arange(short_term.size) * result["step_s"] + 1.5

    # Layout:  row 0 – loudness curve (full width)
    #          row 1 – LFE activity strip (full width, shared x with row 0)
    #          row 2-4 – histogram (left) | metrics panels (right)
    #          row 5 – frequency response (full width)
    fig = plt.figure(figsize=(12, 16.97), constrained_layout=True)
    gs = GridSpec(6, 2, figure=fig,
                  height_ratios=[1.3, 0.18, 0.33, 0.33, 0.33, 2.0],
                  width_ratios=[4.5, 1])
    ax1     = fig.add_subplot(gs[0, :])       # loudness over time  – full width
    ax_lfe_strip = fig.add_subplot(gs[1, :], sharex=ax1)  # LFE strip – full width
    ax2     = fig.add_subplot(gs[2:5, 0])     # histogram           – left, 3 rows tall
    ax4_rel = fig.add_subplot(gs[2, 1])       # channel RMS         – top right
    ax4_lfe = fig.add_subplot(gs[3, 1])       # LFE metrics         – middle right
    ax4_sum = fig.add_subplot(gs[4, 1])       # summary             – bottom right
    ax3     = fig.add_subplot(gs[5, :])

    # ===== Subplot 1: Loudness over time =====
    ax1.plot(t, short_term, lw=0.8, color="#1f77b4", label="Short-term loudness")
    ax1.axhline(result["integrated_lufs"], color="#d62728", ls="--", lw=1.0,
                label=f"Integrated {result['integrated_lufs']:.1f} LUFS")

    # LRA bounds: apply the same double gate as _loudness_range()
    if not np.isnan(result["lra_lu"]):
        lv_abs1 = short_term[short_term > _ABS_GATE_LUFS]
        if lv_abs1.size > 0:
            rel_gate1 = np.mean(lv_abs1) - _LRA_REL_GATE_LU
            lv_dg = lv_abs1[lv_abs1 >= rel_gate1]
            if lv_dg.size > 0:
                p10 = np.percentile(lv_dg, 10)
                p95 = np.percentile(lv_dg, 95)
                ax1.axhline(p95, color="#ff7f0e", ls=":", lw=1.0, alpha=0.7,
                            label=f"LRA bounds (p10: {p10:.1f}, p95: {p95:.1f})")
                ax1.axhline(p10, color="#ff7f0e", ls=":", lw=1.0, alpha=0.7)
                ax1.fill_between(t, p10, p95, alpha=0.1, color="#ff7f0e")

    ax1.set_xlabel("")
    st_valid = short_term[short_term > _ABS_GATE_LUFS]
    y_top = math.ceil((np.max(st_valid) if st_valid.size else -5) / 5.0) * 5.0
    ax1.set_ylim(y_top - 50, y_top)
    ax1.set_ylabel("Loudness (LUFS)")
    ax1.set_title(f"Film loudness over time  -  LRA {result['lra_lu']:.1f} LU")
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="lower right", fontsize=9)
    ax1.tick_params(labelbottom=False)  # x labels on strip instead

    # ===== LFE activity strip =====
    lfe_rms_db = result.get("lfe_rms_db")
    if lfe_rms_db is not None and lfe_rms_db.size > 0:
        t_lfe = np.arange(lfe_rms_db.size) * result["step_s"] + 1.5
        # Clip to a sensible display range (−60 to 0 dBFS).
        lfe_disp = np.clip(lfe_rms_db, -60.0, 0.0)
        # Activity threshold from band analysis (if available).
        ba = result.get("lfe_band_analysis")
        thr = ba["threshold_dBFS"] if ba is not None else -999.0
        active = lfe_rms_db >= thr
        ax_lfe_strip.fill_between(t_lfe, -60.0, lfe_disp,
                                  where=active,
                                  color="#d62728", alpha=0.7, linewidth=0)
        ax_lfe_strip.fill_between(t_lfe, -60.0, lfe_disp,
                                  where=~active,
                                  color="#888888", alpha=0.4, linewidth=0)
        ax_lfe_strip.set_ylim(-60, 0)
        ax_lfe_strip.set_ylabel("LFE\n(dBFS)", fontsize=7, labelpad=2)
        ax_lfe_strip.yaxis.set_tick_params(labelsize=6)
        ax_lfe_strip.set_yticks([-60, -30, 0])
        ax_lfe_strip.grid(True, alpha=0.2)
        ax_lfe_strip.set_xlabel("Time (s)")
    else:
        ax_lfe_strip.axis('off')
        ax_lfe_strip.set_xlabel("Time (s)")
    # Apply the same two-stage gate as _loudness_range() / EBU Tech 3342:
    #   stage 1 – absolute gate at -70 LUFS
    #   stage 2 – relative gate at -20 LU below the mean of stage-1 values
    lv_abs = short_term[short_term > _ABS_GATE_LUFS]

    if lv_abs.size > 0:
        rel_gate = np.mean(lv_abs) - _LRA_REL_GATE_LU
        lv_gated = lv_abs[lv_abs >= rel_gate]

        # p10 / p95 of the double-gated set — identical to _loudness_range()
        p10 = np.percentile(lv_gated, 10)
        p95 = np.percentile(lv_gated, 95)

        # Dynamic X-axis: always 50 dB wide, centred on the LRA band midpoint
        # (p10/p95), snapped to the nearest 5 dB boundary so that both edges
        # always fall on a 5 dB multiple.
        lra_center = (p10 + p95) / 2.0
        center_5 = round(lra_center / 5.0) * 5
        x_lo = center_5 - 25
        x_hi = center_5 + 25

        bins = np.arange(x_lo, x_hi + 1, 1)
        w_abs   = np.full(len(lv_abs),   100.0 / len(lv_abs))
        w_gated = np.full(len(lv_gated), 100.0 / len(lv_abs))
        ax2.hist(lv_abs, bins=bins, weights=w_abs, color="#1f77b4", alpha=0.4,
                 edgecolor="none", label="Absolute-gated")
        ax2.hist(lv_gated, bins=bins, weights=w_gated, color="#1f77b4", alpha=0.8,
                 edgecolor="black", linewidth=0.4, label="Double-gated (LRA)")

        ax2.axvline(result["integrated_lufs"], color="#d62728", ls=":", lw=2.0,
                    label=f"Integrated {result['integrated_lufs']:.1f} LUFS")

        ax2.axvline(p10, color="#ff7f0e", ls="--", lw=2.0,
                    label=f"p10  {p10:.1f} LUFS")
        ax2.axvline(p95, color="#2ca02c", ls="--", lw=2.0,
                    label=f"p95  {p95:.1f} LUFS")

        ax2.axvline(_ABS_GATE_LUFS, color="#888888", ls="-", lw=1.0,
                    alpha=0.5, label=f"Abs. gate {_ABS_GATE_LUFS:.0f} LUFS")
        ax2.axvline(rel_gate, color="#aaaaaa", ls=":", lw=1.0,
                    alpha=0.7, label=f"Rel. gate {rel_gate:.1f} LUFS")

        ax2.set_xlabel("Loudness (LUFS)")
        ax2.set_ylabel("Proportion (%)")
        ax2.set_title(f"Loudness Range Distribution  –  LRA {result['lra_lu']:.1f} LU")
        ax2.grid(True, alpha=0.3, axis="y")
        ax2.legend(loc="upper right", fontsize=9)
        ax2.set_xlim(x_lo, x_hi)

    # ===== Subplot 3: Frequency Response =====
    # freq_audio contains a compact excerpt extracted at _LOUDNESS_SR so that
    # the full-resolution audio does not need to be kept alive for the plot.
    freq_audio = result.get("freq_audio", {})
    freq_sr = freq_audio.get("sr", result["sr"])

    if "left" in freq_audio:
        freqs_c, mag_c = _frequency_response(freq_audio["left"], freq_sr)
        ax3.plot(freqs_c, mag_c, lw=1.0, color="#1fb477", label="Left")

    if "center" in freq_audio:
        freqs_c, mag_c = _frequency_response(freq_audio["center"], freq_sr)
        ax3.plot(freqs_c, mag_c, lw=1.0, color="#1f77b4", label="Center")

    if "lfe" in freq_audio:
        freqs_l, mag_l = _frequency_response(freq_audio["lfe"], freq_sr)
        ax3.plot(freqs_l, mag_l, lw=1.0, color="#d62728", label="LFE")

    ax3.set_xscale('log')
    ax3.set_xlim(_FREQ_RESPONSE_F_MIN, _FREQ_RESPONSE_F_MAX)
    ax3.set_ylim(-50, 2)

    freq_ticks = [2, 5, 10, 20, 50, 100, 200]
    ax3.set_xticks(freq_ticks)
    ax3.set_xticklabels([str(f) for f in freq_ticks])

    ax3.set_xlabel("Frequency (Hz)")
    ax3.set_ylabel("Magnitude (dB, normalized)")
    ax3.set_title("Frequency Response "
                  f"(1/24 oct)")
    ax3.grid(True, alpha=0.3, which='both')
    ax3.legend(loc="lower left", fontsize=9)

    # ===== Metrics panels: LFE (top right) + channel RMS (bottom right) =====
    def _fv(val, fmt):
        """Format val or return 'N/A' when NaN."""
        return "N/A" if (isinstance(val, float) and np.isnan(val)) \
            else fmt % val

    # --- LFE panel ---
    ba = result.get("lfe_band_analysis")
    lfe_lines = ["LFE Band Analysis"]
    if ba is not None:
        lfe_lines.append(
            f"  Active {ba['global_activity_pct']:2.1f} % of runtime")
        lfe_lines.append(f"  {'Band':<12} {'Act%':>5} {'P95':>6} {'Peak':>6}")
        for b in ba["bands"]:
            p95s = (f"{b['p95_rel']:+5.1f}" if not np.isnan(b["p95_rel"])
                    else "  n/a")
            pks = (f"{b['peak_rel']:+5.1f}" if not np.isnan(b["peak_rel"])
                   else "  n/a")
            lfe_lines.append(
                f"  {b['label']:<12} {b['activity_pct']:5.1f} {p95s} {pks}")
        if not np.isnan(ba["sub_bass_ratio_db"]):
            r = ba["sub_bass_ratio_db"]
            lfe_lines.append(
                f"  Sub-bass ratio {r:+.1f} dB")
        if not np.isnan(ba.get("spectral_centroid_hz", float("nan"))):
            lfe_lines.append(
                f"  Centroid    {ba['spectral_centroid_hz']:.0f} Hz")
    else:
        lfe_lines.append("  No LFE channel detected")

    ax4_lfe.axis('off')
    ax4_lfe.text(0.03, 0.97, "\n".join(lfe_lines),
                 transform=ax4_lfe.transAxes, fontsize=8.5,
                 fontfamily="monospace", va="top", ha="left",
                 bbox=dict(boxstyle="round,pad=0.5", facecolor="#ddeeff",
                           edgecolor="#3366aa", alpha=0.85))

    # --- Summary panel ---
    lra = result["lra_lu"]
    ba  = result.get("lfe_band_analysis")
    lra_lbl  = _lra_label(lra) if not np.isnan(lra) else "n/a"
    act_lbl  = (_lfe_activity_label(ba["global_activity_pct"])
                if ba is not None else "n/a")
    bass_lbl = (_sub_bass_ratio_label(ba["sub_bass_ratio_db"])
                if ba is not None and not np.isnan(ba["sub_bass_ratio_db"])
                else "n/a")
    sum_lines = [
        "Summary",
        f'  Loudness range: {lra_lbl}',
        f'  LFE activity  : {act_lbl}',
        f'  LFE depth     : {bass_lbl}',
    ]
    ax4_sum.axis('off')
    ax4_sum.text(0.03, 0.97, "\n".join(sum_lines),
                 transform=ax4_sum.transAxes, fontsize=8.5,
                 fontfamily="monospace", va="top", ha="left",
                 bbox=dict(boxstyle="round,pad=0.5", facecolor="#fff8dd",
                           edgecolor="#aa8800", alpha=0.85))

    # --- Channel RMS panel ---
    surround_results = result["surround_rms"]
    center_dbfs = result["center_rms_dbfs"]
    rel_lines = ["Channel RMS relative to Center"]
    if not (isinstance(center_dbfs, float) and np.isnan(center_dbfs)):
        rel_lines.append(f"  C (ref): {center_dbfs:+8.1f} dBFS")
        for label, _rms_dbfs, rel_db in surround_results:
            rel_lines.append(f"  {label:<7}: {rel_db:+8.1f} dB")

    ax4_rel.axis('off')
    ax4_rel.text(0.03, 0.97, "\n".join(rel_lines),
                 transform=ax4_rel.transAxes, fontsize=8.5,
                 fontfamily="monospace", va="top", ha="left",
                 bbox=dict(boxstyle="round,pad=0.5", facecolor="#ddf0dd",
                           edgecolor="#2d7a2d", alpha=0.85))

    fig.savefig(out_path, dpi=120)
    print(f"\n  [PLOT] {out_path}")



def main():
    ap = argparse.ArgumentParser(
        description="Analyse the dynamic range and loudness of a film audio "
                    "file (ITU-R BS.1770-4 / EBU R128).")
    ap.add_argument("audio", nargs="+",
                    help="Path(s) to audio files; glob patterns are supported "
                         "(e.g. *.wav, /path/to/reels/*.wav)")
    ap.add_argument("--layout", choices=["mono", "stereo", "5.1", "6.1", "7.1"],
                    default=None,
                    help="Channel layout override (default: auto-detect)")
    ap.add_argument("--lfe-channel", type=int, default=None, metavar="N",
                    help="0-based index of the LFE channel to exclude")
    ap.add_argument("--per-channel", action="store_true",
                    help="Also report loudness and true peak per channel")
    ap.add_argument("--exclude-surround", action="store_true",
                    help="Exclude the surround channels from the analysis "
                         "(front/dialogue-only loudness)")
    ap.add_argument("--plot", nargs="?", const="AUTO", default="AUTO",
                    metavar="FILE",
                    help="Path for the output PNG (default: input filename "
                         "with .png extension); pass an empty string or "
                         "--no-plot to suppress the plot")
    ap.add_argument("--no-plot", action="store_true",
                    help="Suppress the plot even when no --plot FILE is given")
    ap.add_argument("--skip-existing", action="store_true",
                    help="Skip files whose plot PNG already exists on disk")
    ap.add_argument("--debug", action="store_true",
                    help="Print pipeline timing for each analysis step")
    args = ap.parse_args()

    # Expand glob patterns; preserve order and deduplicate.
    paths = []
    seen = set()
    for pattern in args.audio:
        matches = sorted(glob.glob(pattern))
        if not matches:
            print(f"[WARN] No files match: {pattern}", file=sys.stderr)
        for p in matches:
            if p not in seen:
                seen.add(p)
                paths.append(p)

    if not paths:
        sys.exit("[ERR] No input files found.")

    fixed_plot_path = (None if args.plot == "AUTO" else args.plot)
    if fixed_plot_path and len(paths) > 1:
        print("[WARN] A fixed --plot path cannot be used with multiple input "
              "files; each file will produce its own .png.", file=sys.stderr)
        fixed_plot_path = None

    for i, path in enumerate(paths):
        plot_path = (fixed_plot_path
                     if fixed_plot_path
                     else os.path.splitext(path)[0] + ".png")

        if args.skip_existing and os.path.exists(plot_path):
            print(f"[SKIP] Plot already exists, skipping: {path}")
            continue

        if len(paths) > 1:
            print(f"\n{'=' * 64}")
            print(f"  File {i + 1}/{len(paths)}: {path}")
            print(f"{'=' * 64}\n")

        result = analyze(path, layout=args.layout,
                         lfe_channel=args.lfe_channel,
                         per_channel=args.per_channel,
                         exclude_surround=args.exclude_surround,
                         debug=args.debug)

        if args.plot is not None and not args.no_plot:
            _plot(result, plot_path)


if __name__ == "__main__":
    main()
