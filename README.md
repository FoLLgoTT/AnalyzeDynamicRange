# AnalyzeDynamicRange

A Python application for analyzing the dynamic range, LFE activity and low-end extension of movie audio files.

![example.png](https://github.com/FoLLgoTT/AnalyzeDynamicRange/blob/main/example.png)

## Overview

`AnalyzeDynamicRange.py` analyzes audio files and calculates professional loudness metrics according to:

- **[ITU-R BS.1770-4](https://www.itu.int/rec/R-REC-BS.1770-4-201510-S/en)** (International Telecommunication Union)
- **[EBU R128](https://tech.ebu.ch/docs/tech/tech3341.pdf)** (European Broadcasting Union)

These standards are internationally recognized and used in the film industry, streaming services, and broadcasting to standardize audio content consistently and technically correctly.

## Features

### Loudness and Dynamic Range Metrics

| Metric | Unit | Description | Source |
|--------|------|-------------|--------|
| **Integrated Loudness** | LUFS | Gated programme loudness across the entire file | ITU-R BS.1770-4 / EBU R128 |
| **Loudness Range (LRA)** | LU | Dynamic range per [EBU Tech 3342](https://tech.ebu.ch/docs/tech/tech3342.pdf) | EBU Tech 3342 |
| **RMS Level** | dBFS | Overall RMS across all channels | *custom* |
| **Momentary Loudness** | LUFS | Time series over 400 ms windows | EBU Tech 3341 |
| **Short-term Loudness** | LUFS | Time series over 3-second windows | EBU Tech 3341 |

Metrics marked *custom* are not defined by ITU or EBU. They are project-specific additions intended for comparing releases and are not comparable to values from standard loudness meters.

### LFE Band Analysis (custom)

> **Custom metrics.** None of the LFE metrics below are defined by ITU or EBU; band limits, activity threshold and labels are project-specific.

The LFE channel is divided

| Metric | Unit | Description |
|--------|------|-------------|
| **LFE active** | % of runtime | Fraction of 400 ms windows where full-band LFE exceeds the activity threshold |
| **Band activity** | % of active windows | Per band: fraction of LFE-active windows where that band also exceeds the threshold |
| **P95 level** | dB rel. main | 95th percentile short-term level of the band over active windows |
| **Peak level** | dB rel. main | Maximum short-term level of the band over active windows |
| **Peak−P95 spread** | dB | Dynamic headroom within active moments |
| **Sub-bass ratio** | dB | Energy in 20–40 Hz relative to 40–120 Hz; indicates how "deep" the bass is |
| **Infrasound ratio** | dB | Energy below 20 Hz relative to 20–120 Hz; flags artefacts or intentional infrasound |
| **Spectral centroid** | Hz | Energy-weighted mean frequency of the 20–120 Hz content over active windows |

Frequency bands:

| Band | Range | Perception |
|---|---|---|
| Infrasound | < 20 Hz | Not audible; pressure/vibration sensation or artefact; bass shakers |
| Sub-bass | 20–40 Hz | Mainly tactile (body sensation, bass shakers) |
| Bass | 40–80 Hz | Heard and felt; core LFE impact |
| Upper LFE | 80–120 Hz | Mainly audible; crossover region to main speakers |

### Channel Level Metrics (relative to Center) (custom)

> **Custom metric.** Unweighted, ungated RMS (no K-weighting); not defined by ITU or EBU.

All channels

| Channel | Filter applied |
|---------|---------------|
| L, R | none |
| LFE | low-pass at 120 Hz (Butterworth order 4, zero-phase) |
| Cb, Ls, Rs, Rc, Lrs, Rrs | high-pass at 80 Hz (Butterworth order 4, single-pass) |

### Signal Processing

- **K-Weighting Filter**: Natively implemented BS.1770-4 two-stage biquad filter
- **Double gating**: Absolute gate at −70 LUFS + relative gate −10 LU below mean (integrated loudness); −20 LU below mean (LRA) — both per EBU Tech 3342
- **Surround Channels**: +1.5 dB weighting for side surrounds (Ls/Rs) per BS.1770-4; back channels (Lrs/Rrs, Rc, Cb) weighted 1.0
- **LRA per EBU Tech 3342**: relative gate −20 LU below the power-mean of the absolute-gated short-term blocks, strict `>` gating, nearest-rank p10/p95
- **LFE Exclusion**: Subwoofer channel excluded from the loudness sum

### Frequency response (custom)
Not defined by ITU or EBU. Low-end frequency response

---

## Installation

### Required packages

```bash
pip install numpy scipy soundfile
```

### Optional (for plots)

```bash
pip install matplotlib
```

---

## Usage

### Basic usage

```bash
python AnalyzeDynamicRange.py movie.wav
```

A plot is generated automatically as `movie.png`. Use `--no-plot` to suppress it.

### Wildcard and multiple files

Glob patterns are supported on all platforms:

```bash
# All WAV files in the current directory
python AnalyzeDynamicRange.py *.wav

# All WAV files in a subdirectory
python AnalyzeDynamicRange.py movies/*.wav

# Two explicit files
python AnalyzeDynamicRange.py movie1.wav movie2.wav
```

Each file produces its own `<filename>.png` plot. When multiple files match, a separator is printed between each analysis.

---

## Options

### `--layout`

Overrides automatic channel layout detection:

```bash
python AnalyzeDynamicRange.py movie.wav --layout 2.1
python AnalyzeDynamicRange.py movie.wav --layout 4.0
python AnalyzeDynamicRange.py movie.wav --layout 4.1
python AnalyzeDynamicRange.py movie.wav --layout 5.0
python AnalyzeDynamicRange.py movie.wav --layout 5.1
python AnalyzeDynamicRange.py movie.wav --layout 6.1
python AnalyzeDynamicRange.py movie.wav --layout 7.1
python AnalyzeDynamicRange.py movie.wav --layout stereo
python AnalyzeDynamicRange.py movie.wav --layout mono
```

### `--lfe-channel`

Specifies the 0-based index of the LFE channel:

```bash
python AnalyzeDynamicRange.py movie.wav --lfe-channel 3
```

### `--per-channel`

Reports integrated loudness for each individual channel:

```bash
python AnalyzeDynamicRange.py movie.wav --per-channel
```

### `--exclude-surround`

Excludes surround channels from the loudness sum (front/dialogue analysis only):

```bash
python AnalyzeDynamicRange.py movie.wav --exclude-surround
```

### `--plot` / `--no-plot`

The plot is generated **by default**. To control output:

```bash
# Default: auto-generates movie.png
python AnalyzeDynamicRange.py movie.wav

# Custom output filename
python AnalyzeDynamicRange.py movie.wav --plot loudness_analysis.png

# Suppress the plot
python AnalyzeDynamicRange.py movie.wav --no-plot
```

| Invocation | Result |
|---|---|
| `script.py movie.wav` | Plot → `movie.png` |
| `script.py movie.wav --plot` | Plot → `movie.png` |
| `script.py movie.wav --plot out.png` | Plot → `out.png` |
| `script.py movie.wav --no-plot` | No plot |
| `script.py *.wav` | Per-file plots → `<name>.png` |
| `script.py *.wav --plot out.png` | Warning; falls back to per-file naming |

### `--json`

Writes a compact, machine-readable **JSON** summary of the analysis alongside the console output. Useful for batch processing, QC pipelines, or feeding results into other tools (`jq`, Python, Excel/Power BI, etc.). Disabled by default.

```bash
# Default: auto-generates movie.json
python AnalyzeDynamicRange.py movie.wav --json

# Custom output filename
python AnalyzeDynamicRange.py movie.wav --json report.json
```

| Invocation | Result |
|---|---|
| `script.py movie.wav` | No JSON (default) |
| `script.py movie.wav --json` | JSON → `movie.json` |
| `script.py movie.wav --json out.json` | JSON → `out.json` |
| `script.py *.wav --json` | Per-file JSON → `<name>.json` |
| `script.py *.wav --json out.json` | Warning; falls back to per-file naming |

The JSON contains the same summary metrics shown on the console (integrated loudness, LRA, RMS, per-channel loudness when `--per-channel` is set, LFE band analysis, channel RMS relative to Center) plus the frequency-response curves on a fixed, deterministic 1/24-octave grid (same frequency points for every file), which makes them suitable for diffing between releases. The full momentary loudness time-series is omitted since it is plot-only data. `NaN`/`Infinity` values (e.g. metrics not applicable to the current layout) are serialised as `null`.

A second, companion file with the `_LoudnessOverTime` suffix (e.g. `movie_LoudnessOverTime.json`) is written alongside the main JSON.

Example structure:

```jsonc
{
  "channels": 6,
  "layout": "5.1",
  "loudness": {
    "integrated_lufs": -24.3,
    "lra_lu": 18.7,
    "lra_label": "dynamic",
    "rms_dbfs": -20.1,
    "momentary_max_lufs": -12.4,
    "short_term_max_lufs": -14.8,
    "short_term_min_lufs": -41.2
  },
  "per_channel": [
    { "channel": 1, "integrated_lufs": -25.1, "role": "normal", "excluded_from_sum": false }
  ],
  "lfe": {
    "threshold_dbfs": -39.3,
    "activity_pct": 12.4,
    "activity_label": "moderate",
    "sub_bass_ratio_db": -2.1,
    "sub_bass_label": "moderate",
    "infra_ratio_db": -28.5,
    "spectral_centroid_hz": 52.3,
    "bands": [
      { "label": "<20 Hz", "activity_pct": 3.1, "p95_rel_db": -8.2, "peak_rel_db": -2.1, "spread_db": 6.1 }
    ]
  },
  "channel_rms_relative_to_center": {
    "center_dbfs": -22.0,
    "channels": [
      { "label": "Ls", "rms_dbfs": -25.3, "rel_db": -3.3 }
    ]
  }
}
```

`per_channel` is `null` unless `--per-channel` was passed. `lfe` is `null` when no LFE channel is present. `channel_rms_relative_to_center` is `null` when there is no Center channel to reference against (e.g. **2.1**, **mono**, **stereo**).

`movie_FrequencyResponse.json` contains one entry per available channel (`left`, `right` for stereo/2.1, `center` for layouts with a Center channel, `lfe` when an LFE channel is present):

```jsonc
{
  "left": {
    "freqs_hz": [2.0, 2.1, 2.2, "...", 200.0],
    "level_db": [-1.2, -0.9, -0.4, "...", 0.0]
  },
  "center": { "freqs_hz": ["..."], "level_db": ["..."] },
  "lfe": { "freqs_hz": ["..."], "level_db": ["..."] }
}
```

Each entry holds the raw 1/24-octave band points (≈161 points, 2–200 Hz, 0 dB-normalised) — the same fixed, deterministic grid is used for every file, so `level_db` arrays from different releases can be compared or subtracted point-by-point to spot spectral differences (e.g. an added/removed high-pass filter).

`movie_Histogram.json` mirrors the Panel 3 histogram:

```jsonc
{
  "bin_edges_lufs": [-45, -44, "...", 5],
  "absolute_gated_pct": [0.0, 0.1, "...", 0.0],
  "double_gated_pct": [0.0, 0.0, "...", 0.0],
  "rel_gate_lufs": -34.5,
  "p10_lufs": -28.3,
  "p95_lufs": -14.1
}
```

`bin_edges_lufs` defines 1 dB wide bins spanning a fixed 50 dB range centred on the LRA band (`len(bin_edges_lufs) - 1` bars, matching the plotted bar count), `absolute_gated_pct`/`double_gated_pct` are the percentage of (only absolute- / absolute+relative-) gated short-term loudness samples falling into each bin, and `rel_gate_lufs`/`p10_lufs`/`p95_lufs` are the same reference values shown in the plot. The whole file is `null` when the short-term loudness curve has no samples above the absolute gate.

### `--skip-existing`

Skips any input file whose corresponding plot PNG already exists on disk. Useful when re-running the script on a folder of files after adding new titles:

```bash
python AnalyzeDynamicRange.py *.wav --skip-existing
```

---

## Plot
The plot is saved as a PNG (120 dpi) and contains **six panels**:

```
┌────────────────────────────────────────────────┐
│  1. Loudness over time              (full width)│
├────────────────────────────────────────────────┤
│  2. LFE activity strip              (full width)│
├───────────────────────────┬────────────────────┤
│                           │  4. Channel RMS    │
│  3. LRA Histogram (wider) │     rel. Center    │
│                           ├────────────────────┤
│                           │  5. LFE Analysis   │
│                           ├────────────────────┤
│                           │  6. Summary        │
└───────────────────────────┴────────────────────┘
┌────────────────────────────────────────────────┐
│  7. Frequency Response              (full width)│
└────────────────────────────────────────────────┘
```

### Panel 1 — Loudness over time

- Blue curve: Short-term loudness (3-second windows)
- Red dashed line: Integrated loudness reference
- Orange shaded band: LRA bounds (p10 to p95 of double-gated values)
- Y-axis: 50 dB range, automatically scaled so the top aligns above the highest valid short-term value (rounded to the next 5 dB step)

### Panel 2 — LFE activity strip

A narrow bar directly below the loudness curve, sharing the same time axis:

- **Red** (opaque): Short-term RMS windows where LFE exceeds the activity threshold (`integrated − 15 dB`)
- **Grey** (transparent): Windows below the activity threshold (LFE inactive or very quiet)
- Y-axis: −60 to 0 dBFS (LFE short-term RMS, 3-second windows, 0.1-second hop)
- Hidden when no LFE channel is present

### Panel 3 — Loudness Range Distribution

- Light bars: Absolute-gated values (> −70 LUFS), Y-axis in **percent of total windows**
- Dark bars: Double-gated values used for LRA calculation (also in percent)
- Red dotted line: Integrated loudness
- Orange dashed line: p10 (LRA lower bound)
- Green dashed line: p95 (LRA upper bound)
- Grey lines: Absolute and relative gate thresholds
- Title displays the EBU Tech 3342 LRA value — identical to Panel 1
- X-axis is dynamically centred on the p10/p95 midpoint and always spans 50 dB in 5 dB steps

Both the bounds and the title value are computed using the same double-gating logic as `_loudness_range()`, so the LRA shown in both panels is always identical.

### Panel 4 — Channel RMS relative to Center

Text box showing the RMS level (*custom*, unweighted) of each channel relative to the unfiltered Center channel.

### Panel 5 — LFE Band Analysis

Text box showing the LFE band analysis: overall activity (% of runtime), per-band activity / P95 / Peak levels (relative to main integrated loudness), sub-bass ratio, and spectral centroid.

### Panel 6 — Summary

Text box with a concise qualitative assessment derived from the numeric results. All labels and their thresholds are **custom** (not part of ITU/EBU), including the LRA labels:

| Field | Source metric | Labels |
|---|---|---|
| **Loudness range** | LRA (LU) | `heavily compressed` · `compressed` · `moderate` · `dynamic` · `high dynamic` · `extreme dynamic` |
| **LFE activity** | LFE active (% of runtime) | `restrained` · `moderate` · `active` · `very active` · `overused` |
| **LFE depth** | Sub-bass ratio (dB) | `upper-bass` · `moderate` · `deep` · `seismic` |

The thresholds for each label are documented in the sections below.

### Panel 7 — Frequency Response

- Green curve: Left channel (Ch 1)
- Orange curve: Right channel (Ch 2) — stereo and 2.1 only
- Blue curve: Center channel (Ch 3) — layouts with a Center channel only
- Red curve: LFE channel — layouts with an LFE channel only
- X-axis: 1–200 Hz, **logarithmic**
- Y-axis: 50 dB range (normalized to 0 dB peak)
- **1/24 octave smoothing** via Welch PSD averaged into fractional-octave bands
- **PCHIP interpolation** onto 500 log-spaced points for a smooth visual curve
- Signal downsampled to **800 Hz** before analysis for maximum frequency resolution in the 1–200 Hz range (bin spacing ≈ 0.006 Hz)

---

## Channel Layouts

Channel order according to Microsoft wave format (see [here](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/channel-mask)):

| Layout | Channels |
|--------|----------|
| Mono (1 ch) | C |
| Stereo (2 ch) | L R |
| 2.1 (3 ch, explicit) | L R LFE |
| 4.0 (4 ch, explicit) | L R C Cb |
| 4.1 (5 ch) | L R C LFE Cb |
| 5.0 (5 ch, explicit) | L R C Ls Rs |
| 5.1 (6 ch) | L R C LFE Ls Rs |
| 6.1 (7 ch) | L R C LFE Rc Ls Rs |
| 7.1 (8 ch) | L R C LFE Lrs Rrs Ls Rs |
| >7.1 (>8 ch) | 7.1 bed applied to ch 1–8; height channels (ch 9+) excluded with a warning |

The layout is auto-detected from the channel count. 3 channels are always treated as unknown by default; use `--layout 2.1` to select L R LFE. 4 channels are always treated as unknown by default; use `--layout 4.0` to select L R C Cb. 5 channels are detected as **4.1** by default; use `--layout 5.0` to select L R C Ls Rs. Use `--layout` to override for any other format.

### Channel weighting (BS.1770)

| Channel type | Weight |
|---|---|
| L, R, C | 1.0 |
| LFE | 0.0 (excluded) |
| Ls, Rs (side surrounds, azimuth 60–120°) | 1.41 (+1.5 dB) |
| Lrs, Rrs, Rc, Cb (back channels) | 1.0 |
| Height channels (ch 9+) | 0.0 (excluded) |

Per BS.1770-4 Table 3, only channels at 60–120° azimuth receive +1.5 dB; back channels are weighted 1.0. `--exclude-surround` mutes both side and back channels.

> **5.0**: No LFE channel present; Ls and Rs are weighted +1.5 dB.  
> **4.1**: LFE = Ch 4 (excluded), Cb = Ch 5 (1.0).  
> **4.0**: No LFE channel present; Cb = Ch 4 (1.0).  
> **2.1**: No Center channel; LFE = Ch 3 (excluded). L and R are unweighted (1.0); no surround gain is applied since there are no surround channels.

---

## Measurement Standards

### Integrated Loudness (LUFS)

**Broadcast targets:**

| Platform | Target |
|---|---|
| Cinema (DCI) | −23 LUFS |
| TV / Streaming | −16 to −14 LUFS |
| Podcasts | −14 to −16 LUFS |

### Loudness Range (LRA, LU)

LRA measures how much the loudness *varies over time*. It is computed per **EBU Tech 3342** using a two-stage gate on short-term loudness values (3-second windows):

1. **Absolute gate** — blocks below −70 LUFS are discarded (silence).
2. **Relative gate** — blocks more than −20 LU below the power-mean loudness of the remaining values are also discarded

The LRA is the difference between the **95th and 10th percentile** of the remaining values, expressed in Loudness Units (LU).

**What it tells you:**

- A **low LRA** means the loudness barely changes throughout the movie — the mix is dynamically flat. This is typical of heavily limited or compressed material where loud and quiet scenes sound nearly equally loud. It reduces the emotional impact of the soundtrack.
- A **high LRA** means the loudness varies widely — quiet dialogue scenes contrast strongly with loud action sequences. This is the intended dynamic behaviour for cinema and high-quality streaming.

**LRA does not measure peak levels.** A movie can have a high LRA but still be clipping, or a low LRA but be at a perfectly safe level.

The labels below are **custom** classifications; EBU Tech 3342 defines only the LRA value itself.

| LRA | Label |
|---|---|
| > 35 LU | `extreme dynamic` |
| > 30 LU | `high dynamic` |
| > 25 LU | `dynamic` |
| > 20 LU | `moderate` |
| > 15 LU | `compressed` |
| ≤ 15 LU | `heavily compressed` |

---

## LFE Channel Analysis (custom)

> All metrics, thresholds and labels in this section are **custom** and not part of ITU or EBU standards.

The LFE channel

### Activity gating

Long silent passages in the LFE channel would distort statistical metrics such as P95 or spectral centroid. A 400 ms sliding window is classified as *active* if the full-band LFE short-term RMS exceeds:

```
threshold = integrated_main − 15 dB
```

All per-band level statistics and energy ratios are computed exclusively over active windows. The overall activity percentage (shown in the header) indicates how much of the runtime contains meaningful LFE content.

| Activity | Label |
|---|---|
| > 20 % | `overused` |
| > 15 % | `very active` |
| > 10 % | `active` |
| > 5 % | `moderate` |
| ≤ 5 % | `restrained` |

### Sub-bass ratio

Measures the spectral balance between the deepest and the mid-bass range:

```
Sub-bass ratio = 10 × log10( E(20–40 Hz) / E(40–120 Hz) )
```

| Ratio | Category |
|---|---|
| > 0 dB | `seismic` — energy below 40 Hz dominates; strong body sensation |
| > −3 dB | `deep` — deep grumble, noticeable sub-bass extension |
| > −6 dB | `moderate` — balanced impact, controlled low end |
| ≤ −6 dB | `upper-bass` — energy concentrated in 40–120 Hz mid-bass |

### Infrasound ratio

```
Infrasound ratio = 10 × log10( E(<20 Hz) / E(20–120 Hz) )
```

A ratio above −20 dB with sustained infrasound activity (> 10 % of active windows) triggers a `[WARN]` and may indicate recording artefacts (wind noise, mechanical rumble) or, in rare cases, intentional infrasound design.

### Spectral centroid

The energy-weighted mean frequency of the 20–120 Hz content over active windows. A centroid below ~45 Hz indicates primarily tactile sub-bass content; above ~70 Hz the LFE is dominated by mid-bass punch rather than deep extension.

---

## Channel RMS relative to Center

Surround channels are high-pass filtered at **80 Hz** (Butterworth order 4, zero-phase) before measurement to remove low-frequency content that would skew the level comparison. The LFE channel uses the existing **120 Hz low-pass** filter.

Surround channels reported per layout:

| Layout | Channels reported |
|---|---|
| 2.1 | Not reported — no Center channel to reference against |
| 4.0 | Cb (4) |
| 4.1 | LFE (4), Cb (5) |
| 5.0 | Ls (4), Rs (5) |
| 5.1 | LFE (4), Ls (5), Rs (6) |
| 6.1 | LFE (4), Rc (5), Ls (6), Rs (7) |
| 7.1 | LFE (4), Lrs (5), Rrs (6), Ls (7), Rs (8) |
| >7.1 | Same as 7.1 — height channels (ch 9+) are not reported |

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `numpy` | Numerical computations |
| `scipy` | Filters, resampling, Welch PSD, PCHIP interpolation |
| `soundfile` | Audio file reading |
| `matplotlib` | Plot rendering (optional) |

---

## Supported Formats

All formats supported by `libsndfile` / `soundfile`:

- WAV (16-bit, 24-bit, 32-bit float)
- FLAC
- AIFF
- OGG
- CAF, and more

---

## Memory Usage

### How memory is managed

The script loads the entire file into RAM at the original sample rate. The following optimisations keep peak RAM well below the raw-file size:

1. The entire audio is **downsampled to 16 kHz** immediately after reading. All metrics (K-weighted loudness, LRA, RMS, LFE loudness/crest, surround RMS) are computed from this compact representation. The downsampled copy is ~3× smaller for a 48 kHz source.
2. The **original full-resolution array is immediately freed** after the downsampling step.
3. The **frequency-response plot** uses 16 kHz, so no large array is retained after analysis.

---

## License

See project license for details.
