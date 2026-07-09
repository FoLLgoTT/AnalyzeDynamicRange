# AnalyzeDynamicRange.py

A Python application for analyzing the dynamic range and loudness of film audio files according to international broadcast standards.

## Overview

`AnalyzeDynamicRange.py` analyzes audio files and calculates professional loudness metrics according to the standards:
- **ITU-R BS.1770-4** (International Telecommunication Union)
- **EBU R128** (European Broadcasting Union)

These standards are internationally recognized and used in the film industry, streaming services, and broadcasting to standardize audio content consistently and technically correctly.

## Features

The script calculates the following metrics:

| Metric | Unit | Description |
|--------|------|-------------|
| **Integrated Loudness** | LUFS | Filtered overall loudness across the entire file with gating |
| **Loudness Range (LRA)** | LU | Dynamic range according to EBU Tech 3342 |
| **True Peak** | dBTP | Inter-sample peak at 4x oversampling (headroom control) |
| **DR Score** | integer | Crest-factor-based metric (higher = more dynamic) |
| **RMS Level** | dBFS | Overall RMS amplitude across all channels |
| **Momentary Loudness** | LUFS | Time series over 400 ms windows |
| **Short-term Loudness** | LUFS | Time series over 3-second windows |

### Special Features

- **K-Weighting Filter**: Natively implemented BS.1770-4 high-frequency weighting
- **Intelligent Gating**: 
  - Absolute gating at -70 LUFS
  - Relative gating 10 dB below the mean
- **Surround Channels**: Automatic +1.5 dB weighting
- **LFE Exclusion**: Subwoofer channel is automatically excluded from the loudness sum
- **4x Oversampling**: For correct true peak measurement (up to 96 kHz)

## Installation

### Required Packages

```bash
pip install numpy scipy soundfile
```

### Optional (for plotting)

```bash
pip install matplotlib
```

## Usage

### Basic Usage

```bash
python AnalyzeDynamicRange.py film.wav
```

Example output:
```
File         : film.wav
Sample rate  : 48000 Hz
Channels     : 6
Duration     : 120.5 s

Surround ch  : [5, 6] (weighted +1.5 dB)

=== Dynamic Range / Loudness ===
  Integrated loudness :   -23.45 LUFS
  Loudness range (LRA):    11.20 LU
  True peak           :    -3.50 dBTP
  DR score            :       12
  RMS level           :   -30.15 dBFS
  Momentary max       :   -18.30 LUFS
  Short-term max      :   -20.10 LUFS
  Short-term min      :   -32.50 LUFS
```

### Options

#### `--per-channel`
Shows loudness and true peak for each channel individually:

```bash
python AnalyzeDynamicRange.py film.wav --per-channel
```

```
=== Per-channel integrated loudness ===
  Channel  1:   -23.12 LUFS  true peak   -3.20 dBTP
  Channel  2:   -23.78 LUFS  true peak   -3.45 dBTP
  Channel  3:   -22.95 LUFS  true peak   -2.80 dBTP
  Channel  4:    -35.00 LUFS  true peak  -25.00 dBTP (LFE, excluded from sum)
  Channel  5:   -26.50 LUFS  true peak   -5.10 dBTP (surround)
  Channel  6:   -27.10 LUFS  true peak   -5.30 dBTP (surround)
```

#### `--layout`
Overrides automatic channel layout detection:

```bash
python AnalyzeDynamicRange.py film.wav --layout 5.1
python AnalyzeDynamicRange.py film.wav --layout 7.1
python AnalyzeDynamicRange.py film.wav --layout stereo
python AnalyzeDynamicRange.py film.wav --layout mono
```

#### `--lfe-channel`
Specifies the 0-based index of the LFE channel (excluded from loudness sum):

```bash
python AnalyzeDynamicRange.py film.wav --lfe-channel 3
```

#### `--exclude-surround`
Excludes surround channels (analyzes front/dialogue loudness only):

```bash
python AnalyzeDynamicRange.py film.wav --exclude-surround
```

Surround channels receive a weight of 0.0 instead of +1.5 dB.

#### `--plot`
Creates a plot of the short-term loudness curve:

```bash
python AnalyzeDynamicRange.py film.wav --plot loudness_plot.png
```

The plot displays:
- Blue curve: Short-term loudness (3-second windows)
- Red dashed line: Integrated loudness reference
- Title with LRA and true peak information

**Note:** Requires `matplotlib`. Install with: `pip install matplotlib`

## Channel Layouts

The script supports multiple channel layouts. The default order follows SMPTE/ITU standard:

### Automatic Detection (Standard)

- **Mono** (1 channel): `[C]`
- **Stereo** (2 channels): `[L, R]`
- **5.1** (6 channels): `[L, R, C, LFE, Ls, Rs]`
- **7.1** (8 channels): `[L, R, C, LFE, Ls, Rs, Lrs, Rrs]`
- **Other**: Channels after position 3 are treated as surround

### Channel Weighting

- **Normal channels** (L, R, C): Weight 1.0
- **LFE** (Subwoofer): Weight 0.0 (excluded)
- **Surround** (Ls, Rs, etc.): Weight 1.41 (+1.5 dB)

## Measurement Standards Explained

### Integrated Loudness (LUFS)
The average loudness across the entire file with intelligent silence detection.

**Broadcast Standards:**
- **Film**: -23 LUFS (DCI, cinema)
- **TV/Streaming**: -16 to -14 LUFS
- **Podcasts**: -14 to -16 LUFS

### Loudness Range (LRA, LU)
Measures the dynamics/variation of loudness over time.

**Interpretation:**
- LRA < 5 LU: Heavily compressed (music videos, action)
- LRA 5-10 LU: Moderately compressed
- LRA > 10 LU: Dynamic/uncompressed (classical, drama)

### True Peak (dBTP)
The highest peak values measured with 4x oversampling. These can exceed 0 dBFS.

**Safety Margins:**
- **-1 dBTP**: Standard for digital broadcasting
- **-3 dBTP**: Conservative safety margin
- **> 0 dBTP**: Risk of clipping/distortion

### DR Score
Crest factor of the loudest 20% of audio blocks.

**Typical Values:**
- DR <= 6: Heavily compressed music
- DR 7-10: Modern pop/rock
- DR 11-15: Dynamic content
- DR > 15: Very dynamic (classical, orchestral)

## Programming Interface

### `analyze()` Function

```python
result = analyze(
    path,                    # Path to audio file
    layout=None,            # "mono", "stereo", "5.1", "7.1" or None
    lfe_channel=None,       # 0-based index or None
    per_channel=False,      # Per-channel metrics
    exclude_surround=False  # Exclude surround channels
)
```

**Return Value:** Dictionary containing:
- `sr`: Sample rate
- `n_channels`: Number of channels
- `integrated_lufs`: Overall loudness
- `lra_lu`: Loudness dynamic range
- `true_peak_dbtp`: Highest true peak
- `dr_score`: DR score
- `rms_dbfs`: RMS level
- `short_term`: Time series (3-second windows)
- `momentary`: Time series (400 ms windows)
- `step_s`: Window step size

### Example (Programmatic Usage)

```python
from AnalyzeDynamicRange import analyze

result = analyze("my_film.wav", layout="5.1")
print(f"Loudness: {result['integrated_lufs']:.2f} LUFS")
print(f"LRA: {result['lra_lu']:.2f} LU")
print(f"True Peak: {result['true_peak_dbtp']:.2f} dBTP")
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `numpy` | Numerical computations, array operations |
| `scipy` | Signal processing (filters, resampling) |
| `soundfile` | Audio file reading |
| `matplotlib` | Optional: plot rendering |

## Supported Formats

The script supports all formats supported by `soundfile`:
- WAV
- FLAC
- OGG
- AIFF
- and more

Typically works with 16-bit or 24-bit PCM.

## Troubleshooting

### "ModuleNotFoundError: No module named 'soundfile'"

Install the required packages:
```bash
pip install numpy scipy soundfile
```

### "Not enough audio to plot a short-term loudness curve"

The audio file is too short (less than ~4 seconds). The script needs sufficient audio for meaningful short-term blocks.

### True Peak exceeds -1 dBTP Warning

The audio file exceeds the safe headroom level. Reduce the volume to avoid clipping/distortion.

### Low LRA (< 5 LU) Info

The file is heavily compressed. This is normal for certain content but may be undesirable for dynamic material.

## Examples

### Analyze a 5.1 film mix with plot

```bash
python AnalyzeDynamicRange.py film_final.wav --layout 5.1 --plot loudness_curve.png
```

### Front-channel only analysis

```bash
python AnalyzeDynamicRange.py film_final.wav --exclude-surround
```

### Detailed per-channel analysis

```bash
python AnalyzeDynamicRange.py film_final.wav --per-channel
```

## Limitations

- Analysis assumes standard channel arrangement (SMPTE/ITU order)
- For non-standard layouts, use `--layout` and `--lfe-channel`
- True peak measurement uses bilinear interpolation upsampler
- Extremely short files (< 100 ms) may produce invalid results

## Standards and References

- **ITU-R BS.1770-4**: "Algorithms to measure audio programme loudness and true-peak audio level"
- **EBU R128**: "Loudness normalisation and permitted maximum level"
- **EBU Tech 3342**: "Loudness Range: A measure to supplement loudness normalisation in accordance with EBU R128"
- **ATSC A/85**: "Techniques for Establishing and Maintaining Audio Loudness for Digital Television (DTV)"

## License

See project license for details.

## Authors/Contact

For questions or issues with this script, please consult the project documentation.
