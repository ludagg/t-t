# État Git et fusion

## Branches du dépôt `ludagg/t-t` (au moment de la passation)

| Branche | Contenu |
| --- | --- |
| `main` | **Application seule** (Dokta/PharmaConnect : `src/`, `api/`, `server.ts`…), 19 commits, historique propre |
| `claude/chemistry-latex-pdf-courses-p0a1gi` | Cours LaTeX : maths, physique, chimie, SVT, info (pipelines + PDF) — 50 commits d'avance |
| **`ccr-bf7f5830-fwu7b5`** | **Branche de travail à jour** : tout ce qui précède **+** espagnol Tle A et 1ère A **+** cette documentation |
| autres `claude/*` | sans rapport avec les cours |

Constat (vérifié) :

- Les fichiers de l'application sont **strictement identiques** entre `main` et `ccr-bf7f5830-fwu7b5`
  (`git diff` vide sur `src/ api/ package.json …`). La seule différence est la présence des dossiers `*-onbuch`.
- `main` et les branches de cours avaient des **historiques sans ancêtre commun** (dépôt re-créé depuis un
  fork). La branche `ccr-bf7f5830-fwu7b5` a été **fusionnée avec `origin/main`**
  (`git merge --allow-unrelated-histories`) : fusion sans conflit et **sans aucune modification de fichier**.
  Elle contient désormais l'historique de `main` **et** celui de la branche chimie
  (`git merge-base --is-ancestor origin/main HEAD` et idem pour la branche chimie : vrai).
- Conséquence : `main` et `claude/chemistry-latex-pdf-courses-p0a1gi` peuvent être amenées à
  `ccr-bf7f5830-fwu7b5` par **simple avance rapide (fast-forward)**, donc **sans perte**.

## Publication — FAITE (commit `d211f78`)

Après « Sync fork » sur GitHub, la branche chimie contenait 49 commits supplémentaires (cours du dépôt
d'origine : info, philo, sciences, svteehb, TD…). Elle a été fusionnée dans `ccr-bf7f5830-fwu7b5`
(sans conflit, aucune suppression), puis **`claude/chemistry-latex-pdf-courses-p0a1gi` et `main` ont été
avancées par avance rapide** (sans `--force`) jusqu'à ce commit. Les trois branches sont identiques à `d211f78`.

Pour la suite : continuer à travailler sur `ccr-bf7f5830-fwu7b5`, pousser les nouveaux cours là, et publier
sur la branche chimie et `main` **uniquement après accord du propriétaire** :

```bash
git fetch origin
git push origin ccr-bf7f5830-fwu7b5:claude/chemistry-latex-pdf-courses-p0a1gi   # avance rapide
git push origin ccr-bf7f5830-fwu7b5:main                                        # avance rapide
```

Si Git refuse (« non-fast-forward »), quelqu'un a poussé entre-temps : `git merge origin/<branche>` puis réessayer.
Ne jamais forcer. Attention : un push sur `main` déclenche un déploiement Vercel de l'application.

## Cours « de l'autre dépôt » (historique)

Ces cours (dépôt `andersonclement/t-t`, dont `ludagg/t-t` est un fork) ont été récupérés via « Sync fork » de la branche
chimie puis fusionnés (voir ci-dessus). Si d'autres branches d'origine doivent être intégrées, procédure :

```bash
git remote add initial https://github.com/<ancien-compte>/<ancien-depot>
git fetch initial
git merge --allow-unrelated-histories initial/<branche>   # puis résoudre d'éventuels conflits
```

Les dossiers `*-onbuch` portent des noms distincts par matière/classe : les conflits ne devraient concerner
que `README.md`, `.gitignore` ou `package*.json`. En cas de doublon d'un même cours, **garder la version
dont le PDF est le plus récent et complet**, jamais supprimer de PDF sans l'avoir vérifié.

## Ce qui est versionné / ignoré

- Versionné : `pipeline/`, `preamble.tex`, `fonts/`, `cours/**/*.tex`, `cours/**/*.pdf`, `docs/`.
- Ignoré (`.gitignore` de chaque dossier `*-onbuch`) : `build/` (cache), `__pycache__/`, `*.log`, `*.aux`, `*.out`, `*.toc`.
- Cache des leçons **non terminées** : `build-cache.tar.gz` dans le dossier de la matière
  (créé à la passation, voir ci-dessous) — **versionné uniquement pour la reprise** ; le supprimer quand
  toutes les leçons sont terminées.

```bash
# créer l'archive du cache (à faire avant de passer la main)
cd espagnol1-onbuch && tar czf build-cache.tar.gz build && cd ..
# la reprendre
cd espagnol1-onbuch && tar xzf build-cache.tar.gz
```

## Règles de commit utilisées

- Messages clairs en français, une pièce de travail par commit (« leçons E13, E14 et E18 compilées »,
  « autofix … »).
- Terminer les messages de commit par les lignes d'attribution demandées par l'environnement.
- Pousser souvent (`git push -u origin <branche>`) : le conteneur de travail est éphémère.
- Jamais de clé API dans un commit ; vérifier avec `grep -rn "nvapi-" . --exclude-dir=build --exclude-dir=.git`.
