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
        5.1    : [L, R, C, LFE, Ls, Rs]
        6.1    : [L, R, C, LFE, Ls, Rs, Rc]
        7.1    : [L, R, C, LFE, Ls, Rs, Lrs, Rrs]
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
import glob
import math
import os
import sys

import numpy as np
import soundfile as sf
from scipy.interpolate import PchipInterpolator
from scipy.signal import butter, lfilter, resample_poly, sosfiltfilt, welch


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

    # Standard SMPTE/ITU order: L R C LFE Ls Rs [Rc] [Lrs Rrs].
    if layout == "5.1" and n_ch >= 6:
        weights[3] = 0.0                 # LFE excluded
        weights[4] = surround            # Ls
        weights[5] = surround            # Rs
    elif layout == "6.1" and n_ch >= 7:
        weights[3] = 0.0                 # LFE excluded
        weights[4] = surround            # Ls
        weights[5] = surround            # Rs
        weights[6] = surround            # Rc (Rear Center)
    elif layout == "7.1" and n_ch >= 8:
        weights[3] = 0.0                 # LFE excluded
        weights[4] = surround            # Ls
        weights[5] = surround            # Rs
        weights[6] = surround            # Lrs
        weights[7] = surround            # Rrs
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


# DC offset above this linear threshold triggers a [WARN].
_DC_OFFSET_WARN_LINEAR = 1e-4   # ≈ −80 dBFS


def _dc_offset_per_channel(data):
    """Compute the DC offset of every channel.

    The DC offset is the arithmetic mean of all samples and represents a
    constant bias in the signal.  Non-zero DC causes audible clicks at edit
    points and can saturate output stages.

    Parameters
        data  Audio array of shape (n_samples, n_channels).

    Returns
        List of (channel_index, dc_linear, dc_dbfs) tuples, one per channel.
        dc_linear is signed; dc_dbfs is computed from the absolute value.
    """
    results = []
    for ch in range(data.shape[1]):
        dc = float(np.mean(data[:, ch]))
        dc_dbfs = 20.0 * np.log10(max(abs(dc), _EPS))
        results.append((ch, dc, dc_dbfs))
    return results



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


def _lfe_loudness(data_lfe, sr):
    """Measure LFE channel loudness separately (LUFS).

    LFE uses standard K-weighting but measured independently without
    the surround channel weighting applied.

    Parameters
        data_lfe  LFE channel data (1D array).
        sr        Sample rate in Hz.

    Returns
        LFE integrated loudness in LUFS, or NaN if insufficient data.
    """
    if data_lfe.ndim == 1:
        data_lfe = data_lfe[:, np.newaxis]

    weighted = _k_weight(data_lfe, sr)
    z_400 = _block_mean_square(weighted, sr, win_s=0.4, step_s=0.1)

    if z_400.shape[0] == 0:
        return float("nan")

    # LFE has weight 1.0 (no special weighting)
    lfe_weight = np.array([1.0])
    return _integrated_loudness(z_400, lfe_weight)


def _lfe_rms_dbfs(data_lfe):
    """RMS level of LFE channel in dBFS."""
    rms = np.sqrt(np.mean(np.square(data_lfe)))
    return 20.0 * np.log10(max(float(rms), _EPS))


def _lfe_true_peak_dbtp(data_lfe, sr):
    """True peak level of LFE channel via 4x oversampling."""
    factor = 4 if sr <= 96000 else 2
    oversampled = resample_poly(data_lfe, factor, 1, axis=0)
    peak = np.max(np.abs(oversampled))
    return 20.0 * np.log10(max(float(peak), _EPS))


def _lfe_crest_factor(data_lfe):
    """Crest factor of LFE: Peak / RMS ratio in dB."""
    peak = np.max(np.abs(data_lfe))
    rms = np.sqrt(np.mean(np.square(data_lfe)))
    if rms < _EPS:
        return float("nan")
    return 20.0 * np.log10(max(float(peak / rms), _EPS))


def _lfe_activity(data_lfe, threshold_dbfs=-50):
    """Percentage of time LFE is above threshold.

    Parameters
        data_lfe        LFE channel data.
        threshold_dbfs  Activity threshold in dBFS.

    Returns
        Percentage of active samples (0-100).
    """
    threshold_linear = 10.0 ** (threshold_dbfs / 20.0)
    active_samples = np.sum(np.abs(data_lfe) > threshold_linear)
    activity_percent = 100.0 * active_samples / len(data_lfe)
    return activity_percent


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


def _surround_rms_relative_to_center(data, sr, effective_layout):
    """Measure all channel RMS levels relative to the center channel.

    Filter applied per channel type:
        L / R              : unfiltered
        LFE                : low-pass at _LFE_LOWPASS_HZ
        Ls / Rs / Rc / Lrs / Rrs : high-pass at _SURROUND_HIGHPASS_HZ

    The center channel (index 2) is the unfiltered reference.

    Channel mapping per layout (0-based):
        all    : L = 0, R = 1, C = 2 (reference)
        >= 5.1 : LFE = 3
        5.1    : Ls = 4, Rs = 5
        6.1    : Ls = 4, Rs = 5, Rc = 6
        7.1    : Ls = 4, Rs = 5, Lrs = 6, Rrs = 7

    Parameters
        data              Full audio array of shape (n_samples, n_channels).
        sr                Sample rate in Hz.
        effective_layout  Resolved layout string: "5.1", "6.1", "7.1", or
                          "auto".

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
        (0, "L    (Ch 1)", "none"),
        (1, "R    (Ch 2)", "none"),
    ]

    if n_ch >= 4:
        channel_map.append((3, "LFE  (Ch 4)", "lowpass"))

    if effective_layout == "7.1":
        channel_map += [
            (4, "Ls   (Ch 5)", "highpass"),
            (5, "Rs   (Ch 6)", "highpass"),
            (6, "Lrs  (Ch 7)", "highpass"),
            (7, "Rrs  (Ch 8)", "highpass"),
        ]
    elif effective_layout == "6.1":
        channel_map += [
            (4, "Ls   (Ch 5)", "highpass"),
            (5, "Rs   (Ch 6)", "highpass"),
            (6, "Rc   (Ch 7)", "highpass"),
        ]
    elif n_ch >= 6:
        channel_map += [
            (4, "Ls   (Ch 5)", "highpass"),
            (5, "Rs   (Ch 6)", "highpass"),
        ]

    channel_map = [(i, lbl, flt) for i, lbl, flt in channel_map if i < n_ch]

    center_rms = np.sqrt(np.mean(np.square(data[:, center_idx])))
    center_dbfs = 20.0 * np.log10(max(float(center_rms), _EPS))

    results = []
    for ch_idx, label, flt in channel_map:
        if flt == "lowpass":
            ch_data = _lfe_lowpass(data[:, ch_idx], sr)
        elif flt == "highpass":
            ch_data = _surround_highpass(data[:, ch_idx], sr)
        else:
            ch_data = data[:, ch_idx]
        rms = np.sqrt(np.mean(np.square(ch_data)))
        rms_dbfs = 20.0 * np.log10(max(float(rms), _EPS))
        results.append((label, rms_dbfs, rms_dbfs - center_dbfs))

    return center_dbfs, results


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
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    n_ch = data.shape[1]

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
    print(f"  Integrated loudness : {integrated:8.1f} LUFS")
    print(f"  Loudness range (LRA): {lra:8.1f} LU")
    print(f"  True peak           : {true_peak:8.1f} dBTP")
    print(f"  DR score            : {dr:8.0f}")
    print(f"  RMS level           : {rms:8.1f} dBFS")
    if momentary.size:
        print(f"  Momentary max       : {np.max(momentary):8.1f} LUFS")
    if short_term.size:
        print(f"  Short-term max      : {np.max(short_term):8.1f} LUFS")
        print(f"  Short-term min      : "
              f"{np.min(short_term[short_term > _ABS_GATE_LUFS]):8.1f} LUFS")
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
            print(f"  Channel {ch + 1:2d}: {ch_int:8.1f} LUFS  "
                  f"true peak {tp_per_ch[ch]:7.1f} dBTP{tag}")

    # LFE analysis if LFE channel is identified
    lfe_loudness = float("nan")
    lfe_peak = float("nan")
    lfe_rms = float("nan")
    lfe_crest = float("nan")
    lfe_activity = float("nan")

    lfe_idx = None
    if lfe_ch:
        lfe_idx = lfe_ch[0] - 1  # Convert from 1-based to 0-based index
    elif lfe_channel is not None and 0 <= lfe_channel < n_ch:
        lfe_idx = lfe_channel

    if lfe_idx is not None:
        lfe_data = _lfe_lowpass(data[:, lfe_idx], sr)
        lfe_loudness = _lfe_loudness(lfe_data, sr)
        lfe_peak = _lfe_true_peak_dbtp(lfe_data, sr)
        lfe_rms = _lfe_rms_dbfs(lfe_data)
        lfe_crest = _lfe_crest_factor(lfe_data)
        lfe_activity = _lfe_activity(lfe_data)

        print("\n=== LFE Channel Analysis ===")
        print(f"  Low-pass filter     : {_LFE_LOWPASS_HZ:.0f} Hz "
              f"(Butterworth order {_LFE_LOWPASS_ORDER}, zero-phase)")
        print(f"  LFE loudness        : {lfe_loudness:8.1f} LUFS")
        if not np.isnan(integrated) and not np.isnan(lfe_loudness):
            lfe_ratio = lfe_loudness - integrated
            print(f"  LFE-to-main ratio   : {lfe_ratio:8.1f} dB")
        print(f"  LFE peak            : {lfe_peak:8.1f} dBTP")
        print(f"  LFE RMS level       : {lfe_rms:8.1f} dBFS")
        print(f"  LFE crest factor    : {lfe_crest:8.1f} dB")
        print(f"  LFE activity        : {lfe_activity:8.1f} %")

        if lfe_peak > -1.0:
            print(f"  [WARN] LFE peak exceeds -1 dBTP - risk of clipping.")
        if not np.isnan(lfe_loudness) and not np.isnan(integrated):
            ratio = lfe_loudness - integrated
            if ratio > -6.0:
                print(f"  [WARN] LFE too loud ({ratio:.1f} dB) - should be "
                      f"-8 to -12 dB below main mix.")
            elif ratio < -15.0:
                print(f"  [INFO] LFE very quiet ({ratio:.1f} dB) - check if "
                      f"intentional.")

    # Surround channel RMS relative to center
    center_dbfs, surround_results = _surround_rms_relative_to_center(
        data, sr, effective_layout)

    if surround_results:
        print("\n=== Channel RMS relative to Center ===")
        print(f"  Low-pass  (LFE)     : {_LFE_LOWPASS_HZ:.0f} Hz "
              f"(Butterworth order {_LFE_LOWPASS_ORDER}, zero-phase)")
        print(f"  High-pass (surround): {_SURROUND_HIGHPASS_HZ:.0f} Hz "
              f"(Butterworth order {_SURROUND_HIGHPASS_ORDER}, zero-phase)")
        print(f"  C    (Ch 3)         : {center_dbfs:8.1f} dBFS  (reference, unfiltered)")
        for label, rms_dbfs, rel_db in surround_results:
            print(f"  {label}     : {rms_dbfs:8.1f} dBFS  {rel_db:+.1f} dB rel. C")

    # DC offset
    dc_offsets = _dc_offset_per_channel(data)
    any_dc_warn = any(abs(dc) >= _DC_OFFSET_WARN_LINEAR for _, dc, _ in dc_offsets)
    print("\n=== DC Offset ===")
    print(f"  Warning threshold   : {_DC_OFFSET_WARN_LINEAR:.0e} "
          f"({20.0 * np.log10(_DC_OFFSET_WARN_LINEAR):.0f} dBFS)")
    for ch_idx, dc_lin, dc_dbfs in dc_offsets:
        warn = "  [WARN]" if abs(dc_lin) >= _DC_OFFSET_WARN_LINEAR else ""
        print(f"  Channel {ch_idx + 1:2d}          : "
              f"{dc_lin:+.2e}  ({dc_dbfs:6.1f} dBFS){warn}")
    if not any_dc_warn:
        print("  All channels within acceptable range.")

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
        "lfe_loudness": lfe_loudness,
        "lfe_peak_dbtp": lfe_peak,
        "lfe_rms_dbfs": lfe_rms,
        "lfe_crest_factor": lfe_crest,
        "lfe_activity_percent": lfe_activity,
        "center_rms_dbfs": center_dbfs,
        "surround_rms": surround_results,
        "dc_offsets": dc_offsets,
        "audio_data": data,  # Raw audio for frequency analysis
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
    #          row 1 – histogram (left) | metrics panel (right)
    #          row 2 – frequency response (full width)
    fig = plt.figure(figsize=(14, 16), constrained_layout=True)
    gs = GridSpec(4, 2, figure=fig,
                  height_ratios=[1.1, 0.75, 0.75, 1.4],
                  width_ratios=[4.5, 1])
    ax1 = fig.add_subplot(gs[0, :])       # loudness over time  – full width
    ax2 = fig.add_subplot(gs[1:3, 0])     # histogram           – left, 2 rows tall
    ax4_rel = fig.add_subplot(gs[1, 1])   # channel RMS         – bottom right
    ax4_lfe = fig.add_subplot(gs[2, 1])   # LFE metrics         – top right
    ax3 = fig.add_subplot(gs[3, :])

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

    ax1.set_xlabel("Time (s)")
    ax1.set_ylabel("Loudness (LUFS)")
    ax1.set_ylim(-55, -5)
    ax1.set_title(f"Film loudness over time  -  LRA {result['lra_lu']:.1f} LU, "
                  f"true peak {result['true_peak_dbtp']:.1f} dBTP")
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="lower right", fontsize=9)

    # ===== Subplot 2: Loudness Range Histogram =====
    # Apply the same two-stage gate as _loudness_range() / EBU Tech 3342:
    #   stage 1 – absolute gate at -70 LUFS
    #   stage 2 – relative gate at -20 LU below the mean of stage-1 values
    lv_abs = short_term[short_term > _ABS_GATE_LUFS]

    if lv_abs.size > 0:
        rel_gate = np.mean(lv_abs) - _LRA_REL_GATE_LU
        lv_gated = lv_abs[lv_abs >= rel_gate]

        bins = np.arange(-50, 1, 1)
        ax2.hist(lv_abs, bins=bins, color="#1f77b4", alpha=0.4,
                 edgecolor="none", label="Absolute-gated")
        ax2.hist(lv_gated, bins=bins, color="#1f77b4", alpha=0.8,
                 edgecolor="black", linewidth=0.4, label="Double-gated (LRA)")

        ax2.axvline(result["integrated_lufs"], color="#d62728", ls=":", lw=2.0,
                    label=f"Integrated {result['integrated_lufs']:.1f} LUFS")

        # p10 / p95 of the double-gated set — identical to _loudness_range()
        p10 = np.percentile(lv_gated, 10)
        p95 = np.percentile(lv_gated, 95)
        ax2.axvline(p10, color="#ff7f0e", ls="--", lw=2.0,
                    label=f"p10  {p10:.1f} LUFS")
        ax2.axvline(p95, color="#2ca02c", ls="--", lw=2.0,
                    label=f"p95  {p95:.1f} LUFS")

        ax2.axvline(_ABS_GATE_LUFS, color="#888888", ls="-", lw=1.0,
                    alpha=0.5, label=f"Abs. gate {_ABS_GATE_LUFS:.0f} LUFS")
        ax2.axvline(rel_gate, color="#aaaaaa", ls=":", lw=1.0,
                    alpha=0.7, label=f"Rel. gate {rel_gate:.1f} LUFS")

        ax2.set_xlabel("Loudness (LUFS)")
        ax2.set_ylabel("Count")
        ax2.set_title(f"Loudness Range Distribution  –  LRA {result['lra_lu']:.1f} LU")
        ax2.grid(True, alpha=0.3, axis="y")
        ax2.legend(loc="upper right", fontsize=9)
        ax2.set_xlim(-50, 0)

    # ===== Subplot 3: Frequency Response (Center vs LFE) =====
    audio_data = result["audio_data"]
    sr = result["sr"]
    n_ch = result["n_channels"]

    center_idx = 2 if n_ch > 2 else None
    lfe_idx = 3 if n_ch > 3 else None

    if center_idx is not None and audio_data.shape[1] > center_idx:
        freqs_c, mag_c = _frequency_response(audio_data[:, center_idx], sr)
        ax3.plot(freqs_c, mag_c, lw=1.0, color="#1f77b4", label="Center")

    if lfe_idx is not None and audio_data.shape[1] > lfe_idx:
        freqs_l, mag_l = _frequency_response(audio_data[:, lfe_idx], sr)
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
    lfe_loudness = result["lfe_loudness"]
    lfe_ratio = lfe_loudness - result["integrated_lufs"]
    lfe_lines = [
        "LFE Channel Analysis",
        f"  Filter       LP @ {_LFE_LOWPASS_HZ:.0f} Hz, ord {_LFE_LOWPASS_ORDER}",
        f"  Loudness     {_fv(lfe_loudness,                  '%7.1f')} LUFS",
        f"  LFE/Main     {_fv(lfe_ratio,                     '%+7.1f')} dB",
        f"  True peak    {_fv(result['lfe_peak_dbtp'],        '%7.1f')} dBTP",
        f"  RMS          {_fv(result['lfe_rms_dbfs'],         '%7.1f')} dBFS",
        f"  Crest        {_fv(result['lfe_crest_factor'],     '%7.1f')} dB",
        f"  Activity     {_fv(result['lfe_activity_percent'], '%7.1f')} %",
    ]

    ax4_lfe.axis('off')
    ax4_lfe.text(0.03, 0.97, "\n".join(lfe_lines),
                 transform=ax4_lfe.transAxes, fontsize=8.5,
                 fontfamily="monospace", va="top", ha="left",
                 bbox=dict(boxstyle="round,pad=0.5", facecolor="#ddeeff",
                           edgecolor="#3366aa", alpha=0.85))

    # --- Channel RMS panel ---
    surround_results = result["surround_rms"]
    center_dbfs = result["center_rms_dbfs"]
    rel_lines = ["Channel RMS relative to Center"]
    if not (isinstance(center_dbfs, float) and np.isnan(center_dbfs)):
        rel_lines.append(f"  {'C  (Ch 3)':<12}  [ref]")
        for label, _rms_dbfs, rel_db in surround_results:
            rel_lines.append(f"  {label:<12}  {rel_db:+.1f} dB")

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
        if len(paths) > 1:
            print(f"\n{'=' * 64}")
            print(f"  File {i + 1}/{len(paths)}: {path}")
            print(f"{'=' * 64}\n")

        result = analyze(path, layout=args.layout,
                         lfe_channel=args.lfe_channel,
                         per_channel=args.per_channel,
                         exclude_surround=args.exclude_surround)

        if args.plot is not None and not args.no_plot:
            plot_path = (fixed_plot_path
                         if fixed_plot_path
                         else os.path.splitext(path)[0] + ".png")
            _plot(result, plot_path)


if __name__ == "__main__":
    main()
