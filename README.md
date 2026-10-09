# SEO Cluster

🇬🇧 English · 🇫🇷 [Version française](README.fr.md)

**From a Semrush or Ahrefs keyword export to a page architecture, in one command.**

You give it a keyword CSV. You get back an Excel file that tells you which pages to create, which keywords go on each page, what URL to give them, and where to start.

Built for SEO consultants who still do this by hand in a spreadsheet. French-first, works with any language written in Latin script.

---

## What you get

An Excel file with 3 sheets:

| Sheet | One row per… | Content |
|---|---|---|
| **Pages** | page to create | cluster, priority, proposed URL, title, main keyword, total page volume |
| **Mots-cles** | keyword | the cluster and page it is assigned to, its rank within the page, its competition level |
| **Clusters** | cluster | total volume, number of pages, pillar keyword and pillar URL |

Sheet names and column headers in the output are in French.

On the bundled file (`sample_data.csv`, 141 sofa-related keywords): **14 clusters and 30 pages**.

```
Cluster               Priority   Total vol.  Pages  Pillar keyword         Pillar URL
Canape Convertible    ★★★ High      319,670      8  canapé convertible    /canapes/canape-convertible/
Canape Lit            ★★★ High       77,670      2  canapé lit            /canapes/canape-lit/
Canape Velours Cotele ★★★ High       47,510      5  canapé velours cotelé /canapes/canape-velours-cotele/
```

(Illustrative: in the actual file, priority labels and headers are in French.)

## Installation

Recent Python 3 (tested with Python 3.14).

```bash
git clone https://github.com/YoussefJlidi/seo-cluster.git
cd seo-cluster
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python seo_cluster.py my_export.csv --base-url /sofas/
```

The Excel file is written next to the CSV (`my_export_study.xlsx`).

| Option | Default | Purpose |
|---|---|---|
| `--output`, `-o` | `<input>_study.xlsx` | path of the Excel file |
| `--base-url`, `-b` | `/` | prefix for the proposed URLs |
| `--threshold`, `-t` | `0.50` | grouping threshold (higher = fewer clusters) |
| `--min-volume`, `-v` | `10` | minimum monthly volume to keep a keyword |
| `--min-cluster-size` | `2` | smaller clusters are merged into the closest one |

Try it on the sample:

```bash
python seo_cluster.py sample_data.csv -o out.xlsx -b /canapes/
```

### CSV format

Columns are detected automatically (Semrush and Ahrefs exports, in French or English), with any separator (`,` `;` tab) and any encoding.

- **Required**: keyword, volume
- **Optional**: difficulty (KD), CPC, URL, position, intent

## How it works

1. **Head phrase**: each keyword is reduced to its head phrase ("canapé convertible pas cher" becomes "canapé convertible"). Groups where one contains the other are merged.
2. **Semantic grouping**: the remaining groups are brought together by vocabulary similarity (TF-IDF + hierarchical clustering).
3. **Page split**: within each cluster, the pillar page takes the highest-volume keyword. A sub-group gets its own page if it totals at least 300 searches/month across at least 2 keywords. Otherwise it stays on the pillar page.
4. **Priority**: cluster score = 60% volume + 40% ease (inverse of average difficulty). Clusters are then split into tiers: High, Medium, Low.

## Limitations

- **It is a proposal, not a decision.** Grouping is based on words, not on the SERP. Two synonyms ("spa" and "wellness") will not be grouped together. Two queries that look close in wording may deserve two different pages. Review every cluster before commissioning content.
- **The tool does not know your site.** It does not check whether a page on the topic already exists. Before creating a proposed page, check your sitemap: improving an existing page beats creating a duplicate that cannibalizes it.
- Cluster titles are generated without accents ("Canape Lit"). Edit them in the Excel file.
- Console messages and the Excel output are in French.

## License

MIT. Free to use, including commercially.
