# SEO Cluster

**D'un export Semrush ou Ahrefs à une architecture de pages, en une commande.**

Tu donnes un CSV de mots-clés. Tu récupères un Excel qui dit quelles pages créer, quels mots-clés vont sur chaque page, quelle URL leur donner et par quoi commencer.

Pensé pour les consultants SEO qui font encore ce travail à la main dans un tableur.

> 🇬🇧 *Turns a Semrush/Ahrefs keyword export into a page plan (clusters, pages, URLs, priorities) as an Excel file. French-first, works with any language using Latin script.*

---

## Ce que tu obtiens

Un fichier Excel en 3 onglets :

| Onglet | Une ligne par… | Contenu |
|---|---|---|
| **Pages** | page à créer | cluster, priorité, URL proposée, titre, mot-clé principal, volume total de la page |
| **Mots-cles** | mot-clé | le cluster et la page auxquels il est rattaché, son rang dans la page, son niveau de concurrence |
| **Clusters** | cluster | volume total, nombre de pages, mot-clé pilier et URL pilier |

Exemple sur le fichier fourni (`sample_data.csv`, 141 mots-clés autour du canapé) : **14 clusters et 30 pages**.

```
Cluster               Priorité   Vol. total  Pages  KW pilier             URL pilier
Canape Convertible    ★★★ Haute     319 670      8  canapé convertible    /canapes/canape-convertible/
Canape Lit            ★★★ Haute      77 670      2  canapé lit            /canapes/canape-lit/
Canape Velours Cotele ★★★ Haute      47 510      5  canapé velours cotelé /canapes/canape-velours-cotele/
```

## Installation

Python 3 récent (testé avec Python 3.14).

```bash
git clone https://github.com/YoussefJlidi/seo-cluster.git
cd seo-cluster
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Utilisation

```bash
python seo_cluster.py mon_export.csv --base-url /canapes/
```

L'Excel est écrit à côté du CSV (`mon_export_study.xlsx`).

| Option | Défaut | Rôle |
|---|---|---|
| `--output`, `-o` | `<input>_study.xlsx` | chemin du fichier Excel |
| `--base-url`, `-b` | `/` | préfixe des URL proposées |
| `--threshold`, `-t` | `0.50` | seuil de regroupement (plus haut = moins de clusters) |
| `--min-volume`, `-v` | `10` | volume mensuel minimum pour garder un mot-clé |
| `--min-cluster-size` | `2` | les clusters plus petits sont fusionnés avec le plus proche |

### Format du CSV

Les colonnes sont détectées automatiquement (exports Semrush et Ahrefs, en français ou en anglais), avec n'importe quel séparateur (`,` `;` tabulation) et encodage.

- **Obligatoires** : mot-clé, volume
- **Facultatives** : difficulté (KD), CPC, URL, position, intention

## Comment ça marche

1. **Expression principale** : chaque mot-clé est ramené à son expression de tête (« canapé convertible pas cher » → « canapé convertible »). Les groupes dont l'un contient l'autre sont fusionnés.
2. **Regroupement sémantique** : les groupes restants sont rapprochés par similarité de vocabulaire (TF-IDF + classification hiérarchique).
3. **Découpage en pages** : dans chaque cluster, la page pilier prend le mot-clé le plus recherché. Un sous-groupe obtient sa propre page s'il pèse au moins 300 recherches/mois sur au moins 2 mots-clés. Sinon il reste sur la page pilier.
4. **Priorité** : score par cluster = 60 % volume + 40 % facilité (inverse de la difficulté moyenne). Les clusters sont ensuite répartis en tiers : Haute, Moyenne, Basse.

## Limites, à connaître avant de s'en servir

- **C'est une proposition, pas une décision.** Le regroupement repose sur les mots, pas sur la SERP. Deux synonymes (« spa » et « bien-être ») ne seront pas rapprochés. Deux requêtes proches en mots peuvent mériter deux pages différentes. Relis chaque cluster avant de lancer la rédaction.
- **L'outil ne connaît pas ton site.** Il ne vérifie pas si une page existe déjà sur le sujet. Avant de créer une page proposée, contrôle ton sitemap : améliorer une page existante vaut mieux que créer un doublon qui la cannibalise.
- Les titres de cluster sont générés sans accents (« Canape Lit »). À retoucher dans l'Excel.

## Licence

MIT. Libre d'usage, y compris commercial.

---

Fait par [Youssef Jlidi](https://youssef-jlidi.com), consultant SEO & GEO.
