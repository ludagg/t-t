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

## Étapes de publication (à faire une fois la génération terminée, ou par le propriétaire)

> Non encore exécutées : elles modifient `main`. À lancer seulement avec l'accord du propriétaire du dépôt.

```bash
git fetch origin
git checkout ccr-bf7f5830-fwu7b5 && git pull origin ccr-bf7f5830-fwu7b5

# 1. Branche « chimie » (tous les cours) — avance rapide
git push origin ccr-bf7f5830-fwu7b5:claude/chemistry-latex-pdf-courses-p0a1gi

# 2. main — avance rapide (refusée par Git si quelqu'un a poussé entre-temps : refaire un fetch/merge)
git push origin ccr-bf7f5830-fwu7b5:main
```

Si `main` a reçu de nouveaux commits entre-temps : `git merge origin/main` sur la branche de travail
(aucun conflit attendu tant que ces commits ne touchent pas aux dossiers `*-onbuch`), puis pousser.

## Cours « de l'autre dépôt » non encore chargés

Le propriétaire a indiqué que des cours générés dans un **dépôt initial (autre compte GitHub)** n'ont pas été
chargés dans ce dépôt (ce dépôt est un fork recréé). Cet agent n'a eu accès qu'à `ludagg/t-t` : ces cours-là
**ne sont pas ici**. Pour les intégrer sans perte :

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
