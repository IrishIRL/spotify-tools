#!/usr/bin/env python3
"""
Spotify liked-songs shuffle audit.

Null model used by the exact tests:
    In the analysed window, every play is an independent uniform draw from the
    eligible liked-track set.

This is intentionally a conservative model for detecting count bias. A true
    random permutation shuffle without replacement would usually be more even
    than this model, not less even, over long continuous listening windows.

Outputs:
    summary.md
    track_all.csv
    track_overplayed.csv
    track_underplayed.csv
    track_silenced.csv
    artist_all.csv
    artist_prioritized.csv
    artist_deprioritized.csv
    group_bias.csv
    sequence_tests.csv
    global_coverage_tests.csv
    track_count_distribution.csv
    track_discovery_overplayed_bh.csv
    track_discovery_underplayed_bh.csv
    artist_discovery_prioritized_bh.csv
    artist_discovery_deprioritized_bh.csv
    data_quality.csv

Dependencies:
    pandas, numpy, scipy

Example:
    python spotify_shuffle_audit.py --liked liked.csv --shuffled shuffled.csv --out audit_results

Strict frozen-window example:
    python spotify_shuffle_audit.py --liked liked.csv --shuffled shuffled.csv --out audit_results \
      --eligibility frozen_after_last_like
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy.stats import beta, binom


# ----------------------------- configuration ----------------------------- #

SPOTIFY_TRACK_RE = re.compile(r"(?:track[:/])([A-Za-z0-9]{22})")


# ------------------------------ small utils ------------------------------- #

def die(message: str, code: int = 2) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def ensure_outdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_csv_auto(path: Path) -> pd.DataFrame:
    """Read CSV/TSV with delimiter sniffing and forgiving encodings."""
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "latin1"):
        try:
            return pd.read_csv(path, sep=None, engine="python", encoding=encoding, dtype=str)
        except UnicodeDecodeError:
            continue
        except Exception as exc:
            last_exc = exc
    die(f"Could not read {path}. Last error: {last_exc}")


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [
        str(c).strip().lower().replace(" ", "_").replace("-", "_")
        for c in df.columns
    ]
    return df


def first_present(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def parse_spotify_track_id(value: object) -> Optional[str]:
    if pd.isna(value):
        return None
    s = str(value).strip()
    if not s:
        return None
    if len(s) == 22 and re.fullmatch(r"[A-Za-z0-9]{22}", s):
        return s
    match = SPOTIFY_TRACK_RE.search(s)
    return match.group(1) if match else None


def canonical_key(df: pd.DataFrame) -> Tuple[pd.Series, str]:
    """
    Return a stable track key and the key source.
    Priority: track_id, parsed spotify_url, isrc, composite name+artists+duration.
    """
    if "track_id" in df.columns:
        key = df["track_id"].map(parse_spotify_track_id)
        if key.notna().sum() > 0:
            return key.astype("string"), "track_id"

    if "spotify_url" in df.columns:
        key = df["spotify_url"].map(parse_spotify_track_id)
        if key.notna().sum() > 0:
            return key.astype("string"), "spotify_url_track_id"

    if "isrc" in df.columns:
        key = df["isrc"].astype("string").str.strip().str.upper().replace({"": pd.NA})
        if key.notna().sum() > 0:
            return key, "isrc"

    needed = {"track_name", "artists"}
    if needed.issubset(df.columns):
        duration = df["duration_ms"].astype("string") if "duration_ms" in df.columns else ""
        key = (
            df["track_name"].astype("string").str.strip().str.lower()
            + "||"
            + df["artists"].astype("string").str.strip().str.lower()
            + "||"
            + duration.astype("string").str.strip()
        )
        key = key.replace({"<NA>||<NA>||<NA>": pd.NA})
        return key, "track_name_artists_duration"

    die(
        "No usable track identifier found. Provide track_id, spotify_url, isrc, "
        "or at least track_name + artists."
    )


def to_datetime_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def to_boolish(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
        .map({"true": True, "false": False, "1": True, "0": False, "yes": True, "no": False})
    )


def safe_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def split_artists(value: object) -> List[str]:
    """Parse a simple Spotify-export artists field."""
    if pd.isna(value):
        return []
    s = str(value).strip()
    if not s:
        return []

    # Many exports use comma+space; some use semicolon or pipes. Keep "AC/DC" intact.
    if ";" in s:
        parts = s.split(";")
    elif " | " in s:
        parts = s.split(" | ")
    elif " / " in s:
        parts = s.split(" / ")
    else:
        parts = s.split(",")

    out = []
    seen = set()
    for p in parts:
        name = re.sub(r"\s+", " ", p.strip())
        if name and name.lower() not in seen:
            out.append(name)
            seen.add(name.lower())
    return out


def neglog10(p: float) -> float:
    if p <= 0:
        return float("inf")
    return -math.log10(p)


def fmt_float(x: float, digits: int = 6) -> str:
    if pd.isna(x):
        return "NA"
    if x == float("inf"):
        return "inf"
    return f"{x:.{digits}g}"


# ------------------------- exact probability tools ------------------------ #

def adjust_pvalues(pvalues: Sequence[float], method: str = "holm") -> np.ndarray:
    """Multiple-testing correction. Holm is the strict default."""
    p = np.asarray(pvalues, dtype=float)
    p = np.where(np.isnan(p), 1.0, p)
    m = len(p)
    if m == 0:
        return np.array([], dtype=float)

    method = method.lower()
    if method in {"none", "raw"}:
        return np.clip(p, 0, 1)

    order = np.argsort(p)
    ranked = p[order]
    out_sorted = np.empty(m, dtype=float)

    if method == "bonferroni":
        out = np.minimum(p * m, 1.0)
        return out

    if method == "holm":
        # Holm step-down adjusted p-values.
        factors = m - np.arange(m)
        raw = ranked * factors
        out_sorted = np.maximum.accumulate(raw)
        out_sorted = np.minimum(out_sorted, 1.0)
    elif method == "bh":
        # Benjamini-Hochberg FDR adjusted p-values.
        factors = m / np.arange(1, m + 1)
        raw = ranked * factors
        out_sorted = np.minimum.accumulate(raw[::-1])[::-1]
        out_sorted = np.minimum(out_sorted, 1.0)
    elif method == "by":
        # Benjamini-Yekutieli FDR under arbitrary dependence.
        c_m = np.sum(1.0 / np.arange(1, m + 1))
        factors = m * c_m / np.arange(1, m + 1)
        raw = ranked * factors
        out_sorted = np.minimum.accumulate(raw[::-1])[::-1]
        out_sorted = np.minimum(out_sorted, 1.0)
    else:
        die(f"Unknown p-value correction: {method}")

    out = np.empty(m, dtype=float)
    out[order] = out_sorted
    return np.clip(out, 0, 1)


def binomial_tail_tests(k: np.ndarray, n: int, p0: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Exact one-sided binomial p-values for over- and under-representation."""
    k = np.asarray(k, dtype=int)
    p0 = np.asarray(p0, dtype=float)
    p0 = np.clip(p0, 0.0, 1.0)

    p_over = np.ones_like(p0, dtype=float)
    p_under = np.ones_like(p0, dtype=float)

    valid = (n >= 0) & (p0 >= 0) & (p0 <= 1)
    if n == 0:
        return p_over, p_under

    # P[X >= k] = SF(k-1). For k=0 this is 1.
    idx = valid & (k > 0)
    p_over[idx] = binom.sf(k[idx] - 1, n, p0[idx])

    idx = valid
    p_under[idx] = binom.cdf(k[idx], n, p0[idx])
    return np.clip(p_over, 0, 1), np.clip(p_under, 0, 1)


def clopper_pearson(k: np.ndarray, n: int, alpha: float) -> Tuple[np.ndarray, np.ndarray]:
    """Exact two-sided Clopper-Pearson confidence interval for binomial p."""
    k = np.asarray(k, dtype=int)
    lo = np.zeros_like(k, dtype=float)
    hi = np.ones_like(k, dtype=float)

    if n <= 0:
        lo[:] = np.nan
        hi[:] = np.nan
        return lo, hi

    nonzero = k > 0
    not_all = k < n
    lo[nonzero] = beta.ppf(alpha / 2.0, k[nonzero], n - k[nonzero] + 1)
    hi[not_all] = beta.ppf(1.0 - alpha / 2.0, k[not_all] + 1, n - k[not_all])
    return lo, hi


def combined_directional_adjust(
    p_over: Sequence[float],
    p_under: Sequence[float],
    method: str,
) -> Tuple[np.ndarray, np.ndarray]:
    """Correct over+under directional tests as one family."""
    p = np.concatenate([np.asarray(p_over, dtype=float), np.asarray(p_under, dtype=float)])
    q = adjust_pvalues(p, method=method)
    m = len(p_over)
    return q[:m], q[m:]


# ------------------------------- data model ------------------------------- #

@dataclass
class PreparedData:
    liked: pd.DataFrame
    shuffled: pd.DataFrame
    key_source_liked: str
    key_source_shuffled: str
    warnings: List[str]
    data_quality: List[Dict[str, object]]


# ----------------------------- preparation -------------------------------- #

def prepare_data(
    liked_path: Path,
    shuffled_path: Path,
    window_start: Optional[str],
    window_end: Optional[str],
    eligibility: str,
) -> PreparedData:
    warnings: List[str] = []
    quality: List[Dict[str, object]] = []

    liked = normalize_columns(read_csv_auto(liked_path))
    shuffled = normalize_columns(read_csv_auto(shuffled_path))

    liked_key, liked_key_source = canonical_key(liked)
    shuffled_key, shuffled_key_source = canonical_key(shuffled)
    liked["_key"] = liked_key
    shuffled["_key"] = shuffled_key

    liked_missing_key = int(liked["_key"].isna().sum())
    shuffled_missing_key = int(shuffled["_key"].isna().sum())
    quality += [
        {"check": "liked_rows", "value": len(liked)},
        {"check": "shuffled_rows", "value": len(shuffled)},
        {"check": "liked_key_source", "value": liked_key_source},
        {"check": "shuffled_key_source", "value": shuffled_key_source},
        {"check": "liked_missing_key_rows", "value": liked_missing_key},
        {"check": "shuffled_missing_key_rows", "value": shuffled_missing_key},
    ]

    liked = liked.dropna(subset=["_key"]).copy()
    shuffled = shuffled.dropna(subset=["_key"]).copy()

    duplicate_liked_keys = int(liked["_key"].duplicated().sum())
    quality.append({"check": "liked_duplicate_key_rows_removed", "value": duplicate_liked_keys})
    if duplicate_liked_keys:
        warnings.append(
            f"Removed {duplicate_liked_keys} duplicate liked rows by key. "
            "If these are different recordings with the same ISRC/composite key, use track_id instead."
        )
        liked = liked.drop_duplicates("_key", keep="first").copy()

    if "observed_at" in shuffled.columns:
        shuffled["observed_at"] = to_datetime_utc(shuffled["observed_at"])
        bad_observed = int(shuffled["observed_at"].isna().sum())
        quality.append({"check": "shuffled_bad_observed_at_rows", "value": bad_observed})
        if bad_observed:
            warnings.append(f"{bad_observed} shuffled rows have invalid observed_at.")
    else:
        warnings.append("shuffled CSV has no observed_at column; sequence/time-window checks are limited.")

    if "added_at" in liked.columns:
        liked["added_at"] = to_datetime_utc(liked["added_at"])
        bad_added = int(liked["added_at"].isna().sum())
        quality.append({"check": "liked_bad_added_at_rows", "value": bad_added})
        if bad_added:
            warnings.append(f"{bad_added} liked rows have invalid added_at.")
    else:
        warnings.append("liked CSV has no added_at column; cannot automatically verify a frozen liked-set window.")

    if window_start and "observed_at" in shuffled.columns:
        start = pd.Timestamp(window_start, tz="UTC")
        before = len(shuffled)
        shuffled = shuffled[shuffled["observed_at"] >= start].copy()
        quality.append({"check": "window_start_rows_removed", "value": before - len(shuffled)})

    if window_end and "observed_at" in shuffled.columns:
        end = pd.Timestamp(window_end, tz="UTC")
        before = len(shuffled)
        shuffled = shuffled[shuffled["observed_at"] <= end].copy()
        quality.append({"check": "window_end_rows_removed", "value": before - len(shuffled)})

    eligibility = eligibility.lower()
    if eligibility not in {"fixed_current", "frozen_after_last_like"}:
        die("eligibility must be fixed_current or frozen_after_last_like")

    if eligibility == "frozen_after_last_like":
        if "added_at" not in liked.columns:
            die("eligibility=frozen_after_last_like requires added_at in the liked CSV")
        if "observed_at" not in shuffled.columns:
            die("eligibility=frozen_after_last_like requires observed_at in the shuffled CSV")
        last_added = liked["added_at"].max()
        if pd.isna(last_added):
            die("Could not compute max(added_at); use fixed_current or fix liked added_at values")
        before = len(shuffled)
        shuffled = shuffled[shuffled["observed_at"] >= last_added].copy()
        quality.append({"check": "frozen_after_last_like_start_utc", "value": str(last_added)})
        quality.append({"check": "frozen_after_last_like_rows_removed", "value": before - len(shuffled)})
        if len(shuffled) == 0:
            die(
                "No shuffled rows remain after max(added_at). For an exact audit, collect plays after "
                "freezing the liked list, or re-run with --eligibility fixed_current if you are sure the "
                "current liked CSV is the eligible set for the whole analysed window."
            )
    elif "added_at" in liked.columns and "observed_at" in shuffled.columns:
        min_obs = shuffled["observed_at"].min()
        max_added = liked["added_at"].max()
        if pd.notna(min_obs) and pd.notna(max_added) and max_added > min_obs:
            warnings.append(
                "Some liked tracks were added after the first observed play. fixed_current assumes all liked "
                "tracks were eligible throughout the analysed window. For a stricter exact audit, use "
                "--eligibility frozen_after_last_like or provide a liked snapshot from the start of the test."
            )

    # Keep only shuffled plays that match the liked set for primary probability tests.
    liked_keys = set(liked["_key"].astype(str))
    shuffled["_in_liked"] = shuffled["_key"].astype(str).isin(liked_keys)
    outside = int((~shuffled["_in_liked"]).sum())
    quality.append({"check": "shuffled_rows_not_in_liked_set", "value": outside})
    if outside:
        warnings.append(
            f"{outside} shuffled rows are not in the liked CSV and are excluded from uniform liked-set tests. "
            "This can happen if songs were unliked, relinked, local files changed, or the capture included other contexts."
        )
    shuffled = shuffled[shuffled["_in_liked"]].copy()

    if "observed_at" in shuffled.columns:
        shuffled = shuffled.sort_values("observed_at", kind="mergesort").reset_index(drop=True)
    else:
        shuffled = shuffled.reset_index(drop=True)

    liked = liked.reset_index(drop=True)
    quality += [
        {"check": "liked_unique_tracks_used", "value": len(liked)},
        {"check": "shuffled_liked_plays_used", "value": len(shuffled)},
    ]

    if len(liked) == 0:
        die("No liked tracks remain after cleaning.")
    if len(shuffled) == 0:
        die("No shuffled plays remain after cleaning and matching to liked tracks.")

    return PreparedData(liked, shuffled, liked_key_source, shuffled_key_source, warnings, quality)


# ------------------------------- analyses --------------------------------- #

def track_analysis(
    liked: pd.DataFrame,
    shuffled: pd.DataFrame,
    alpha: float,
    correction: str,
) -> pd.DataFrame:
    n_tracks = len(liked)
    n_plays = len(shuffled)
    p0 = 1.0 / n_tracks
    expected = n_plays * p0

    counts = shuffled["_key"].value_counts()
    out = liked.copy()
    out["observed_count"] = out["_key"].map(counts).fillna(0).astype(int)
    out["expected_count"] = expected
    out["observed_minus_expected"] = out["observed_count"] - expected
    out["play_rate"] = out["observed_count"] / n_plays
    out["expected_rate"] = p0
    out["count_ratio"] = np.where(expected > 0, out["observed_count"] / expected, np.nan)

    p_over, p_under = binomial_tail_tests(out["observed_count"].to_numpy(), n_plays, np.full(n_tracks, p0))
    out["p_over_exact"] = p_over
    out["p_under_exact"] = p_under
    out["neglog10_p_over"] = [neglog10(x) for x in p_over]
    out["neglog10_p_under"] = [neglog10(x) for x in p_under]
    q_over, q_under = combined_directional_adjust(p_over, p_under, correction)
    out[f"q_over_{correction}"] = q_over
    out[f"q_under_{correction}"] = q_under

    # Familywise Bonferroni exact confidence intervals for all track probabilities.
    # Two-sided CI family: all tracks at once. This is stricter than individual 95% CIs.
    ci_alpha = alpha / max(n_tracks, 1)
    ci_lo, ci_hi = clopper_pearson(out["observed_count"].to_numpy(), n_plays, ci_alpha)
    out[f"fw_{100*(1-alpha):.1f}_ci_low_rate"] = ci_lo
    out[f"fw_{100*(1-alpha):.1f}_ci_high_rate"] = ci_hi
    out["fw_ci_low_ratio_vs_uniform"] = ci_lo / p0
    out["fw_ci_high_ratio_vs_uniform"] = ci_hi / p0

    over_flag = out[f"q_over_{correction}"] <= alpha
    under_flag = out[f"q_under_{correction}"] <= alpha
    out["audit_status"] = np.select(
        [over_flag, under_flag & (out["observed_count"] == 0), under_flag],
        ["over_played", "silenced_significant", "under_played"],
        default="not_significant",
    )

    # Choose human-useful columns first, preserving all original metadata later.
    preferred = [
        "audit_status",
        "observed_count",
        "expected_count",
        "observed_minus_expected",
        "count_ratio",
        "p_over_exact",
        f"q_over_{correction}",
        "p_under_exact",
        f"q_under_{correction}",
        "fw_ci_low_ratio_vs_uniform",
        "fw_ci_high_ratio_vs_uniform",
        "track_name",
        "artists",
        "album",
        "release_date",
        "duration_ms",
        "popularity",
        "explicit",
        "spotify_url",
        "isrc",
        "track_id",
        "_key",
    ]
    cols = [c for c in preferred if c in out.columns] + [c for c in out.columns if c not in preferred]
    return out[cols].sort_values(
        [f"q_over_{correction}", "count_ratio", "observed_count"],
        ascending=[True, False, False],
        kind="mergesort",
    )


def artist_analysis(
    liked: pd.DataFrame,
    shuffled: pd.DataFrame,
    alpha: float,
    correction: str,
    min_liked_tracks: int,
) -> pd.DataFrame:
    if "artists" not in liked.columns:
        return pd.DataFrame()

    n_tracks = len(liked)
    n_plays = len(shuffled)

    artist_rows = []
    key_to_artists: Dict[str, List[str]] = {}
    for _, row in liked.iterrows():
        key = str(row["_key"])
        artists = split_artists(row.get("artists"))
        key_to_artists[key] = artists
        for artist in artists:
            artist_rows.append({"artist": artist, "_key": key})

    if not artist_rows:
        return pd.DataFrame()

    artist_track = pd.DataFrame(artist_rows).drop_duplicates(["artist", "_key"])
    liked_counts = artist_track.groupby("artist")["_key"].nunique().rename("liked_track_count")

    # One membership hit per play per artist. A collab track counts for every listed artist.
    play_artist_rows = []
    for key in shuffled["_key"].astype(str):
        for artist in key_to_artists.get(key, []):
            play_artist_rows.append({"artist": artist})
    if play_artist_rows:
        play_counts = pd.DataFrame(play_artist_rows).value_counts("artist").rename("observed_count")
    else:
        play_counts = pd.Series(dtype=int, name="observed_count")

    out = liked_counts.to_frame().join(play_counts, how="left").fillna({"observed_count": 0})
    out["observed_count"] = out["observed_count"].astype(int)
    out = out[out["liked_track_count"] >= min_liked_tracks].copy()
    if out.empty:
        return out.reset_index()

    out["expected_probability"] = out["liked_track_count"] / n_tracks
    out["expected_count"] = n_plays * out["expected_probability"]
    out["observed_minus_expected"] = out["observed_count"] - out["expected_count"]
    out["count_ratio"] = np.where(out["expected_count"] > 0, out["observed_count"] / out["expected_count"], np.nan)

    p_over, p_under = binomial_tail_tests(
        out["observed_count"].to_numpy(),
        n_plays,
        out["expected_probability"].to_numpy(),
    )
    out["p_over_exact"] = p_over
    out["p_under_exact"] = p_under
    q_over, q_under = combined_directional_adjust(p_over, p_under, correction)
    out[f"q_over_{correction}"] = q_over
    out[f"q_under_{correction}"] = q_under
    out["audit_status"] = np.select(
        [out[f"q_over_{correction}"] <= alpha, out[f"q_under_{correction}"] <= alpha],
        ["prioritized", "deprioritized"],
        default="not_significant",
    )

    ci_alpha = alpha / max(len(out), 1)
    lo, hi = clopper_pearson(out["observed_count"].to_numpy(), n_plays, ci_alpha)
    out[f"fw_{100*(1-alpha):.1f}_ci_low_probability"] = lo
    out[f"fw_{100*(1-alpha):.1f}_ci_high_probability"] = hi
    out["fw_ci_low_ratio_vs_uniform"] = lo / out["expected_probability"]
    out["fw_ci_high_ratio_vs_uniform"] = hi / out["expected_probability"]

    out = out.reset_index()
    return out.sort_values(
        [f"q_over_{correction}", "count_ratio", "observed_count"],
        ascending=[True, False, False],
        kind="mergesort",
    )


def add_group_columns(liked: pd.DataFrame) -> pd.DataFrame:
    liked = liked.copy()

    if "explicit" in liked.columns:
        liked["group_explicit"] = to_boolish(liked["explicit"]).astype("string").fillna("unknown")

    if "popularity" in liked.columns:
        popularity = safe_num(liked["popularity"])
        bins = [-0.1, 20, 40, 60, 80, 100]
        labels = ["000-020", "021-040", "041-060", "061-080", "081-100"]
        liked["group_popularity"] = pd.cut(popularity, bins=bins, labels=labels).astype("string").fillna("unknown")

    if "duration_ms" in liked.columns:
        minutes = safe_num(liked["duration_ms"]) / 60000.0
        bins = [-0.01, 2, 3, 4, 5, 7, 10, np.inf]
        labels = ["00-02m", "02-03m", "03-04m", "04-05m", "05-07m", "07-10m", "10m+"]
        liked["group_duration"] = pd.cut(minutes, bins=bins, labels=labels).astype("string").fillna("unknown")

    if "release_date" in liked.columns:
        # Spotify release_date can be YYYY, YYYY-MM, or YYYY-MM-DD.
        year = liked["release_date"].astype("string").str.extract(r"(\d{4})", expand=False).astype("float")
        decade = (np.floor(year / 10) * 10).astype("Int64").astype("string") + "s"
        liked["group_release_decade"] = decade.fillna("unknown")

    if "album" in liked.columns:
        liked["group_album"] = liked["album"].astype("string").str.strip().fillna("unknown")

    return liked


def generic_group_analysis(
    liked: pd.DataFrame,
    shuffled: pd.DataFrame,
    alpha: float,
    correction: str,
    min_group_tracks: int,
    include_album: bool,
) -> pd.DataFrame:
    liked_g = add_group_columns(liked)
    group_cols = [c for c in liked_g.columns if c.startswith("group_")]
    if not include_album:
        group_cols = [c for c in group_cols if c != "group_album"]
    if not group_cols:
        return pd.DataFrame()

    n_tracks = len(liked_g)
    n_plays = len(shuffled)
    play_meta = shuffled[["_key"]].merge(liked_g[["_key"] + group_cols], on="_key", how="left")

    all_rows = []
    for col in group_cols:
        group_name = col.replace("group_", "")
        liked_counts = liked_g.groupby(col, dropna=False)["_key"].nunique().rename("liked_track_count")
        play_counts = play_meta.groupby(col, dropna=False)["_key"].count().rename("observed_count")
        out = liked_counts.to_frame().join(play_counts, how="left").fillna({"observed_count": 0})
        out["observed_count"] = out["observed_count"].astype(int)
        out = out[out["liked_track_count"] >= min_group_tracks].copy()
        if out.empty:
            continue
        out["group_type"] = group_name
        out["group_value"] = out.index.astype(str)
        all_rows.append(out.reset_index(drop=True))

    if not all_rows:
        return pd.DataFrame()

    res = pd.concat(all_rows, ignore_index=True)
    res["expected_probability"] = res["liked_track_count"] / n_tracks
    res["expected_count"] = n_plays * res["expected_probability"]
    res["observed_minus_expected"] = res["observed_count"] - res["expected_count"]
    res["count_ratio"] = np.where(res["expected_count"] > 0, res["observed_count"] / res["expected_count"], np.nan)

    # Correct all group-direction tests together.
    p_over, p_under = binomial_tail_tests(
        res["observed_count"].to_numpy(),
        n_plays,
        res["expected_probability"].to_numpy(),
    )
    res["p_over_exact"] = p_over
    res["p_under_exact"] = p_under
    q_over, q_under = combined_directional_adjust(p_over, p_under, correction)
    res[f"q_over_{correction}"] = q_over
    res[f"q_under_{correction}"] = q_under
    res["audit_status"] = np.select(
        [res[f"q_over_{correction}"] <= alpha, res[f"q_under_{correction}"] <= alpha],
        ["over_represented", "under_represented"],
        default="not_significant",
    )

    return res.sort_values(
        ["group_type", f"q_over_{correction}", f"q_under_{correction}", "count_ratio"],
        ascending=[True, True, True, False],
        kind="mergesort",
    )


def sequence_tests(liked: pd.DataFrame, shuffled: pd.DataFrame, alpha: float) -> pd.DataFrame:
    """
    Exact tests on simple sequence properties under iid uniform draws.
    These are diagnostic, not a replacement for track/artist count tests.
    """
    rows = []
    n_tracks = len(liked)
    n_plays = len(shuffled)
    if n_plays < 2:
        return pd.DataFrame(rows)

    keys = shuffled["_key"].astype(str).to_numpy()
    transitions = n_plays - 1

    immediate_repeats = int(np.sum(keys[1:] == keys[:-1]))
    p_repeat = 1.0 / n_tracks
    p_over = binom.sf(immediate_repeats - 1, transitions, p_repeat) if immediate_repeats > 0 else 1.0
    p_under = binom.cdf(immediate_repeats, transitions, p_repeat)
    rows.append(
        {
            "test": "immediate_same_track_repeats",
            "observed": immediate_repeats,
            "expected": transitions * p_repeat,
            "p_over_exact": p_over,
            "p_under_exact": p_under,
            "interpretation": "Too many immediate repeats can indicate non-random replay; too few can indicate no-repeat shuffle-bag behavior.",
        }
    )

    return pd.DataFrame(rows)


def poisson_binomial_tail(probs: Sequence[float], observed: int) -> Tuple[float, float]:
    """Exact Poisson-binomial lower and upper tail for one observed count."""
    probs_arr = np.asarray(probs, dtype=float)
    probs_arr = np.clip(probs_arr, 0, 1)
    n = len(probs_arr)
    if n == 0:
        return 1.0, 1.0

    # DP only up to observed for lower tail and observed-1 for upper complement.
    # For simplicity and stability, compute full distribution if moderate; otherwise truncated.
    # Full DP is O(n^2), too large for very long histories. Truncated O(n*observed).
    cap = max(observed, 0)
    dp = np.zeros(cap + 1, dtype=float)
    dp[0] = 1.0
    for p in probs_arr:
        upper = min(cap, n)
        if cap > 0:
            dp[1:] = dp[1:] * (1 - p) + dp[:-1] * p
        dp[0] *= 1 - p
    lower = float(np.sum(dp[: observed + 1])) if observed <= cap else 1.0

    if observed <= 0:
        upper_tail = 1.0
    else:
        cdf_before = float(np.sum(dp[:observed]))
        upper_tail = max(0.0, min(1.0, 1.0 - cdf_before))
    return max(0.0, min(1.0, lower)), upper_tail


def logsumexp_np(values: np.ndarray) -> float:
    """Small local log-sum-exp helper to avoid probability underflow."""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return float("-inf")
    m = float(np.max(arr))
    if not np.isfinite(m):
        return m
    return float(m + math.log(float(np.sum(np.exp(arr - m)))))


def exp_from_log(logp: float) -> float:
    """Return exp(logp), using 0 when the value is below float precision."""
    if not np.isfinite(logp):
        return 0.0 if logp < 0 else float("inf")
    if logp < math.log(np.finfo(float).tiny):
        return 0.0
    return float(math.exp(logp))


def exact_unique_lower_tail_logp(n_tracks: int, n_plays: int, observed_unique: int) -> Tuple[float, str]:
    """
    Exact lower-tail probability for the number of unique tracks observed under iid uniform draws.

    U is the number of occupied boxes after throwing n_plays balls into n_tracks equally likely boxes.
    This dynamic program computes P(U <= observed_unique) in log space.
    """
    if n_tracks <= 0 or n_plays < 0:
        return float("nan"), "invalid_input"
    if n_plays == 0:
        return (0.0 if observed_unique >= 0 else float("-inf")), "exact_occupancy_dp"

    max_unique = min(n_tracks, n_plays)
    observed_unique = int(max(0, min(observed_unique, max_unique)))

    work_units = n_plays * max_unique
    if work_units > 250_000_000:
        return float("nan"), "skipped_exact_dp_too_large"

    log_n = math.log(n_tracks)
    logp = np.full(max_unique + 1, float("-inf"), dtype=float)
    logp[0] = 0.0

    for t in range(n_plays):
        current_max = min(t, n_tracks, max_unique)
        new = np.full(max_unique + 1, float("-inf"), dtype=float)
        lp = logp[: current_max + 1]

        if current_max >= 1:
            u_stay = np.arange(1, current_max + 1, dtype=float)
            new[1 : current_max + 1] = np.logaddexp(
                new[1 : current_max + 1],
                lp[1 : current_max + 1] + np.log(u_stay) - log_n,
            )

        grow_max = min(current_max, n_tracks - 1, max_unique - 1)
        if grow_max >= 0:
            u_grow = np.arange(grow_max + 1, dtype=float)
            new[1 : grow_max + 2] = np.logaddexp(
                new[1 : grow_max + 2],
                lp[: grow_max + 1] + np.log(n_tracks - u_grow) - log_n,
            )

        logp = new

    return logsumexp_np(logp[: observed_unique + 1]), "exact_occupancy_dp"


def track_count_distribution(liked: pd.DataFrame, shuffled: pd.DataFrame) -> pd.DataFrame:
    """Observed vs expected number of tracks with count 0, 1, 2, ... under iid uniform draws."""
    n_tracks = len(liked)
    n_plays = len(shuffled)
    if n_tracks == 0:
        return pd.DataFrame()

    counts = shuffled["_key"].value_counts()
    all_counts = liked["_key"].astype(str).map(counts).fillna(0).astype(int)
    max_observed = int(all_counts.max()) if len(all_counts) else 0
    p = 1.0 / n_tracks

    rows: List[Dict[str, object]] = []
    for k in range(max_observed + 1):
        observed_tracks = int((all_counts == k).sum())
        expected_tracks = float(n_tracks * binom.pmf(k, n_plays, p))
        rows.append(
            {
                "play_count_bucket": str(k),
                "observed_track_count": observed_tracks,
                "expected_track_count": expected_tracks,
                "observed_minus_expected": observed_tracks - expected_tracks,
                "observed_to_expected_ratio": observed_tracks / expected_tracks if expected_tracks > 0 else np.nan,
            }
        )

    if max_observed >= 1:
        observed_tail = int((all_counts >= max_observed).sum())
        expected_tail = float(n_tracks * binom.sf(max_observed - 1, n_plays, p))
        rows.append(
            {
                "play_count_bucket": f">={max_observed}",
                "observed_track_count": observed_tail,
                "expected_track_count": expected_tail,
                "observed_minus_expected": observed_tail - expected_tail,
                "observed_to_expected_ratio": observed_tail / expected_tail if expected_tail > 0 else np.nan,
            }
        )

    return pd.DataFrame(rows)


def global_coverage_tests(liked: pd.DataFrame, shuffled: pd.DataFrame) -> pd.DataFrame:
    """
    Global library-coverage tests.

    Individual track tests can be conservative after correcting thousands of hypotheses.
    This test asks a different question: did the shuffle cover roughly as many distinct tracks as
    uniform iid randomness predicts? It directly detects subset/cycle behavior.
    """
    n_tracks = len(liked)
    n_plays = len(shuffled)
    if n_tracks == 0:
        return pd.DataFrame()

    counts = shuffled["_key"].value_counts()
    all_counts = liked["_key"].astype(str).map(counts).fillna(0).astype(int)
    unique_observed = int((all_counts > 0).sum())
    zero_observed = int((all_counts == 0).sum())

    p_zero = (1.0 - 1.0 / n_tracks) ** n_plays if n_tracks > 1 else (1.0 if n_plays == 0 else 0.0)
    expected_zero = n_tracks * p_zero
    expected_unique = n_tracks - expected_zero

    if n_tracks > 1:
        p_two_zero = (1.0 - 2.0 / n_tracks) ** n_plays
        var_zero = n_tracks * p_zero * (1.0 - p_zero) + n_tracks * (n_tracks - 1) * (p_two_zero - p_zero * p_zero)
    else:
        var_zero = 0.0
    sd_zero = math.sqrt(max(var_zero, 0.0))
    z_zero_high = (zero_observed - expected_zero) / sd_zero if sd_zero > 0 else np.nan
    z_unique_low = (unique_observed - expected_unique) / sd_zero if sd_zero > 0 else np.nan

    logp_unique_low, method = exact_unique_lower_tail_logp(n_tracks, n_plays, unique_observed)
    p_unique_low = exp_from_log(logp_unique_low)
    log10_unique_low = logp_unique_low / math.log(10) if np.isfinite(logp_unique_low) else np.nan

    rows = [
        {
            "test": "unique_tracks_observed_too_low",
            "observed": unique_observed,
            "expected": expected_unique,
            "standard_deviation": sd_zero,
            "z_score": z_unique_low,
            "p_value_exact": p_unique_low,
            "log10_p_value_exact": log10_unique_low,
            "method": method,
            "interpretation": "Tests whether the shuffle touched far fewer distinct liked tracks than uniform iid randomness predicts.",
        },
        {
            "test": "zero_play_tracks_observed_too_high",
            "observed": zero_observed,
            "expected": expected_zero,
            "standard_deviation": sd_zero,
            "z_score": z_zero_high,
            "p_value_exact": p_unique_low,
            "log10_p_value_exact": log10_unique_low,
            "method": method,
            "interpretation": "Same occupancy test as unique_tracks_observed_too_low, expressed as too many liked tracks receiving zero plays.",
        },
        {
            "test": "mean_plays_among_played_tracks",
            "observed": float(n_plays / unique_observed) if unique_observed else np.nan,
            "expected": float(n_plays / expected_unique) if expected_unique else np.nan,
            "standard_deviation": np.nan,
            "z_score": np.nan,
            "p_value_exact": np.nan,
            "log10_p_value_exact": np.nan,
            "method": "derived_metric",
            "interpretation": "If only a small subset is touched, played tracks will look overplayed on average even when individual Holm tests are conservative.",
        },
    ]
    return pd.DataFrame(rows)


def add_bh_discovery_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add Benjamini-Hochberg FDR q-values for discovery-oriented files."""
    out = df.copy()
    if out.empty or "p_over_exact" not in out.columns or "p_under_exact" not in out.columns:
        return out
    q_over, q_under = combined_directional_adjust(out["p_over_exact"].to_numpy(), out["p_under_exact"].to_numpy(), "bh")
    out["q_over_bh"] = q_over
    out["q_under_bh"] = q_under
    return out


# ------------------------------- reporting -------------------------------- #

def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def top_lines(
    df: pd.DataFrame,
    title: str,
    q_col: str,
    ratio_col: str = "count_ratio",
    max_rows: int = 10,
) -> List[str]:
    lines = [f"### {title}", ""]
    if df.empty:
        lines += ["No rows.", ""]
        return lines
    cols = [
        c
        for c in ["track_name", "artists", "artist", "group_type", "group_value", "observed_count", "expected_count", ratio_col, q_col]
        if c in df.columns
    ]
    show = df[cols].head(max_rows).copy()
    # Markdown table without requiring tabulate.
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for _, row in show.iterrows():
        values = []
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                values.append(fmt_float(v, 4))
            else:
                values.append(str(v).replace("|", "/"))
        lines.append("| " + " | ".join(values) + " |")
    lines.append("")
    return lines


def write_summary(
    path: Path,
    prepared: PreparedData,
    track_df: pd.DataFrame,
    artist_df: pd.DataFrame,
    group_df: pd.DataFrame,
    seq_df: pd.DataFrame,
    global_df: pd.DataFrame,
    count_dist_df: pd.DataFrame,
    alpha: float,
    correction: str,
    eligibility: str,
) -> None:
    liked = prepared.liked
    shuffled = prepared.shuffled
    n_tracks = len(liked)
    n_plays = len(shuffled)
    expected = n_plays / n_tracks
    p_zero = (1 - 1 / n_tracks) ** n_plays

    q_over = f"q_over_{correction}"
    q_under = f"q_under_{correction}"

    over = track_df[track_df["audit_status"] == "over_played"].sort_values([q_over, "count_ratio"], ascending=[True, False])
    under = track_df[track_df["audit_status"].isin(["under_played", "silenced_significant"])].sort_values([q_under, "count_ratio"], ascending=[True, True])
    silent = track_df[track_df["observed_count"] == 0].sort_values([q_under, "track_name" if "track_name" in track_df.columns else "_key"])

    lines = [
        "# Spotify shuffle audit summary",
        "",
        "## Model",
        "",
        "Primary null hypothesis: in the analysed window, each play is an independent uniform draw from the eligible liked-track set.",
        "This report uses exact binomial tail probabilities for track, artist, and group counts. Directional over/under tests are corrected as one multiple-testing family with "
        f"`{correction}` at alpha = {alpha}.",
        "",
        "A finite listening log cannot prove Spotify's algorithm is random with certainty. It can show which observations are very unlikely under a clearly stated random model.",
        "",
        "## Inputs used",
        "",
        f"- Eligibility mode: `{eligibility}`",
        f"- Liked tracks used: {n_tracks:,}",
        f"- Shuffled liked-set plays used: {n_plays:,}",
        f"- Expected plays per track under uniform iid: {expected:.4f}",
        f"- Probability that a specific liked track receives zero plays: {p_zero:.6g}",
        f"- Expected number of zero-play liked tracks: {n_tracks * p_zero:.4f}",
        "",
    ]

    if prepared.warnings:
        lines += ["## Data warnings", ""]
        for w in prepared.warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines += ["## Main counts", ""]
    lines += [
        f"- Statistically over-played tracks: {len(over):,}",
        f"- Statistically under-played tracks: {len(under):,}",
        f"- Zero-play tracks: {len(silent):,}",
        f"- Zero-play tracks that are significant after correction: {int((silent['audit_status'] == 'silenced_significant').sum()) if not silent.empty else 0:,}",
        "",
    ]

    if not global_df.empty:
        lines += ["## Global coverage / concentration", ""]
        unique_row = global_df[global_df["test"] == "unique_tracks_observed_too_low"]
        zero_row = global_df[global_df["test"] == "zero_play_tracks_observed_too_high"]
        mean_row = global_df[global_df["test"] == "mean_plays_among_played_tracks"]
        if not unique_row.empty:
            r = unique_row.iloc[0]
            lines.append(
                f"- Unique liked tracks actually played: {int(r['observed']):,}; expected under uniform iid: {float(r['expected']):,.1f}."
            )
            lines.append(
                f"- Exact occupancy lower-tail log10 p-value: {fmt_float(float(r['log10_p_value_exact']), 4)} "
                f"using `{r['method']}`."
            )
        if not zero_row.empty:
            r = zero_row.iloc[0]
            lines.append(
                f"- Zero-play liked tracks observed: {int(r['observed']):,}; expected under uniform iid: {float(r['expected']):,.1f}."
            )
        if not mean_row.empty:
            r = mean_row.iloc[0]
            lines.append(
                f"- Mean plays among tracks that appeared at least once: {fmt_float(float(r['observed']), 4)}; "
                f"uniform iid expectation after conditioning on being played: {fmt_float(float(r['expected']), 4)}."
            )
        lines.append(
            "- This global test is often more informative than asking whether one specific track survives strict Holm correction."
        )
        lines.append("")

    if not count_dist_df.empty:
        lines += ["### Track-count distribution", ""]
        show = count_dist_df.head(12).copy()
        lines.append("| play_count_bucket | observed_track_count | expected_track_count | observed_to_expected_ratio |")
        lines.append("| --- | ---: | ---: | ---: |")
        for _, row in show.iterrows():
            lines.append(
                f"| {row['play_count_bucket']} | {int(row['observed_track_count'])} | "
                f"{fmt_float(float(row['expected_track_count']), 4)} | "
                f"{fmt_float(float(row['observed_to_expected_ratio']), 4)} |"
            )
        lines.append("")

    lines += top_lines(over, "Top strictly over-played tracks", q_over)
    lines += top_lines(under, "Top strictly under-played or silenced tracks", q_under)

    if not artist_df.empty:
        prioritized = artist_df[artist_df["audit_status"] == "prioritized"].sort_values([q_over, "count_ratio"], ascending=[True, False])
        deprioritized = artist_df[artist_df["audit_status"] == "deprioritized"].sort_values([q_under, "count_ratio"], ascending=[True, True])
        lines += ["## Artists", ""]
        lines += [f"- Prioritized artists: {len(prioritized):,}"]
        lines += [f"- Deprioritized artists: {len(deprioritized):,}", ""]
        lines += top_lines(prioritized, "Top prioritized artists", q_over)
        lines += top_lines(deprioritized, "Top deprioritized artists", q_under)

    if not group_df.empty:
        significant_groups = group_df[group_df["audit_status"] != "not_significant"]
        lines += ["## Other group signals", ""]
        lines += [f"- Significant metadata groups: {len(significant_groups):,}", ""]
        lines += top_lines(significant_groups.sort_values([q_over, q_under]), "Most significant groups", q_over)

    if not seq_df.empty:
        lines += ["## Sequence diagnostics", ""]
        lines.append("| test | observed | expected | p_over_exact | p_under_exact |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for _, row in seq_df.iterrows():
            lines.append(
                f"| {row['test']} | {int(row['observed'])} | {fmt_float(float(row['expected']), 4)} | "
                f"{fmt_float(float(row['p_over_exact']), 4)} | {fmt_float(float(row['p_under_exact']), 4)} |"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------- main ---------------------------------- #

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exact statistical audit of Spotify liked-songs shuffle history."
    )
    parser.add_argument("--liked", required=True, type=Path, help="CSV containing all liked tracks.")
    parser.add_argument("--shuffled", required=True, type=Path, help="CSV containing observed shuffled plays.")
    parser.add_argument("--out", required=True, type=Path, help="Output directory.")
    parser.add_argument("--alpha", type=float, default=0.05, help="Familywise/FDR decision level. Default: 0.05.")
    parser.add_argument(
        "--correction",
        choices=["holm", "bonferroni", "bh", "by", "none"],
        default="holm",
        help="Multiple-testing correction. Holm is strict familywise default. BH/BY are discovery-oriented.",
    )
    parser.add_argument(
        "--eligibility",
        choices=["fixed_current", "frozen_after_last_like"],
        default="fixed_current",
        help=(
            "fixed_current assumes the liked CSV is the eligible set for all analysed plays. "
            "frozen_after_last_like keeps only plays after the latest added_at, making the current liked set exact."
        ),
    )
    parser.add_argument("--window-start", default=None, help="Optional UTC start, e.g. 2026-05-23T00:00:00Z.")
    parser.add_argument("--window-end", default=None, help="Optional UTC end, e.g. 2026-05-24T00:00:00Z.")
    parser.add_argument(
        "--min-artist-liked-tracks",
        type=int,
        default=1,
        help="Minimum liked tracks for artist-level testing. Default: 1. Use 2+ to suppress one-track artist aliases.",
    )
    parser.add_argument(
        "--min-group-tracks",
        type=int,
        default=10,
        help="Minimum liked tracks for metadata group tests. Default: 10.",
    )
    parser.add_argument(
        "--include-album-groups",
        action="store_true",
        help="Also test album groups. Often creates many tests; off by default.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if not (0 < args.alpha < 1):
        die("--alpha must be between 0 and 1")

    ensure_outdir(args.out)

    prepared = prepare_data(
        liked_path=args.liked,
        shuffled_path=args.shuffled,
        window_start=args.window_start,
        window_end=args.window_end,
        eligibility=args.eligibility,
    )

    track_df = track_analysis(prepared.liked, prepared.shuffled, args.alpha, args.correction)
    artist_df = artist_analysis(
        prepared.liked,
        prepared.shuffled,
        args.alpha,
        args.correction,
        args.min_artist_liked_tracks,
    )
    group_df = generic_group_analysis(
        prepared.liked,
        prepared.shuffled,
        args.alpha,
        args.correction,
        args.min_group_tracks,
        args.include_album_groups,
    )
    seq_df = sequence_tests(prepared.liked, prepared.shuffled, args.alpha)
    global_df = global_coverage_tests(prepared.liked, prepared.shuffled)
    count_dist_df = track_count_distribution(prepared.liked, prepared.shuffled)

    track_df = add_bh_discovery_columns(track_df)
    artist_df = add_bh_discovery_columns(artist_df)

    q_over = f"q_over_{args.correction}"
    q_under = f"q_under_{args.correction}"

    write_csv(pd.DataFrame(prepared.data_quality), args.out / "data_quality.csv")
    write_csv(global_df, args.out / "global_coverage_tests.csv")
    write_csv(count_dist_df, args.out / "track_count_distribution.csv")
    write_csv(track_df, args.out / "track_all.csv")
    write_csv(
        track_df[track_df["audit_status"] == "over_played"].sort_values([q_over, "count_ratio"], ascending=[True, False]),
        args.out / "track_overplayed.csv",
    )
    write_csv(
        track_df[track_df["audit_status"].isin(["under_played", "silenced_significant"])].sort_values([q_under, "count_ratio"], ascending=[True, True]),
        args.out / "track_underplayed.csv",
    )
    write_csv(
        track_df[track_df["q_over_bh"] <= args.alpha].sort_values(["q_over_bh", "count_ratio", "observed_count"], ascending=[True, False, False]),
        args.out / "track_discovery_overplayed_bh.csv",
    )
    write_csv(
        track_df[track_df["q_under_bh"] <= args.alpha].sort_values(["q_under_bh", "count_ratio", "observed_count"], ascending=[True, True, True]),
        args.out / "track_discovery_underplayed_bh.csv",
    )
    write_csv(
        track_df[track_df["observed_count"] == 0].sort_values([q_under, "_key"]),
        args.out / "track_silenced.csv",
    )

    if not artist_df.empty:
        write_csv(artist_df, args.out / "artist_all.csv")
        write_csv(
            artist_df[artist_df["audit_status"] == "prioritized"].sort_values([q_over, "count_ratio"], ascending=[True, False]),
            args.out / "artist_prioritized.csv",
        )
        write_csv(
            artist_df[artist_df["audit_status"] == "deprioritized"].sort_values([q_under, "count_ratio"], ascending=[True, True]),
            args.out / "artist_deprioritized.csv",
        )
        write_csv(
            artist_df[artist_df["q_over_bh"] <= args.alpha].sort_values(["q_over_bh", "count_ratio", "observed_count"], ascending=[True, False, False]),
            args.out / "artist_discovery_prioritized_bh.csv",
        )
        write_csv(
            artist_df[artist_df["q_under_bh"] <= args.alpha].sort_values(["q_under_bh", "count_ratio", "observed_count"], ascending=[True, True, True]),
            args.out / "artist_discovery_deprioritized_bh.csv",
        )

    if not group_df.empty:
        write_csv(group_df, args.out / "group_bias.csv")

    if not seq_df.empty:
        write_csv(seq_df, args.out / "sequence_tests.csv")

    write_summary(
        args.out / "summary.md",
        prepared,
        track_df,
        artist_df,
        group_df,
        seq_df,
        global_df,
        count_dist_df,
        args.alpha,
        args.correction,
        args.eligibility,
    )

    print(f"Done. Results written to: {args.out}")
    print(f"Open: {args.out / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
