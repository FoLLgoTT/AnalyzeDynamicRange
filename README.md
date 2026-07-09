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

| Metric | Unit | Description |
|--------|------|-------------|
| **Integrated Loudness** | LUFS | Gated programme loudness across the entire file |
| **Loudness Range (LRA)** | LU | Dynamic range per [EBU Tech 3342](https://tech.ebu.ch/docs/tech/tech3342.pdf) |
| **RMS Level** | dBFS | Overall RMS across all channels |
| **Momentary Loudness** | LUFS | Time series over 400 ms windows |
| **Short-term Loudness** | LUFS | Time series over 3-second windows |

### LFE Band Analysis

The LFE channel is divided into four frequency bands. All level metrics are expressed **relative to the main-mix integrated loudness** so results are comparable across films with different overall levels. Statistics are computed only over LFE-active windows (short-term RMS ≥ integrated − 15 dB) to prevent long silent passages from distorting the results. A low pass at 120 Hz is applied before LFE analysis to remove unwanted high frequency content (clipping etc.).

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

### Frequency response
Low-end frequency response is shown for Center, Left and LFE channels. In this analysis high pass filtering can be observed.

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

Text box showing the RMS level of each channel relative to the unfiltered Center channel.

### Panel 5 — LFE Band Analysis

Text box showing the LFE band analysis: overall activity (% of runtime), per-band activity / P95 / Peak levels (relative to main integrated loudness), sub-bass ratio, and spectral centroid.

### Panel 6 — Summary

Text box with a concise qualitative assessment derived from the numeric results:

| Field | Source metric | Labels |
|---|---|---|
| **Loudness range** | LRA (LU) | `heavily compressed` · `compressed` · `moderate` · `dynamic` · `high dynamic` · `extreme dynamic` |
| **LFE activity** | LFE active (% of runtime) | `restrained` · `moderate` · `active` · `very active` · `overused` |
| **LFE depth** | Sub-bass ratio (dB) | `upper-bass` · `moderate` · `deep` · `seismic` |

The thresholds for each label are documented in the sections below.

### Panel 7 — Frequency Response

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

| LRA | Label |
|---|---|
| > 35 LU | `extreme dynamic` |
| > 30 LU | `high dynamic` |
| > 25 LU | `dynamic` |
| > 20 LU | `moderate` |
| > 15 LU | `compressed` |
| ≤ 15 LU | `heavily compressed` |

---

## LFE Channel Analysis

The LFE channel is analyzed **after** applying a zero-phase Butterworth low-pass filter at **120 Hz** (order 4). Zero-phase filtering prevents time-domain phase distortion and keeps peak measurements accurate.

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

1. The entire audio is **downsampled to 16 kHz** immediately after reading. All metrics (K-weighted loudness, LRA, RMS, LFE loudness/crest, surround RMS) are computed from this compact representation. The downsampled copy is ~3× smaller for a 48 kHz source.
2. The **original full-resolution array is immediately freed** after the downsampling step.
3. The **frequency-response plot** uses 16 kHz, so no large array is retained after analysis.

---

## License

See project license for details.
