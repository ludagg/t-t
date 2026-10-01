# Génération des cours OnBuch+ — documentation de passation

Ce dossier documente tout ce qu'il faut savoir pour **continuer la génération des cours
signés OnBuch+** (LaTeX → PDF, rédigés par des modèles NVIDIA) sans perdre de qualité.

| Fichier | Contenu |
| --- | --- |
| [`PROMPT_AGENT.md`](PROMPT_AGENT.md) | **Le prompt à donner à l'agent** qui prend le relais (à lire en premier) |
| [`PIPELINE.md`](PIPELINE.md) | Comment fonctionne la pipeline, commandes, variables d'environnement, cache |
| [`ESPAGNOL.md`](ESPAGNOL.md) | Spécificités des cours d'espagnol (Tle A et 1ère A), état d'avancement, décisions |
| [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) | Catalogue des erreurs LaTeX rencontrées et de leurs corrections |
| [`GIT.md`](GIT.md) | État des branches, fusion avec `main`, ce qui reste à faire côté Git |
| [`status.py`](status.py) | Script qui affiche l'avancement réel (blocs validés, leçons terminées, PDF) |
| [`tools/`](tools/) | `bal.py` (localise les accolades déséquilibrées), `tectonic-shim.sh` (remplace tectonic par xelatex) |

## Vue d'ensemble

Chaque matière/classe a son dossier `<matiere>-onbuch/` :

```
<matiere>-onbuch/
├── preamble.tex          # préambule LaTeX « Pop » OnBuch+ (boîtes, couleurs, page de garde)
├── fonts/                # polices (Plus Jakarta Sans, Archivo Black, Fira Math…)
├── pipeline/
│   ├── lessons.json      # catalogue des leçons (programme officiel MINESEC)
│   ├── generate.py       # génération par modèles (plan, sections, relecture, compilation d'essai)
│   ├── build.py          # assemblage + compilation des PDF
│   ├── check.py          # outil de correction manuelle / finalisation
│   └── tables.py         # détecteur de tableaux mal formés
├── build/                # CACHE de génération — NON versionné (.gitignore)
└── cours/<classe>/<id>-<slug>/<id>-<slug>.{tex,pdf}   # résultat final, versionné
```

Dossiers existants : `maths-onbuch`, `maths1-onbuch`, `physique-onbuch`, `physique1-onbuch`,
`chimie-onbuch`, `chimie1-onbuch`, `svt-onbuch`, `svt1-onbuch`, `info-onbuch`,
**`espagnol-onbuch`** (Terminale A) et **`espagnol1-onbuch`** (Première A).

## Principe de qualité

1. **Une leçon = un plan + des sections + des annexes** (activité d'intégration, méthodes et
   exercices résolus, exercices, corrigés, fiche bilan). Chaque bloc est rédigé par un modèle
   « rédacteur », relu par un modèle « relecteur », puis **compilé seul** avant d'être validé.
2. Les erreurs de compilation fréquentes sont corrigées **mécaniquement** par `autofix()`
   (sans appel au modèle). Les cas rares sont corrigés **à la main** (voir `TROUBLESHOOTING.md`).
3. Aucun bloc n'entre dans un PDF sans avoir compilé (`*.ok.tex`).

## Sécurité — clés API

Les clés NVIDIA (`nvapi-…`) ne doivent **jamais** être écrites dans un fichier du dépôt.
Elles se passent par la variable d'environnement `NVIDIA_API_KEYS` (séparées par des virgules).
Plusieurs clés ont été collées en clair dans des conversations : **les révoquer et en créer de
nouvelles**. Plusieurs clés deviennent des erreurs `403` quand elles sont révoquées.
