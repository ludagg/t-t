# Prompt à donner à l'agent qui reprend la génération

> Copier-coller le bloc ci-dessous comme première consigne. Fournir séparément (jamais dans le dépôt)
> les clés NVIDIA : `NVIDIA_API_KEYS="cle1,cle2,..."`.

---

````text
Tu reprends un chantier de génération de cours LaTeX/PDF « signés OnBuch+ » pour le Cameroun
(programme officiel MINESEC), dans le dépôt GitHub ludagg/t-t. Travaille sur la branche
`ccr-bf7f5830-fwu7b5` (c'est la branche à jour : elle contient aussi l'historique de `main`).

# 0. Lis d'abord, dans l'ordre
1. docs/onbuch-generation/README.md      (vue d'ensemble)
2. docs/onbuch-generation/PIPELINE.md    (commandes, cache, pièges)
3. docs/onbuch-generation/ESPAGNOL.md    (spécificités des cours d'espagnol)
4. docs/onbuch-generation/TROUBLESHOOTING.md (erreurs LaTeX connues et remèdes)
5. docs/onbuch-generation/GIT.md         (branches, fusion, ce qui reste à publier)
Puis lance `python3 docs/onbuch-generation/status.py espagnol` pour voir l'avancement réel.

# 1. Mission
Terminer les deux séries de cours d'espagnol, en conservant EXACTEMENT la qualité et la méthode déjà
en place :
 a) Terminale A  → dossier `espagnol-onbuch/` (E01…E29, 29 leçons). Finaliser, compiler et pousser
    les leçons restantes (voir status.py), puis recompiler TOUS les PDF avec `TWO_PASS=1` pour
    uniformiser (préambule mis à jour, numéros de page corrects).
 b) Première A   → dossier `espagnol1-onbuch/` (EA01…EA20, 20 leçons). Si `espagnol1-onbuch/build-cache.tar.gz`
    existe, le décompresser dans ce dossier pour reprendre sans perte. Générer, corriger, compiler.
Chaque leçon terminée = un dossier `cours/<classe>/<id>-<slug>/` avec le `.tex` et le `.pdf`, poussés.

# 2. Environnement à mettre en place
- Python 3.11, `pip install requests` (utilise un venv si le module `cryptography` système est cassé).
- Compilateur : `tectonic` si possible ; sinon installe TeX Live (xetex, latex-extra, pictures, science,
  lang-french, lang-spanish, fonts-extra) et utilise docs/onbuch-generation/tools/tectonic-shim.sh comme
  commande `tectonic` (copie-le dans ton PATH). `TWO_PASS=1` pour build.py.
- Clés : `export NVIDIA_API_KEYS="cle1,cle2,..."`. Ne les écris JAMAIS dans un fichier ni un commit.
  Teste-les (`curl` sur https://integrate.api.nvidia.com/v1/chat/completions, modèle
  nvidia/nemotron-3-ultra-550b-a55b) : un 403 = clé révoquée, demande-en une autre.

# 3. Boucle de travail (répète jusqu'à ce que tout soit fini)
1. Lancer la génération en arrière-plan, depuis le dossier de la matière :
   `WORKERS=4 MAX_CONCURRENT=8 nohup python3 pipeline/generate.py [IDs…] > /tmp/gen.log 2>&1 &`
   (reprend grâce au cache build/ ; ~20–35 min par leçon ; surveiller le log : « À CORRIGER », « ÉCHEC », « terminé »).
2. Corriger les blocs en erreur : `python3 pipeline/check.py` puis `python3 pipeline/check.py <id>/<bloc>`.
   Diagnostic : docs/onbuch-generation/tools/bal.py. Catalogue d'erreurs : TROUBLESHOOTING.md.
   - Si une même erreur revient : ajoute une règle à `autofix()` (generate.py, dans espagnol-onbuch ET
     espagnol1-onbuch), teste la non-régression sur 5–6 blocs déjà validés, committe.
   - Si la relecture a corrompu un bloc : recopie le brouillon (`cp bloc.tex bloc.rev.tex`).
   - Si un bloc validé est anormalement petit (< ~5 Ko) : sortie dégénérée → supprime-le (et corriges*
     si c'est exercices) et relance generate.py pour cette leçon.
3. `python3 pipeline/check.py --finalize` puis `TWO_PASS=1 python3 pipeline/build.py <ids>`.
4. Vérifie visuellement au moins un PDF par lot (page de garde, un texte espagnol dans la boîte « Texto »,
   un tableau, une figure). Contrôle l'espagnol : accents, ¿ ¡, règles de grammaire, traductions, corrigés.
5. Commit + push à chaque lot terminé :
   `git add <matiere>-onbuch/cours/<classe>/*/*.tex <matiere>-onbuch/cours/<classe>/*/*.pdf`
   Message en français, une pièce de travail par commit, avec les lignes d'attribution requises.
   `git push -u origin ccr-bf7f5830-fwu7b5` (réessaie avec attente si erreur réseau).
   Le dépôt doit toujours être « propre » (git status vide) quand tu t'arrêtes.

# 4. Exigences de qualité (non négociables)
- Aucun bloc dans un PDF sans compilation réussie ; aucun contenu inventé de façon trompeuse
  (pas de fausses citations, pas de statistiques précises non vérifiables).
- Respect du programme : les titres et contenus viennent de `pipeline/lessons.json` (fiche officielle).
- Les cours sont en français + espagnol ; Première A = niveau A2+/B1, Terminale A = B1.
- Pas de clé API, pas de fichier temporaire (.aux/.out/.log) dans Git.
- Ne modifie PAS l'application (src/, api/, server.ts, package.json…) ni les autres matières,
  sauf demande explicite.

# 5. Fin de mission
Quand status.py indique 29/29 (Terminale A) et 20/20 (Première A) avec PDF :
 - supprime `espagnol1-onbuch/build-cache.tar.gz` (et tout autre build-cache) du dépôt ;
 - mets à jour docs/onbuch-generation/ESPAGNOL.md (section « Reste à faire ») ;
 - fais le point avec le propriétaire AVANT de publier sur `main` et sur la branche
   `claude/chemistry-latex-pdf-courses-p0a1gi` (voir GIT.md : avance rapide, sans perte) ;
 - ne force-pousse jamais (`--force`) et ne supprime aucune branche.
````

---

## Notes pour la personne qui lance l'agent

- Donner à l'agent **l'accès en écriture** au dépôt `ludagg/t-t` (ou à son fork), et la branche
  `ccr-bf7f5830-fwu7b5`.
- Fournir **plusieurs clés NVIDIA valides** : la génération est limitée par le quota (erreurs 429) ;
  avec 2 à 4 clés on obtient ≈ 4–6 leçons en parallèle.
- Budget indicatif : ≈ 20 à 35 minutes par leçon et par worker ; Première A (20 leçons) ≈ 2 à 3 h
  avec 4–5 workers ; corrections manuelles ≈ 3 à 5 blocs par lot de 5 leçons.
- Les clés collées dans des conversations doivent être considérées comme compromises : les révoquer
  après usage.
