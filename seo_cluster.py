#!/usr/bin/env python3
"""
SEO Keyword Cluster Engine
===========================
Transforms a raw Semrush/Ahrefs CSV export into a structured keyword study
with semantic clusters, page assignments, and priority scoring.

Usage:
    python seo_cluster.py input.csv [--output study.xlsx] [--threshold 0.55]
                                    [--base-url /canapes/] [--min-volume 10]

Output: Excel file with 3 sheets:
    1. Pages     - One row per page with all assigned keywords
    2. Keywords  - One row per keyword with cluster + page assignment
    3. Clusters  - Summary per cluster (volume, nb pages, pillar KW)
"""

import argparse
import re
import sys
from pathlib import Path
from collections import Counter, defaultdict

import pandas as pd
import numpy as np
from unidecode import unidecode
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics.pairwise import cosine_similarity


# ---------------------------------------------------------------------------
# 1. CSV LOADING & AUTO-DETECTION
# ---------------------------------------------------------------------------

SEMRUSH_COLUMNS = {
    "keyword": ["keyword", "mot-clé", "mot cle", "query", "search query"],
    "volume": ["volume", "search volume", "vol.", "avg. monthly searches"],
    "kd": ["keyword difficulty", "kd", "kd %", "difficulty", "kd%"],
    "cpc": ["cpc", "cpc (usd)", "cpc (eur)"],
    "url": ["url", "current url", "landing page"],
    "position": ["position", "pos", "pos.", "ranking"],
    "intent": ["intent", "search intent"],
}


def normalize_col(name: str) -> str:
    return unidecode(name).strip().lower().replace(".", "").replace("%", "")


def detect_columns(df: pd.DataFrame) -> dict:
    """Map raw column names to standardized names."""
    mapping = {}
    raw_cols = {normalize_col(c): c for c in df.columns}

    for standard, candidates in SEMRUSH_COLUMNS.items():
        for candidate in candidates:
            norm = normalize_col(candidate)
            if norm in raw_cols:
                mapping[standard] = raw_cols[norm]
                break

    return mapping


def load_csv(path: str) -> pd.DataFrame:
    """Load CSV with auto-encoding and separator detection."""
    for encoding in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
        for sep in [",", ";", "\t"]:
            try:
                df = pd.read_csv(path, encoding=encoding, sep=sep, on_bad_lines="skip")
                if len(df.columns) > 1 and len(df) > 0:
                    return df
            except Exception:
                continue

    raise ValueError(f"Cannot parse CSV: {path}")


def prepare_data(df: pd.DataFrame, min_volume: int = 10) -> pd.DataFrame:
    """Standardize columns, clean data, filter by volume."""
    col_map = detect_columns(df)

    if "keyword" not in col_map:
        print("ERROR: No 'keyword' column found.")
        print(f"  Available columns: {list(df.columns)}")
        sys.exit(1)

    result = pd.DataFrame()
    result["keyword"] = df[col_map["keyword"]].astype(str).str.strip().str.lower()

    if "volume" in col_map:
        result["volume"] = (
            pd.to_numeric(
                df[col_map["volume"]]
                .astype(str)
                .str.replace(r"[^\d]", "", regex=True),
                errors="coerce",
            )
            .fillna(0)
            .astype(int)
        )
    else:
        result["volume"] = 0

    if "kd" in col_map:
        result["kd"] = (
            pd.to_numeric(
                df[col_map["kd"]]
                .astype(str)
                .str.replace(r"[^\d.]", "", regex=True),
                errors="coerce",
            )
            .fillna(-1)
            .astype(float)
        )
    else:
        result["kd"] = -1.0

    if "cpc" in col_map:
        result["cpc"] = pd.to_numeric(
            df[col_map["cpc"]].astype(str).str.replace(r"[^\d.]", "", regex=True),
            errors="coerce",
        ).fillna(0)
    else:
        result["cpc"] = 0.0

    if "intent" in col_map:
        result["intent"] = df[col_map["intent"]].astype(str).str.strip()
    else:
        result["intent"] = ""

    # Deduplicate: keep highest volume variant
    result = result.sort_values("volume", ascending=False).drop_duplicates(
        subset="keyword", keep="first"
    )

    # Filter minimum volume
    result = result[result["volume"] >= min_volume].reset_index(drop=True)

    print(f"  Loaded {len(result)} keywords (min vol={min_volume})")
    return result


# ---------------------------------------------------------------------------
# 2. SEMANTIC CLUSTERING (HYBRID: HEAD-TERM + TF-IDF)
# ---------------------------------------------------------------------------

FR_STOP_WORDS = set([
    "de", "du", "des", "le", "la", "les", "un", "une", "en", "et", "pour",
    "par", "avec", "dans", "sur", "au", "aux", "ce", "se", "son", "sa",
    "ses", "ne", "pas", "plus", "que", "qui", "est", "sont", "a", "d",
    "l", "n", "s", "c", "m", "t", "y",
])

# Commercial/intent modifiers — stripped during head-term extraction
MODIFIER_WORDS = set([
    "pas", "cher", "prix", "meilleur", "avis", "comparatif", "solde",
    "soldes", "promo", "promotion", "achat", "acheter", "occasion",
    "destockage", "livraison", "gratuite", "rapide", "conforama",
    "ikea", "habitat", "maison", "monde", "black", "friday",
    "paiement", "plusieurs", "fois", "qualite", "bonne", "haute",
    "haut", "gamme", "luxe", "premium",
])

# Numeric patterns to preserve as-is (dimensions, places)
NUMERIC_PATTERN = re.compile(r"\b\d+(?:x\d+)?\b")

# Known compound terms that should be treated as one unit
COMPOUND_TERMS = [
    "clic clac", "bubble sofa", "velours cotele", "velour cotele",
    "anti tache", "haut de gamme", "bleu canard",
]


def normalize_keyword(kw: str) -> str:
    return unidecode(kw.strip().lower())


def extract_head_term(kw: str) -> str:
    """
    Extract the 'head term' (core product concept) from a keyword.
    e.g. "canapé convertible 2 places pas cher" -> "canape convertible"
    e.g. "clic clac avec coffre" -> "clic clac"
    e.g. "canapé clic clac" -> "canape clic-clac" (compound preserved)
    """
    norm = normalize_keyword(kw)

    # Replace compound terms with hyphenated form to keep them as one token
    for compound in sorted(COMPOUND_TERMS, key=len, reverse=True):
        norm_compound = normalize_keyword(compound)
        if norm_compound in norm:
            norm = norm.replace(norm_compound, norm_compound.replace(" ", "-"))

    words = norm.split()

    # Remove stop words & modifiers, keep product terms
    core_words = []
    for w in words:
        # Restore compound for checking
        w_check = w.replace("-", " ")
        if w_check in FR_STOP_WORDS or w_check in MODIFIER_WORDS:
            continue
        if NUMERIC_PATTERN.fullmatch(w):
            continue
        core_words.append(w)

    # The head term is the first 1-3 meaningful words
    if len(core_words) >= 2:
        return " ".join(core_words[:2])
    elif core_words:
        return core_words[0]
    return norm


def _explode_words(ht: str) -> set:
    """Split head-term into atomic words, breaking hyphenated compounds."""
    words = set()
    for token in ht.split():
        if "-" in token:
            words.update(p for p in token.split("-") if p)
        else:
            words.add(token)
    return words


def _pre_merge_head_terms(head_groups: dict) -> dict:
    """
    Phase 1.5: Merge head-term groups by word containment + Jaccard.
    Rules:
      1. Subset: if A's atomic words are a subset of B's → merge
      2. High Jaccard (>=0.5) on atomic words → merge
    Does NOT use "distinctive word" logic to avoid false positives
    (e.g. "convertible" wrongly merging unrelated products).
    """
    head_terms = list(head_groups.keys())
    ht_words = {ht: _explode_words(ht) for ht in head_terms}

    # Union-Find
    parent = {ht: ht for ht in head_terms}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            if len(head_groups.get(ra, [])) >= len(head_groups.get(rb, [])):
                parent[rb] = ra
            else:
                parent[ra] = rb

    # Rule 1: Subset (one term's words fully contained in another's)
    for i, ht_a in enumerate(head_terms):
        for j, ht_b in enumerate(head_terms):
            if i >= j:
                continue
            words_a, words_b = ht_words[ht_a], ht_words[ht_b]
            if words_a.issubset(words_b) or words_b.issubset(words_a):
                union(ht_a, ht_b)

    # Rule 2: High Jaccard on atomic words
    for i, ht_a in enumerate(head_terms):
        for j, ht_b in enumerate(head_terms):
            if i >= j:
                continue
            if find(ht_a) == find(ht_b):
                continue
            words_a, words_b = ht_words[ht_a], ht_words[ht_b]
            if not words_a or not words_b:
                continue
            jaccard = len(words_a & words_b) / len(words_a | words_b)
            if jaccard >= 0.5:
                union(ht_a, ht_b)

    # Rebuild groups after merging
    merged = defaultdict(list)
    for ht in head_terms:
        root = find(ht)
        merged[root].extend(head_groups[ht])

    return dict(merged)


def cluster_keywords(
    df: pd.DataFrame,
    threshold: float = 0.55,
    min_cluster_size: int = 1,
) -> pd.DataFrame:
    """
    Three-phase clustering:
    Phase 1:   Group by extracted head-terms (fast, high precision)
    Phase 1.5: Merge head-terms with containment/overlap (fixes synonyms)
    Phase 2:   Merge remaining groups via TF-IDF similarity
    """
    df = df.copy()
    keywords = df["keyword"].tolist()
    n = len(keywords)

    if n == 0:
        return df.assign(cluster_id=-1, cluster_name="")

    # --- Phase 1: Head-term grouping ---
    print(f"  Phase 1: Extracting head terms from {n} keywords...")
    df["_head_term"] = df["keyword"].apply(extract_head_term)

    head_groups = defaultdict(list)
    for idx, row in df.iterrows():
        head_groups[row["_head_term"]].append(idx)

    print(f"  Found {len(head_groups)} head-term groups")

    # --- Phase 1.5: Pre-merge by word containment ---
    head_groups = _pre_merge_head_terms(dict(head_groups))
    print(f"  After containment merge: {len(head_groups)} groups")

    # --- Phase 2: Merge similar groups via TF-IDF (word + char) ---
    print(f"  Phase 2: TF-IDF merge (threshold={threshold})...")

    head_terms = list(head_groups.keys())
    if len(head_terms) <= 1:
        df["cluster_id"] = 0
        df["cluster_name"] = _name_cluster(df)
        return df

    # Build representative text for each group
    group_texts = []
    for ht in head_terms:
        indices = head_groups[ht]
        texts = [normalize_keyword(df.at[i, "keyword"]) for i in indices]
        # Restore hyphens to spaces for better TF-IDF
        clean_ht = ht.replace("-", " ")
        group_texts.append(f"{clean_ht} {clean_ht} " + " ".join(t.replace("-", " ") for t in texts))

    # Word TF-IDF
    word_vec = TfidfVectorizer(
        analyzer="word",
        ngram_range=(1, 3),
        stop_words=list(FR_STOP_WORDS),
        min_df=1,
        max_df=0.95,
        sublinear_tf=True,
    )
    # Char TF-IDF (catches morphological similarity)
    char_vec = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=(3, 5),
        min_df=1,
        max_df=0.95,
        sublinear_tf=True,
    )

    from scipy.sparse import hstack
    word_matrix = word_vec.fit_transform(group_texts)
    char_matrix = char_vec.fit_transform(group_texts)
    tfidf_matrix = hstack([word_matrix * 0.7, char_matrix * 0.3])

    sim_matrix = cosine_similarity(tfidf_matrix)
    distance_matrix = np.clip(1 - sim_matrix, 0, None)
    np.fill_diagonal(distance_matrix, 0)

    if len(head_terms) > 1:
        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=threshold,
            metric="precomputed",
            linkage="average",
        )
        group_labels = clustering.fit_predict(distance_matrix)
    else:
        group_labels = [0]

    ht_to_cluster = dict(zip(head_terms, group_labels))

    # Assign cluster IDs
    for ht, indices in head_groups.items():
        cid = ht_to_cluster[ht]
        for idx in indices:
            df.at[idx, "cluster_id"] = cid

    # Handle tiny clusters: merge into nearest larger cluster
    cluster_sizes = Counter(df["cluster_id"].tolist())
    small_clusters = {c for c, s in cluster_sizes.items() if s < min_cluster_size}

    if small_clusters and len(cluster_sizes) > len(small_clusters):
        large_ht_indices = [i for i, ht in enumerate(head_terms) if ht_to_cluster[ht] not in small_clusters]
        if large_ht_indices:
            for i, ht in enumerate(head_terms):
                if ht_to_cluster[ht] in small_clusters:
                    sims = sim_matrix[i, large_ht_indices]
                    best_large_idx = large_ht_indices[np.argmax(sims)]
                    new_cluster = ht_to_cluster[head_terms[best_large_idx]]
                    for idx in head_groups[ht]:
                        df.at[idx, "cluster_id"] = new_cluster

    # --- Name clusters ---
    cluster_names = {}
    for cid in df["cluster_id"].unique():
        mask = df["cluster_id"] == cid
        cluster_kws = df[mask].sort_values("volume", ascending=False)
        cluster_names[cid] = _name_cluster(cluster_kws)

    df["cluster_name"] = df["cluster_id"].map(cluster_names)

    # Clean up temp column
    df.drop(columns=["_head_term"], inplace=True)

    n_clusters = df["cluster_id"].nunique()
    print(f"  Final: {n_clusters} clusters")

    return df


def _name_cluster(cluster_df: pd.DataFrame) -> str:
    """Name a cluster based on common core terms in its keywords."""
    if cluster_df.empty:
        return "Unknown"

    kws = cluster_df.sort_values("volume", ascending=False)["keyword"].tolist()

    # Find words common to >50% of keywords (after normalization)
    words_per_kw = [
        [w for w in normalize_keyword(kw).split()
         if w not in FR_STOP_WORDS and w not in MODIFIER_WORDS and len(w) > 1]
        for kw in kws
    ]

    word_freq = Counter()
    for words in words_per_kw:
        for w in set(words):
            word_freq[w] += 1

    threshold = max(1, len(kws) * 0.3)
    common_words = [w for w, c in word_freq.most_common(4) if c >= threshold and not NUMERIC_PATTERN.fullmatch(w)]

    if common_words:
        # Order by first appearance in highest-volume keyword
        top_words = [w for w in normalize_keyword(kws[0]).split()
                     if w not in FR_STOP_WORDS and w not in MODIFIER_WORDS]
        ordered = [w for w in top_words if w in common_words]
        remaining = [w for w in common_words if w not in ordered]
        name = " ".join(ordered + remaining)
    else:
        # Fallback: cleaned top keyword
        name = " ".join(
            w for w in normalize_keyword(kws[0]).split()
            if w not in FR_STOP_WORDS and len(w) > 1
        )

    return name.strip().title() if name.strip() else kws[0].title()


# ---------------------------------------------------------------------------
# 3. PAGE ASSIGNMENT & URL GENERATION
# ---------------------------------------------------------------------------

# Attributes that warrant their own sub-page
PAGE_SPLIT_ATTRIBUTES = {
    "color": ["blanc", "noir", "gris", "beige", "vert", "bleu", "rouge",
              "marron", "taupe", "orange", "rose", "jaune", "bleu canard",
              "gris clair"],
    "material": ["velours", "cuir", "tissu", "bouclette", "simili", "lin",
                 "microfibre", "cotele", "velour cotele"],
    "size": ["1 place", "2 places", "3 places", "4 places", "5 places",
             "7 places", "10 places", "14 places",
             "xxl", "xl", "petit", "grand"],
    "dimension": ["140", "140x190", "140x200", "160x200", "120x190",
                   "130x190", "190x140", "200 cm", "300 cm"],
    "feature": ["convertible", "modulable", "coffre", "dehoussable", "relax",
                "electrique", "gigogne", "angle", "droit", "panoramique",
                "couchage quotidien"],
    "intent": ["pas cher", "solde", "soldes", "promo", "prix", "meilleur",
               "avis", "comparatif", "guide", "comment", "choisir",
               "haut de gamme", "luxe", "design", "confortable"],
}


def assign_pages(df: pd.DataFrame, base_url: str = "/") -> pd.DataFrame:
    """Group keywords into pages within each cluster."""
    df = df.copy()
    df["page_id"] = ""
    df["page_title"] = ""
    df["page_url"] = ""
    df["page_type"] = ""

    for cid in df["cluster_id"].unique():
        mask = df["cluster_id"] == cid
        cluster_df = df[mask].sort_values("volume", ascending=False)
        cluster_name = cluster_df.iloc[0]["cluster_name"]
        slug_base = _slugify(cluster_name)
        base = base_url.rstrip("/")

        # Detect sub-groups worth their own page
        sub_pages = _detect_sub_pages(cluster_df, cluster_name, slug_base, base)

        # All keywords not assigned to sub-pages go to pillar
        assigned_indices = set()
        for sp in sub_pages.values():
            assigned_indices.update(sp["indices"])

        pillar_indices = [idx for idx in cluster_df.index if idx not in assigned_indices]
        # Always include all indices in pillar if no sub-pages
        if not sub_pages:
            pillar_indices = list(cluster_df.index)

        pillar_id = f"p_{slug_base}"
        pillar_title = _make_title(cluster_name)
        pillar_url = f"{base}/{slug_base}/"

        # Determine pillar type
        pillar_type = "Listing"

        for idx in pillar_indices:
            df.at[idx, "page_id"] = pillar_id
            df.at[idx, "page_title"] = pillar_title
            df.at[idx, "page_url"] = pillar_url
            df.at[idx, "page_type"] = pillar_type

        for sp_id, sp_info in sub_pages.items():
            for idx in sp_info["indices"]:
                df.at[idx, "page_id"] = sp_id
                df.at[idx, "page_title"] = sp_info["title"]
                df.at[idx, "page_url"] = sp_info["url"]
                df.at[idx, "page_type"] = sp_info["type"]

    return df


def _detect_sub_pages(
    cluster_df: pd.DataFrame,
    cluster_name: str,
    slug_base: str,
    base: str,
    min_sub_volume: int = 300,
    min_sub_keywords: int = 2,
) -> dict:
    """Identify keyword sub-groups that deserve their own page."""
    sub_pages = {}
    cluster_name_norm = normalize_keyword(cluster_name)

    # Check each attribute type for sub-grouping potential
    candidate_subs = defaultdict(list)

    for idx, row in cluster_df.iterrows():
        kw_norm = normalize_keyword(row["keyword"])

        for attr_type, attr_values in PAGE_SPLIT_ATTRIBUTES.items():
            for attr_val in attr_values:
                if attr_val in kw_norm and attr_val not in cluster_name_norm:
                    candidate_subs[(attr_type, attr_val)].append(idx)

    # Filter: only keep sub-groups with enough volume and keywords
    for (attr_type, attr_val), indices in sorted(
        candidate_subs.items(),
        key=lambda x: -sum(cluster_df.loc[i, "volume"] for i in x[1] if i in cluster_df.index),
    ):
        # Deduplicate indices (keyword may match multiple attributes)
        indices = [i for i in indices if i in cluster_df.index]
        total_vol = sum(cluster_df.loc[i, "volume"] for i in indices)

        if total_vol < min_sub_volume or len(indices) < min_sub_keywords:
            continue

        sub_slug = _slugify(attr_val)
        sp_id = f"p_{slug_base}_{sub_slug}"

        # Skip if this page already exists (avoid duplication)
        if sp_id in sub_pages:
            continue

        # Determine page type
        if attr_type == "intent" and any(
            w in attr_val for w in ["guide", "comment", "choisir", "comparatif", "meilleur"]
        ):
            page_type = "Guide"
        elif attr_type == "intent":
            page_type = "Lp"
        else:
            page_type = "Listing"

        sub_pages[sp_id] = {
            "title": _make_title(f"{cluster_name} {attr_val}"),
            "url": f"{base}/{slug_base}/{sub_slug}/",
            "type": page_type,
            "indices": indices,
        }

    return sub_pages


def _slugify(text: str) -> str:
    text = unidecode(text).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _make_title(text: str) -> str:
    words = text.split()
    title = " ".join(w.capitalize() if len(w) > 2 else w for w in words)
    return title[0].upper() + title[1:] if title else text


# ---------------------------------------------------------------------------
# 4. PRIORITY SCORING
# ---------------------------------------------------------------------------

def compute_priority(df: pd.DataFrame) -> pd.DataFrame:
    """Assign priority per cluster based on volume + KD opportunity."""
    df = df.copy()

    cluster_stats = df.groupby("cluster_id").agg(
        total_volume=("volume", "sum"),
        avg_kd=("kd", lambda x: x[x >= 0].mean() if (x >= 0).any() else 50),
        kw_count=("keyword", "count"),
    )

    vol_max = cluster_stats["total_volume"].max() or 1
    cluster_stats["vol_score"] = cluster_stats["total_volume"] / vol_max

    # Lower KD = higher opportunity
    cluster_stats["kd_score"] = 1 - (cluster_stats["avg_kd"].clip(0, 100) / 100)

    # Combined score (60% volume, 40% KD opportunity)
    cluster_stats["score"] = cluster_stats["vol_score"] * 0.6 + cluster_stats["kd_score"] * 0.4

    # Assign labels
    thresholds = cluster_stats["score"].quantile([0.33, 0.66])

    def priority_label(score):
        if score >= thresholds[0.66]:
            return "Haute"
        elif score >= thresholds[0.33]:
            return "Moyenne"
        return "Basse"

    cluster_stats["priority"] = cluster_stats["score"].apply(priority_label)

    df["priority"] = df["cluster_id"].map(cluster_stats["priority"])
    df["priority_score"] = df["cluster_id"].map(cluster_stats["score"])

    return df


def priority_stars(priority: str) -> str:
    return {
        "Haute": "\u2605\u2605\u2605 Haute",
        "Moyenne": "\u2605\u2605 Moyenne",
        "Basse": "\u2605 Basse",
    }.get(priority, priority)


def kd_level(kd) -> str:
    try:
        kd = float(kd)
    except (ValueError, TypeError):
        return "N/A"
    if kd < 0:
        return "N/A"
    if kd <= 20:
        return "\U0001f7e2 Tres faible"
    if kd <= 30:
        return "\U0001f7e2 Faible"
    if kd <= 50:
        return "\U0001f7e1 Moyenne"
    if kd <= 70:
        return "\U0001f7e0 Elevee"
    return "\U0001f534 Tres elevee"


# ---------------------------------------------------------------------------
# 5. OUTPUT GENERATION
# ---------------------------------------------------------------------------

def generate_pages_sheet(df: pd.DataFrame) -> pd.DataFrame:
    """Sheet 1: One row per page, all keywords aggregated."""
    rows = []
    page_groups = df.groupby("page_id", sort=False)

    for page_id, group in page_groups:
        group = group.sort_values("volume", ascending=False)
        top_kw = group.iloc[0]

        # Build keyword string
        kw_parts = []
        for _, r in group.iterrows():
            kd_str = f" KD{int(r['kd'])}" if r["kd"] >= 0 else ""
            kw_parts.append(f"{r['keyword']} ({r['volume']:,}){kd_str}")
        all_kw_str = " | ".join(kw_parts)

        kd_main = int(top_kw["kd"]) if top_kw["kd"] >= 0 else "N/A"

        rows.append({
            "#": 0,
            "Cluster": top_kw["cluster_name"],
            "Priorite": priority_stars(top_kw["priority"]),
            "URL": top_kw["page_url"],
            "Titre de page": top_kw["page_title"],
            "Type": top_kw["page_type"],
            "Nb KW": len(group),
            "Vol. total page": group["volume"].sum(),
            "KW principal": top_kw["keyword"],
            "Vol. KW1": top_kw["volume"],
            "KD KW1": kd_main,
            "Tous les mots-cles (vol)": all_kw_str,
            "Statut": "A creer",
        })

    result = pd.DataFrame(rows)
    if not result.empty:
        # Sort: priority (high first), then volume descending
        priority_order = {"\u2605\u2605\u2605 Haute": 0, "\u2605\u2605 Moyenne": 1, "\u2605 Basse": 2}
        result["_sort"] = result["Priorite"].map(priority_order).fillna(3)
        result = result.sort_values(
            ["_sort", "Vol. total page"], ascending=[True, False]
        ).drop(columns=["_sort"]).reset_index(drop=True)
        result["#"] = range(1, len(result) + 1)

    return result


def generate_keywords_sheet(df: pd.DataFrame) -> pd.DataFrame:
    """Sheet 2: One row per keyword, with cluster + page assignment."""
    rows = []

    for page_id, group in df.groupby("page_id", sort=False):
        group = group.sort_values("volume", ascending=False)
        for rank, (_, r) in enumerate(group.iterrows(), 1):
            rows.append({
                "Cluster": r["cluster_name"],
                "Priorite": priority_stars(r["priority"]),
                "Titre de page": r["page_title"],
                "URL": r["page_url"],
                "Type": r["page_type"],
                "Rang": rank,
                "Mot-cle": r["keyword"],
                "Volume": r["volume"],
                "KD": int(r["kd"]) if r["kd"] >= 0 else "N/A",
                "Niveau concurrence": kd_level(r["kd"]),
            })

    result = pd.DataFrame(rows)
    if not result.empty:
        priority_order = {"\u2605\u2605\u2605 Haute": 0, "\u2605\u2605 Moyenne": 1, "\u2605 Basse": 2}
        result["_sort"] = result["Priorite"].map(priority_order).fillna(3)
        result = result.sort_values(
            ["Cluster", "_sort", "Volume"], ascending=[True, True, False]
        ).drop(columns=["_sort"]).reset_index(drop=True)

    return result


def generate_clusters_sheet(df: pd.DataFrame) -> pd.DataFrame:
    """Sheet 3: Cluster summary - one row per cluster."""
    rows = []

    for cid in sorted(df["cluster_id"].unique()):
        mask = df["cluster_id"] == cid
        cluster_df = df[mask]
        top_kw = cluster_df.sort_values("volume", ascending=False).iloc[0]

        n_pages = cluster_df["page_id"].nunique()
        n_kw = len(cluster_df)
        total_vol = cluster_df["volume"].sum()
        avg_vol_page = total_vol // n_pages if n_pages > 0 else 0

        pillar_kw = top_kw["keyword"]
        pillar_vol = top_kw["volume"]
        pillar_url = top_kw["page_url"]

        rows.append({
            "Cluster": top_kw["cluster_name"],
            "Priorite": priority_stars(top_kw["priority"]),
            "Vol. total": total_vol,
            "Nb pages": n_pages,
            "Nb KW assignes": n_kw,
            "Vol. moyen/page": avg_vol_page,
            "KW pilier": pillar_kw,
            "Vol. KW pilier": pillar_vol,
            "URL pilier": pillar_url,
        })

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values("Vol. total", ascending=False).reset_index(drop=True)

    return result


# ---------------------------------------------------------------------------
# 6. EXCEL EXPORT WITH FORMATTING
# ---------------------------------------------------------------------------

def export_excel(
    pages_df: pd.DataFrame,
    keywords_df: pd.DataFrame,
    clusters_df: pd.DataFrame,
    output_path: str,
):
    """Export to formatted Excel workbook."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        pages_df.to_excel(writer, sheet_name="Pages", index=False)
        keywords_df.to_excel(writer, sheet_name="Mots-cles", index=False)
        clusters_df.to_excel(writer, sheet_name="Clusters", index=False)

        # Styling
        header_font = Font(bold=True, color="FFFFFF", size=11)
        header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin_border = Border(
            left=Side(style="thin", color="D9D9D9"),
            right=Side(style="thin", color="D9D9D9"),
            top=Side(style="thin", color="D9D9D9"),
            bottom=Side(style="thin", color="D9D9D9"),
        )

        # Priority color coding
        priority_fills = {
            "\u2605\u2605\u2605 Haute": PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid"),
            "\u2605\u2605 Moyenne": PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid"),
            "\u2605 Basse": PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid"),
        }

        for sheet_name in ["Pages", "Mots-cles", "Clusters"]:
            ws = writer.sheets[sheet_name]

            # Header styling
            for cell in ws[1]:
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_align
                cell.border = thin_border

            # Column widths & data formatting
            for col_idx, col in enumerate(ws.columns, 1):
                max_length = 0
                col_letter = get_column_letter(col_idx)
                header_value = ws.cell(row=1, column=col_idx).value or ""

                for row_idx, cell in enumerate(col, 1):
                    cell.border = thin_border
                    if row_idx > 1:
                        cell.alignment = Alignment(vertical="center", wrap_text=False)

                    # Priority coloring
                    if row_idx > 1 and header_value == "Priorite":
                        val = str(cell.value or "")
                        if val in priority_fills:
                            cell.fill = priority_fills[val]

                    try:
                        cell_len = len(str(cell.value or ""))
                        max_length = max(max_length, min(cell_len, 50))
                    except Exception:
                        pass

                # Smart column widths
                if "mots-cles" in header_value.lower() or "tous" in header_value.lower():
                    ws.column_dimensions[col_letter].width = 80
                elif header_value in ("URL", "URL pilier"):
                    ws.column_dimensions[col_letter].width = 35
                elif header_value in ("Titre de page", "KW pilier", "KW principal", "Mot-cle"):
                    ws.column_dimensions[col_letter].width = 35
                else:
                    ws.column_dimensions[col_letter].width = max(max_length + 3, 12)

            # Freeze header + auto-filter
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions

    print(f"\n  Excel exported: {output_path}")


# ---------------------------------------------------------------------------
# 7. MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="SEO Keyword Cluster Engine - Transform CSV to structured keyword study"
    )
    parser.add_argument("input", help="Path to CSV file (Semrush/Ahrefs export)")
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output Excel file path (default: <input>_study.xlsx)"
    )
    parser.add_argument(
        "--threshold", "-t", type=float, default=0.50,
        help="Clustering distance threshold (0.0-1.0, higher = fewer clusters). Default: 0.50"
    )
    parser.add_argument(
        "--base-url", "-b", default="/",
        help="Base URL path for generated URLs. Default: /"
    )
    parser.add_argument(
        "--min-volume", "-v", type=int, default=10,
        help="Minimum search volume to include. Default: 10"
    )
    parser.add_argument(
        "--min-cluster-size", type=int, default=2,
        help="Minimum keywords per cluster (smaller merged into nearest). Default: 2"
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}")
        sys.exit(1)

    output_path = args.output or str(input_path.with_suffix("")) + "_study.xlsx"

    print(f"\n{'='*60}")
    print(f"  SEO KEYWORD CLUSTER ENGINE")
    print(f"{'='*60}")
    print(f"  Input:     {input_path}")
    print(f"  Output:    {output_path}")
    print(f"  Threshold: {args.threshold}")
    print(f"  Base URL:  {args.base_url}")
    print(f"  Min vol:   {args.min_volume}")
    print(f"{'='*60}\n")

    # Step 1: Load
    print("[1/5] Loading CSV...")
    raw_df = load_csv(str(input_path))
    df = prepare_data(raw_df, min_volume=args.min_volume)

    if df.empty:
        print("ERROR: No keywords found after filtering.")
        sys.exit(1)

    # Step 2: Cluster
    print("\n[2/5] Clustering keywords...")
    df = cluster_keywords(df, threshold=args.threshold, min_cluster_size=args.min_cluster_size)

    # Step 3: Assign pages
    print("\n[3/5] Assigning pages & URLs...")
    df = assign_pages(df, base_url=args.base_url)

    # Step 4: Priority
    print("\n[4/5] Computing priorities...")
    df = compute_priority(df)

    # Step 5: Export
    print("\n[5/5] Generating Excel...")
    pages_df = generate_pages_sheet(df)
    keywords_df = generate_keywords_sheet(df)
    clusters_df = generate_clusters_sheet(df)

    export_excel(pages_df, keywords_df, clusters_df, output_path)

    # Summary
    total_vol = df["volume"].sum()
    n_clusters = df["cluster_id"].nunique()
    n_pages = df["page_id"].nunique()
    n_kw = len(df)

    print(f"\n{'='*60}")
    print(f"  RESUME")
    print(f"{'='*60}")
    print(f"  Mots-cles:  {n_kw:,}")
    print(f"  Clusters:   {n_clusters}")
    print(f"  Pages:      {n_pages}")
    print(f"  Volume:     {total_vol:,}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
