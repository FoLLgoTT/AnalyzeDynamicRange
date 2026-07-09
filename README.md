# AnalyzeDynamicRange

A Python application for analyzing the dynamic range and loudness of movie audio files according to international broadcast standards.

## Overview

`AnalyzeDynamicRange.py` analyzes audio files and calculates professional loudness metrics according to:

- **[ITU-R BS.1770-4](https://www.itu.int/rec/R-REC-BS.1770-4-201510-S/en)** (International Telecommunication Union)
- **[EBU R128](https://tech.ebu.ch/docs/tech/tech3341.pdf)** (European Broadcasting Union)

These standards are internationally recognized and used in the film industry, streaming services, and broadcasting to standardize audio content consistently and technically correctly.

## Features

### Loudness and Dynamic Range Metrics

| Metric | Unit | Description |
|--------|------|-------------|
| **Integrated Loudness** | LUFS | Gated programme loudness across the entire file |
| **Loudness Range (LRA)** | LU | Dynamic range per EBU Tech 3342 |
| **RMS Level** | dBFS | Overall RMS across all channels |
| **Momentary Loudness** | LUFS | Time series over 400 ms windows |
| **Short-term Loudness** | LUFS | Time series over 3-second windows |
| **DC Offset** | dBFS | Per-channel mean signal bias |

### LFE Channel Metrics (120 Hz low-pass applied before all measurements)

| Metric | Unit | Description |
|--------|------|-------------|
| **LFE Loudness** | LUFS | Integrated loudness of the LFE channel |
| **LFE-to-Main Ratio** | dB | Level difference between LFE and main mix |
| **LFE RMS** | dBFS | RMS level of the LFE channel |
| **LFE Crest Factor** | dB | Peak-to-RMS ratio of LFE |

### Channel Level Metrics (relative to Center)

All channels are measured against the unfiltered Center channel (Ch 3) as reference.

| Channel | Filter applied |
|---------|---------------|
| L, R | none |
| LFE | low-pass at 120 Hz (Butterworth order 4, zero-phase) |
| Ls, Rs, Rc, Lrs, Rrs | high-pass at 80 Hz (Butterworth order 4, zero-phase) |

### Signal Processing

- **K-Weighting Filter**: Natively implemented BS.1770-4 two-stage biquad filter
- **Double gating**: Absolute gate at −70 LUFS + relative gate −10 LU below mean (integrated loudness); −20 LU below mean (LRA) — both per EBU Tech 3342
- **Surround Channels**: Automatic +1.5 dB weighting per BS.1770
- **LFE Exclusion**: Subwoofer channel excluded from the loudness sum

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

---

## Plot

The plot is saved as a PNG (120 dpi) and contains **four panels**:

```
┌────────────────────────────────────────────────┐
│  1. Loudness over time              (full width)│
└────────────────────────────────────────────────┘
┌───────────────────────────┬────────────────────┐
│  2. LRA Histogram (wider) │  3. LFE Analysis   │
│                           ├────────────────────┤
│                           │  4. Channel RMS    │
│                           │     rel. Center    │
└───────────────────────────┴────────────────────┘
┌────────────────────────────────────────────────┐
│  5. Frequency Response              (full width)│
└────────────────────────────────────────────────┘
```

### Panel 1 — Loudness over time

- Blue curve: Short-term loudness (3-second windows)
- Red dashed line: Integrated loudness reference
- Orange shaded band: LRA bounds (p10 to p95 of double-gated values)
- Y-axis: −50 to 0 LUFS

### Panel 2 — Loudness Range Distribution

- Light bars: Absolute-gated values (> −70 LUFS)
- Dark bars: Double-gated values used for LRA calculation
- Red dotted line: Integrated loudness
- Orange dashed line: p10 (LRA lower bound)
- Green dashed line: p95 (LRA upper bound)
- Grey lines: Absolute and relative gate thresholds
- Title displays the EBU Tech 3342 LRA value — identical to Panel 1

Both the bounds and the title value are computed using the same double-gating logic as `_loudness_range()`, so the LRA shown in both panels is always identical.

### Panel 3 — LFE Channel Analysis

Text box showing all LFE metrics (loudness, LFE/Main ratio, RMS, crest factor).

### Panel 4 — Channel RMS relative to Center

Text box showing the RMS level of each channel relative to the unfiltered Center channel.

### Panel 5 — Frequency Response

- Green curve: Left channel (Ch 1)
- Blue curve: Center channel (Ch 3)
- Red curve: LFE channel (Ch 4)
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
| 5.1 (6 ch) | L R C LFE Ls Rs |
| 6.1 (7 ch) | L R C LFE Rc Ls Rs |
| 7.1 (8 ch) | L R C LFE Lrs Rrs Ls Rs |

### Channel weighting (BS.1770)

| Channel type | Weight |
|---|---|
| L, R, C | 1.0 |
| LFE | 0.0 (excluded) |
| Ls, Rs, Rc, Lrs, Rrs | 1.41 (+1.5 dB) |

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
2. **Relative gate** — blocks more than −20 LU below the mean of the remaining values are also discarded (very quiet passages that are not representative of the programme content).

The LRA is the difference between the **95th and 10th percentile** of the remaining values, expressed in Loudness Units (LU).

**What it tells you:**

- A **low LRA** means the loudness barely changes throughout the movie — the mix is dynamically flat. This is typical of heavily limited or compressed material where loud and quiet scenes sound nearly equally loud. It reduces the emotional impact of the soundtrack.
- A **high LRA** means the loudness varies widely — quiet dialogue scenes contrast strongly with loud action sequences. This is the intended dynamic behaviour for cinema and high-quality streaming.

**LRA does not measure peak levels.** A movie can have a high LRA but still be clipping, or a low LRA but be at a perfectly safe level.

| LRA | Interpretation |
|---|---|
| < 5 LU | Heavily compressed — likely over-limited or heavily processed |
| 5–10 LU | Moderately compressed — typical of broadcast TV |
| 10–20 LU | Dynamic — typical target for theatrical movie mixes |
| > 20 LU | Very dynamic — orchestral, documentary, dialogue-heavy drama |

### DC Offset (dBFS per channel)

The arithmetic mean of all samples in a channel. Non-zero DC causes audible clicks at edit points and can saturate output stages.

| Level | Assessment |
|---|---|
| < −80 dBFS (< 1×10⁻⁴ linear) | Acceptable |
| ≥ −80 dBFS | [WARN] DC offset present — apply a high-pass filter |

---

## LFE Channel Analysis

The LFE channel is analyzed **after** applying a zero-phase Butterworth low-pass filter at **120 Hz** (order 4). Zero-phase filtering prevents time-domain phase distortion and keeps peak measurements accurate.

### LFE-to-Main Ratio guidelines

| Ratio | Assessment |
|---|---|
| > −6 dB | LFE too loud |
| −8 to −12 dB | Ideal range |
| < −15 dB | LFE very quiet |

---

## Channel RMS relative to Center

Surround channels are high-pass filtered at **80 Hz** (Butterworth order 4, zero-phase) before measurement to remove low-frequency content that would skew the level comparison. The LFE channel uses the existing **120 Hz low-pass** filter.

Surround channels reported per layout:

| Layout | Channels reported |
|---|---|
| 5.1 | Ls (4), Rs (5) |
| 6.1 | Rc (4), Ls (5), Rs (6) |
| 7.1 | Lrs (4), Rrs (5), Ls (6), Rs (7) |

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

1. The entire audio is **downsampled to 16 kHz** immediately after reading. All metrics (K-weighted loudness, LRA, RMS, LFE loudness/crest, surround RMS, DC offset) are computed from this compact representation. The downsampled copy is ~3× smaller for a 48 kHz source.
2. The **original full-resolution array is immediately freed** after the downsampling step.
3. The **frequency-response plot** uses 16 kHz, so no large array is retained after analysis.

### Approximate peak RAM requirement

| Duration | Channels | Source SR | Peak RAM (approx.) |
|---|---|---|---|
| 10 min | stereo | 48 kHz | < 0.1 GB |
| 30 min | 5.1 | 48 kHz | ~0.3 GB |
| 2 h | 7.1 | 48 kHz | ~8 GB |
| 3 h | 7.1 | 48 kHz | ~12 GB |

Peak occurs during the downsampling step when the original full-resolution file and the 16 kHz copy briefly coexist. After that point RAM stays at around 1/3 of the raw-file size.

### Recommendation: analyse representative excerpts

For very long feature movie files it is still recommended to **extract a representative excerpt** before running the analysis — for example the middle 5–10 minutes of the movie. Loudness metrics (LUFS, LRA) and the frequency response are statistically stable over a few minutes of typical programme content.

Example using `ffmpeg` to extract 10 minutes starting at the 30-minute mark:

```bash
ffmpeg -ss 00:30:00 -t 00:10:00 -i movie.wav -c copy excerpt.wav
python AnalyzeDynamicRange.py excerpt.wav
```

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'soundfile'`

```bash
pip install numpy scipy soundfile
```

### `Not enough audio to plot a short-term loudness curve`

The file is too short (< ~4 s). The script needs enough blocks for meaningful short-term loudness analysis.

### Low LRA (< 5 LU) info

The file is heavily compressed. Normal for some content; check if intentional for dynamic material.

---

## Standards and References

- **ITU-R BS.1770-4**: Algorithms to measure audio programme loudness and true-peak audio level
- **EBU R128**: Loudness normalisation and permitted maximum level
- **EBU Tech 3342**: Loudness Range — a measure to supplement loudness normalisation
- **ATSC A/85**: Techniques for establishing and maintaining audio loudness for DTV

---

## License

See project license for details.
