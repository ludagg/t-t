# Prompt : démarrer une NOUVELLE matière (programme annuel fourni en PDF)

> Copier-coller le bloc ci-dessous comme première consigne, en remplaçant les `<…>`.
> Fournir séparément (jamais dans le dépôt, jamais dans un commit) les clés :
> `export NVIDIA_API_KEYS="cle1,cle2,..."` (séparées par des virgules).

---

````text
Tu reprends un chantier de génération de cours LaTeX/PDF « signés OnBuch+ » pour le Cameroun
(programmes officiels MINESEC) dans le dépôt GitHub ludagg/t-t. Branche de travail :
`ccr-bf7f5830-fwu7b5` (à jour ; `main` lui est identique au commit 95f1b05).
Matière à produire : <MATIÈRE>, classes <Terminale A / Première A / …>. Programme : <fichier(s) PDF joint(s)>.

# 0. Lis d'abord
docs/onbuch-generation/README.md, PIPELINE.md, TROUBLESHOOTING.md, GIT.md, ESPAGNOL.md,
puis ce fichier-ci (§ 6 « Leçons apprises »). Ne modifie ni l'application (src/, api/, server.ts,
package.json…) ni les autres matières, sauf demande explicite.

# 1. Principe : une matière = un dossier-pipeline cloné
Chaque matière/classe a son dossier `<matiere>[1]-onbuch/` (suffixe 1 = Première). Modèles à cloner,
du plus proche de ta matière au plus éloigné :
 - langue romane/germanique : `espagnol-onbuch`, `allemand-onbuch`, `anglais-onbuch` (+ versions `1`)
 - langue à écriture non latine : `chinois-onbuch`, `chinois1-onbuch` (xeCJK, pinyin, police CJK)
 - sciences/maths/etc. : `maths-onbuch`, `physique-onbuch`, `chimie-onbuch`, `svt-onbuch`, `philo-onbuch`, `info-onbuch`…
Contenu d'un dossier : `pipeline/{generate.py,build.py,check.py,tables.py,lessons.json}`, `preamble.tex`,
`fonts/`, `.gitignore`, `cours/<Tle-A|1ere-A>/<ID>-<slug>/{.tex,.pdf}`, `REPARTITION-<Classe>.pdf|tex`.
`build/` (cache) est gitignoré : seuls `cours/**/*.tex|pdf`, pipelines, préambules, polices et documents
de répartition sont versionnés.

# 2. Étapes pour une nouvelle matière
1. Extraire le texte du PDF de progression (pdftotext / pymupdf) et en tirer, pour CHAQUE leçon de la
   fiche : id (préfixe 2-3 lettres + numéro ; ex. ZH01, ZHA01), titre exact, durée/heures, objectifs,
   contenus, capacités, éventuelles lignes « intégration/évaluation ». Écrire `pipeline/lessons.json`
   (voir le format dans un dossier existant ; les titres viennent TOUJOURS de la fiche officielle).
2. Cloner le dossier modèle le plus proche, renommer les ids/chemins, adapter dans `generate.py` les
   prompts (langue, niveau CECRL/pédagogique, vocabulaire, consignes de rigueur de la matière) et
   `preamble.tex` (titre, couleurs si besoin). Ne touche pas à la structure des blocs (plan →
   sections sNN → relecture `.rev.tex` → `autofix()` + test de compilation → `.ok.tex` ; annexes
   activite / methodes / exercices / corriges / bilan).
3. Lancer la génération (cf. § 4), corriger, finaliser, compiler, vérifier, pousser, répéter.
4. Générer le document `REPARTITION-<Classe>` (tableau leçons ↔ pages ↔ heures, scripts de type
   `mk_rep_*.py`, cf. `chinois-onbuch/REPARTITION-Terminale-A.tex` comme modèle) et le commiter.
5. Fin : zips de livraison, point avec le propriétaire (§ 5).

# 3. Environnement (conteneur éphémère : à refaire à chaque redémarrage)
- Python 3 système avec `requests` (le venv sert pour pypdf/pymupdf/fonttools ; certains `python3` ont un
  module `cryptography` cassé → utiliser /usr/bin/python3 pour le pipeline, le venv pour les scripts PDF).
- Compilateur : TeX Live (xetex, latex-extra, pictures, science, lang-french + langue concernée ;
  `texlive-lang-chinese` pour le chinois) et le shim `docs/onbuch-generation/tools/tectonic-shim.sh`
  copié en `tectonic` dans le PATH (mettre /usr/bin en tête du PATH). `TWO_PASS=1` pour `build.py`
  afin d'avoir les bons totaux de pages.
- Clés NVIDIA : modèles `moonshotai/kimi-k3` et `nvidia/nemotron-3-ultra-550b-a55b`. Un 403 = clé
  révoquée. Ne jamais écrire une clé dans un fichier, un log versionné ou un message de commit.

# 4. Boucle de travail
1. Depuis le dossier de la matière : `WORKERS=4 MAX_CONCURRENT=8 nohup python3 pipeline/generate.py [IDs…] > /tmp/gen.log 2>&1 &`
   (reprend grâce au cache `build/` ; 20–35 min par leçon ; surveiller « À CORRIGER », « ÉCHEC », « terminé »).
   Si le conteneur redémarre, relance la même commande : rien n'est perdu.
2. `python3 pipeline/check.py` liste les blocs en erreur ; `check.py <id>/<bloc>` revalide un bloc ;
   `check.py --finalize` pose le drapeau DONE d'une leçon complète. ATTENTION : le `generate.py` en cours
   garde en mémoire l'ancienne version de `autofix()` → beaucoup d'« À CORRIGER » sont de fausses alertes
   qui passent dans un `check.py` neuf. Valide toujours avec `check.py <id>/<bloc>` explicite.
   `check.py` réécrit le texte autocorrigé dans le `.rev.tex` (donc une retouche manuelle qui ressemble
   à une règle d'autofix peut être « réparée » à l'envers : écris des constructions simples et fermées).
3. Erreur récurrente → ajoute une règle à `autofix()` dans TOUS les `generate.py` de la matière
   (classes Terminale et Première), teste la non-régression sur 5-6 blocs déjà validés, commite.
   Bloc corrompu par la relecture → recopie le brouillon (`cp bloc.tex bloc.rev.tex`).
   Bloc validé anormalement petit (< ~5 Ko) = sortie dégénérée → supprime-le et relance la leçon.
4. `check.py --finalize`, puis `TWO_PASS=1 python3 pipeline/build.py <ids>` (compile + assemble).
5. Vérifie visuellement au moins un PDF par lot (page de garde, un tableau, une figure, une boîte de texte).
6. Commit + push à chaque lot : `git add <matiere>-onbuch/cours/<classe>/*/*.tex …/*.pdf`, message en
   français, terminé par les lignes d'attribution demandées par l'environnement ; `git push -u origin
   ccr-bf7f5830-fwu7b5` (réessaie avec attente si erreur réseau). Dépôt propre à chaque arrêt.
   Un script de cycle (check → finalize → build des leçons DONE → audit des logs → commit/push) évite de
   répéter ces gestes.

# 5. Règles de livraison (accord EXPLICITE du propriétaire requis)
- Envoi des zips sur tmpfiles.org (`curl -F "file=@x.zip" https://tmpfiles.org/api/v1/upload`, lien à
  donner avec `/dl/`, ~1 h de rétention, ~30 Mo max) : seulement si le propriétaire le demande.
- Publication sur `main` : seulement sur demande, en avance rapide (`git merge-base --is-ancestor origin/main HEAD`
  puis `git push origin ccr-bf7f5830-fwu7b5:main`). Un push sur `main` déclenche un déploiement Vercel.
  Jamais `--force`, jamais de suppression de branche, pas de PR sans demande. La branche
  `claude/chemistry-latex-pdf-courses-p0a1gi` reste à d211f78 sauf demande.
- Aucune matière ne se déclare « terminée » avec un bloc non compilé. Rappelle au propriétaire de
  révoquer les clés NVIDIA collées en conversation.

# 6. Leçons apprises (à lire avant de commencer)
- Contenu : la relecture IA ne remplace pas un enseignant. Points faibles connus : décompositions de
  caractères et ordre des traits (chinois), cas/déclinaisons rares (allemand), faits chiffrés. Interdis
  les fausses citations / statistiques non vérifiables et signale au propriétaire qu'une relecture
  humaine est nécessaire avant diffusion.
- Erreurs LaTeX fréquentes du modèle : accolades déséquilibrées ou bloc tronqué ; tableaux au mauvais
  nombre de colonnes ; `\\` dans un groupe/`\zh{}` ; `\centering` dans une cellule ; pseudo-tableaux avec
  `\hline` ; TikZ (foreach avec virgules, `\label` comme variable, mindmap sans accolades, `\n` littéral
  dans un nœud) ; commandes/environnements inventés (`\fr \blank \vs \zhbf \courssubsub`, env `zhbox`)
  – tolérés par le préambule chinois ; `\+`, `\"` ; caractères coréens/arabes/cyrilliques hallucinés ;
  U+FFFD. Beaucoup sont déjà traités par `autofix()` : relis-le avant d'écrire une nouvelle règle.
- Glyphes manquants : chaque préambule charge DejaVu Sans en police de secours (IPA U+0250–02FF,
  dingbats, ✓ ✗ ☐, etc. via `\symactive`) ; les emojis (U+1F300–1FAFF) sont blanchis ; ✅→✓. Le test
  de compilation du chinois signale tout caractère CJK absent de la police (« Missing character »).
  Audite les `.log` avec `grep "Missing character"` avant de livrer.
- Chinois : xeCJK + sous-ensemble WenQuanYi Zen Hei (`fonts/WenQuanYiZenHei-subset.ttf`, ~7,6 Mo),
  macro `\zh{}`, pinyin avec tons (ǎ ǐ ǒ ǔ ǖ…) gérés par caractères actifs ; caractères hors plan
  basique remplacés par « (trait brisé) » ; garde-fou « caractère suspect » restreint à
  cyrillique/arabe/hangul. Pour une autre langue non latine, reprends cette architecture.
- Crash silencieux de `generate.py` (redémarrage du conteneur) : relancer, le cache est intact.
- Ne jamais supposer qu'une alerte « À CORRIGER » est vraie sans `check.py <id>/<bloc>`.

# 7. Fin de mission pour une matière
`status.py <matiere>` (docs/onbuch-generation/status.py) doit montrer N/N leçons avec PDF dans chaque
classe ; documents de répartition générés ; dépôt propre et poussé ; docs/onbuch-generation/ mis à jour
(ajoute `<MATIERE>.md` avec les spécificités et la section « Reste à faire ») ; point avec le
propriétaire AVANT toute publication sur `main`.
````
