# Cours d'espagnol — Terminale A et Première A

Sources officielles : fiches de progression harmonisée MINESEC 2026-27
(`progression_terminale-a_espagnol_annuel.pdf`, 29 leçons ; `progression_premiere-a_espagnol_annuel.pdf`, 20 leçons).
Le texte des fiches a été transposé dans `pipeline/lessons.json` (titres, modules, contenus, savoir-faire).

| Classe | Dossier | Identifiants | Sortie | Leçons |
| --- | --- | --- | --- | --- |
| Terminale A | `espagnol-onbuch/` | `E01`…`E29` | `cours/Tle-A/` | 29 (5 modules) |
| Première A | `espagnol1-onbuch/` | `EA01`…`EA20` | `cours/1ere-A/` | 20 (5 modules) |

Avancement exact : `python3 docs/onbuch-generation/status.py espagnol`.

## Ce qui diffère des autres matières (maths, physique, chimie, SVT)

Les cours d'espagnol sont des **cours de langue** : explications, consignes, méthodes en **français** ;
textes, dialogues, poèmes, exemples, modèles de rédaction en **espagnol** (avec accents, `¿ ¡`),
suivis d'un glossaire ou d'une traduction française. Pas de formules mathématiques ni de courbes.

Adaptations faites à la pipeline copiée de `svt-onbuch` :

1. **`SYSTEM` (prompt système)** réécrit : professeur agrégé d'espagnol, contrat LaTeX adapté
   (boîtes, `\esp{}`, `textebox`), consignes de langue, pas d'API/IPA, textes **originaux**
   (pas de fausses citations ni de statistiques non vérifiables ; poèmes originaux ou vers célèbres
   du domaine public dont on est certain). Première A : niveau ≈ A2+/B1, textes de 150 à 250 mots.
2. **Types de leçon** (`"type"` dans `lessons.json`) : `texte` (lecture + lexique + commentaire),
   `traduction` (grammaire, thème/version), `mixte`. Le plan, les sections et les annexes s'adaptent.
3. **`preamble.tex`** :
   - `babel` en `[spanish,french]` (le français reste la langue principale) ;
   - `\esp{…}` : mot/phrase espagnole dans un paragraphe français (groupe `\selectlanguage{spanish}` + italique ;
     fonctionne sur plusieurs paragraphes et en mode math) ;
   - environnement `textebox[Source]` : cadre bleu « Texto » pour un texte/dialogue/poème espagnol ;
   - macros « tolérantes » car les modèles les inventent : `\cross`, `\xmark`, `\cmark`, `\justification`,
     `\textipa`.
4. **`autofix()`** enrichi (voir `TROUBLESHOOTING.md`).
5. **Annexes** réécrites : activité d'intégration (tâche de communication), méthodes (fiche + exercice
   résolu type examen), exercices (3 niveaux : connaissances / entraînement / préparation), corrigés
   rédigés en espagnol correct, fiche bilan (carte mentale + lexique + règles + checklist).

## Choix particuliers de la Première A

- Les fiches contiennent des **leçons « (suite) »** (EA08, EA12, EA16, EA20) : chacune est traitée comme
  une leçon à part entière qui **prolonge** la précédente par de la production (dialogue, rédaction,
  traduction), sans répéter le texte support. Voir leurs `savoirs` dans `lessons.json`.
- Les durées sont indicatives (« 2 h »).

## Vérifications qualité propres à l'espagnol

Lors de la relecture d'un bloc ou d'un PDF, contrôler :
- orthographe et accents espagnols (`más/mas`, `qué/que`, `sí/si`), `¿ ?` et `¡ !` bien appariés ;
- règles énoncées exactes (ser/estar, por/para, impératif avec enclise, subjonctif, concordance des temps) ;
- traductions françaises fidèles, faux amis signalés ;
- corrigés cohérents avec les exercices ;
- contexte camerounais/hispanophone plausible (Yaoundé, Douala, Guinée équatoriale…).

Un PDF témoin de bon rendu : `espagnol-onbuch/cours/Tle-A/E15-acuerdo-y-desacuerdo/`.

## Reste à faire (au moment de la passation)

- Terminale A : voir `status.py` (dernières leçons E26–E29 à finaliser/compiler).
- Première A : génération lancée ; si `build-cache.tar.gz` est présent dans `espagnol1-onbuch/`, le
  décompresser pour reprendre sans perte (voir `GIT.md`).
- **Recompilation finale** de tous les PDF de la Terminale A avec `TWO_PASS=1` (ou tectonic) pour
  uniformiser : les PDF produits avant les dernières corrections du préambule (`\esp` multi-paragraphes
  et mode math, `\justification`…) et ceux compilés en une seule passe (numéros de page « ?? »)
  doivent être refaits : `TWO_PASS=1 python3 pipeline/build.py` (toutes les leçons `DONE`).
