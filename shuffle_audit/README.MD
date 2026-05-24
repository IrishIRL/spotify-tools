# Shuffle Audit

Scripts for collecting and analyzing Spotify shuffle behavior.

## Purpose

This project tests whether Spotify shuffle behavior differs from an independent uniform-random baseline over a static liked-song library.

The audit does not attempt to reverse engineer Spotify’s internal algorithm. Instead, it answers a narrower and statistically testable question:
> If every liked track had equal probability of being selected at every shuffle step, how likely would the observed playback history be?

The project focuses on:
- overplayed tracks
- underplayed tracks
- silenced tracks
- prioritized artists
- deprioritized artists
- metadata-level bias
- global library coverage and concentration

## Project structure
```
shuffle_audit/
├── README.md
├── spotify_shuffle_probe.py
├── spotify_export_shuffle_probe.py
├── spotify_shuffle_audit.py
```

## Files
### `spotify_shuffle_probe.py`
*Purpose*: Runs Spotify playback in a loop:
1. Reads the currently playing track
2. Stores track metadata in shuffle_probe.db
3. Skips to the next track
4. Repeats continuously

The script is designed for collecting a large shuffle sample from a static liked-song library.

Collected fields include:
- track ID
- track name
- artists
- album
- release date
- duration
- popularity
- explicit flag
- Spotify URL
- ISRC
- timestamp of observation

### `spotify_export_shuffle_probe.py`
*Purpose*: Exports `shuffle_probe.db` into: `shuffle_probe.csv`
The CSV is used as input for the statistical audit.

### `spotify_shuffle_audit.py`
*Purpose*: Performs the statistical audit.
The script compares: `liked_songs.csv` against: `shuffle_probe.csv` using exact probability tests.

Main outputs include:
- track-level bias reports
- artist-level bias reports
- metadata-group analysis
- sequence diagnostics
- global library coverage tests
- data quality diagnostics

## Statistical model

Primary null hypothesis:
> Each shuffle play is an independent uniform draw from the eligible liked-track set.

This is intentionally strict.

A true random permutation shuffle without replacement would generally produce more even coverage than this baseline, not less.

The audit uses:
- exact binomial tail probabilities
- occupancy probability tests
- exact confidence intervals
- multiple-testing correction

Supported multiple-testing corrections:
- Holm (default, strict familywise control)
- Bonferroni
- Benjamini–Hochberg (BH)
- Benjamini–Yekutieli (BY)

## Requirements

Python: `Python 3.11+`

Packages: `pip install pandas numpy scipy`

## Environment

Scripts expect: `../.env`

Required variables:
```
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
SPOTIFY_REDIRECT_URI=
OUTPUT_DIR=
```

## Workflow
### 1. Export liked songs
`python spotify_export_liked.py`. Produces: `liked_songs.csv`

### 2. Run shuffle probe
`python shuffle_audit/spotify_shuffle_probe.py`. 
The script continuously:
```
records current track
skips to next track
stores observations in SQLite
```

Database: `shuffle_probe.db`

Stop manually with: `Ctrl+C`

### 3. Export probe database
`python shuffle_audit/spotify_export_shuffle_probe.py`

Produces: `shuffle_probe.csv`

### 4. Run audit

Example:
```
python shuffle_audit/spotify_shuffle_audit.py \
  --liked liked_songs.csv \
  --shuffled shuffle_probe.csv \
  --out audit_results
```
Static-library audit:
```
python shuffle_audit/spotify_shuffle_audit.py \
  --liked liked_songs.csv \
  --shuffled shuffle_probe.csv \
  --out audit_results \
  --eligibility fixed_current
```
Strict frozen-window audit:
```
python shuffle_audit/spotify_shuffle_audit.py \
  --liked liked_songs.csv \
  --shuffled shuffle_probe.csv \
  --out audit_results \
  --eligibility frozen_after_last_like
```

## Audit output

The audit creates CSV reports for:

- overplayed tracks 
- underplayed tracks 
- silenced tracks 
- prioritized artists 
- deprioritized artists 
- global coverage tests 
- sequence tests 
- data quality checks

Main summary: `audit_results/summary.md`

## Example audit result

Example real-world audit using:
```
3,565 liked tracks
5,705 observed shuffle plays
```
Observed:

| Metric                         | Observed | Expected under uniform baseline |
|:-------------------------------|:--------:|--------------------------------:|
| Unique tracks played           |  1,283   |                         2,845.6 |
| Zero-play tracks               |  2,282   |                           719.4 |
| Mean plays among played tracks |  4.447   |                           2.005 |


Additional observations:
- many tracks repeated 7–10 times
- far fewer one-play tracks than expected
- dozens of one-song artists received 10 plays
- several medium-popularity groups were overrepresented
- very-low-popularity tracks were underrepresented

The exact occupancy test produced: `log10(p) ≈ -1529` under the independent uniform baseline.

Interpretation:
> The observed shuffle history was highly concentrated into a subset of the liked-song library and differed strongly from the tested uniform-random baseline.

## Known limitations
- The audit assumes the liked-song library remained static during the analyzed window unless explicitly using a frozen snapshot workflow.
- The project tests a specific null model, not Spotify’s undisclosed internal implementation.
- Duplicate observations caused by API lag may inflate play counts.
- Playlist context changes during collection can affect results.
- Exact occupancy calculations may become computationally expensive for extremely large datasets.