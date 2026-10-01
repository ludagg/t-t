# Pipeline de génération — mode d'emploi

## Prérequis

- Python 3.11+, paquet `requests` (`pip install requests`).
- Un compilateur LaTeX :
  - **idéal : `tectonic`** (utilisé par `generate.py` et `build.py`, variable `TECTONIC`) ;
  - **secours : `xelatex`** (TeX Live : `texlive-xetex texlive-latex-extra texlive-pictures
    texlive-science texlive-lang-french texlive-lang-spanish texlive-fonts-extra`) via le script
    [`tools/tectonic-shim.sh`](tools/tectonic-shim.sh) :
    ```bash
    mkdir -p ~/bin && cp docs/onbuch-generation/tools/tectonic-shim.sh ~/bin/tectonic
    chmod +x ~/bin/tectonic && export PATH=~/bin:$PATH
    ```
    Le shim fait **une passe** (rapide, pour la génération). Pour `build.py`, exporter
    `TWO_PASS=1` : deuxième passe → numéros de page « N / total » corrects (sinon « ?? »).
- Clés NVIDIA : `export NVIDIA_API_KEYS="cle1,cle2,…"` (jamais dans un fichier).
- Dans l'environnement où cette pipeline a été écrite, le module Python `cryptography` système
  était cassé : utiliser un venv (`python3 -m venv v && v/bin/pip install pypdf pymupdf requests`).

## Les 3 commandes

Toujours depuis le dossier de la matière (ex. `cd espagnol1-onbuch`) :

```bash
# 1. Générer (reprend là où ça s'est arrêté grâce au cache build/)
NVIDIA_API_KEYS="..." WORKERS=4 MAX_CONCURRENT=8 python3 pipeline/generate.py          # toutes les leçons
NVIDIA_API_KEYS="..." python3 pipeline/generate.py EA05 EA06                           # certaines leçons

# 2. Corriger / finaliser
python3 pipeline/check.py                     # liste les blocs en erreur (*.err)
python3 pipeline/check.py EA05/s03            # recompile build/EA05/s03.rev.tex ; valide si OK
python3 pipeline/check.py --finalize          # marque DONE les leçons dont tous les blocs sont validés

# 3. Assembler et compiler les PDF
TWO_PASS=1 WORKERS=4 python3 pipeline/build.py EA05 EA06     # ou sans argument : toutes les leçons DONE
```

Variables : `WORKERS` (leçons en parallèle, défaut 6), `MAX_CONCURRENT` (appels simultanés par
modèle, défaut 12), `WRITER_MODELS`, `REVIEW_MODELS` (défaut : `moonshotai/kimi-k3` et
`nvidia/nemotron-3-ultra-550b-a55b`), `TECTONIC`.

## Étapes d'une leçon (`generate.py`)

1. `plan.json` — plan détaillé (JSON) calé sur `lessons.json`. Réessaie 3 fois si le JSON est invalide.
2. `sNN.tex` — rédaction de chaque section (rédacteur). Réessaie jusqu'à 3 fois si la sortie est
   dégénérée (trop courte ou caractère répété à l'infini).
3. `sNN.rev.tex` — relecture (relecteur). Rejetée (version du rédacteur conservée) si elle est
   trop courte (< 70 %) ou gonflée (> 2,5× ; > 1,6× pour bilan/corrigés/extras).
4. Compilation d'essai du bloc seul (`autofix()` d'abord). OK → `sNN.ok.tex`.
   Échec → `sNN.err` (« À CORRIGER ») et le bloc reste à corriger **à la main**.
5. Annexes : `activite`, `methodes`, `exercices`, `corriges` (dépend de `exercices`), `bilan`.
6. Quand tous les blocs sont validés : fichier `build/<id>/DONE`.

`build.py` assemble `plan.json` + blocs `*.ok.tex` (réapplique `autofix()`), écrit
`cours/<classe>/<id>-<slug>/<id>-<slug>.tex` et compile en PDF.

## Cache `build/` (non versionné)

`build/<id>/` contient plan, brouillons (`.tex`), relectures (`.rev.tex`), blocs validés
(`.ok.tex`), erreurs (`.err`) et `DONE`. **Il n'est pas dans Git** (`.gitignore`). Conséquences :

- Les leçons **terminées** sont dans `cours/` (versionnées) : rien à refaire.
- Pour les leçons **en cours**, une archive du cache est fournie : `build-cache.tar.gz`
  dans chaque dossier de matière concerné (voir `GIT.md`). La décompresser **dans le dossier de la
  matière** : `cd espagnol1-onbuch && tar xzf build-cache.tar.gz`. Sans elle, les leçons
  non terminées sont regénérées depuis zéro (≈ 20–35 min par leçon).

## Pièges opérationnels (appris à la dure)

- **Ne jamais** lancer `pkill -f generate.py` depuis un shell dont la ligne de commande contient
  « generate.py » : on se tue soi-même. Utiliser `pgrep -f "^python3 pipeline/generate"` puis `kill <pid>`.
- Le générateur lit `lessons.json`, mais le **code Python est chargé au démarrage** : une correction
  de `generate.py` ne vaut que pour les processus lancés après. Relancer pour en profiter
  (le cache évite de tout refaire).
- `check.py <bloc>` **réécrit** `build/.../bloc.rev.tex` avec la version après `autofix()`.
  Si on édite à la main un `.rev.tex`, repartir du fichier actuel, pas d'un numéro de ligne mémorisé.
- Quand la **relecture** a corrompu un bloc (texte tronqué, doublé, raisonnement du modèle collé
  dans le LaTeX, structure de listes cassée), la solution la plus sûre est de **recopier le brouillon
  du rédacteur** : `cp build/<id>/<bloc>.tex build/<id>/<bloc>.rev.tex` puis `check.py <id>/<bloc>`.
- Un bloc dont le `.ok.tex` fait moins de ~5 Ko alors que les autres en font 10–30 est suspect
  (sortie dégénérée). Le supprimer (`rm build/<id>/<bloc>*` et aussi `corriges*` si c'est `exercices`)
  et relancer `generate.py <id>`.
- Erreurs réseau : `429` (quota → attente automatique), `403` (clé révoquée → en fournir une autre),
  `502` ou réponse vide (nouvel essai automatique).
- `git status` doit rester propre : le hook de fin de session demande de committer/pousser.
  Ne versionner que `cours/**/*.tex` et `*.pdf` (les `.aux/.out/.log` sont ignorés).

## Avancement

`python3 docs/onbuch-generation/status.py` affiche, pour chaque dossier `*-onbuch`, les leçons
terminées, en cours, les blocs en erreur et les PDF présents.
