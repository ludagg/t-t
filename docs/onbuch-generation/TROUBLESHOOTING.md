# Catalogue des erreurs LaTeX et de leurs corrections

Toutes les corrections « mécaniques sûres » vivent dans `autofix()` de
`<matiere>-onbuch/pipeline/generate.py` (appelée par `generate.py`, `check.py` **et** `build.py`).
Quand une nouvelle erreur revient plusieurs fois, **ajouter une règle à `autofix()`** plutôt que
de corriger à la main bloc par bloc — puis relancer `check.py` et vérifier qu'aucun bloc déjà validé
ne régresse (voir « Test de non-régression »).

## Méthode de diagnostic

```bash
python3 pipeline/check.py                       # blocs en erreur
python3 pipeline/check.py E12/s03               # recompile et affiche l'erreur + le contexte
python3 docs/onbuch-generation/tools/bal.py build/E12/s03.rev.tex   # lignes où les accolades { } sont déséquilibrées
```

La ligne indiquée est celle **où TeX s'aperçoit** du problème, souvent bien après la cause réelle
(accolade oubliée, environnement non refermé). `bal.py` montre où la profondeur d'accolades ne revient
pas à zéro ; le script de vérification d'environnements est reproduit ci-dessous.

```python
import re, sys
t = open(sys.argv[1]).read(); st = []
for m in re.finditer(r'\\(begin|end)\{([^}]+)\}', t):
    ln = t[:m.start()].count('\n') + 1
    if m.group(1) == 'begin': st.append((m.group(2), ln))
    elif st and st[-1][0] == m.group(2): st.pop()
    else: print('mismatch', m.group(2), ln, st[-2:])
print('open:', st)
```

## Erreurs fréquentes → cause → remède

| Message TeX | Cause habituelle | Remède |
| --- | --- | --- |
| `Undefined control sequence` sur `\child` | Mindmap TikZ : le modèle écrit `\child[...]` | autofix : `\child` → `child` ; supprimer le `;` qui termine le nœud racine avant les `child` |
| `Undefined control sequence` `\step`, `\justification`, `\cross`, `\textipa` | Commande inventée par le modèle | `\step`→`\item` (autofix) ; `\justification`, `\cross`, `\cmark`, `\textipa` définies dans `preamble.tex` |
| `Undefined control sequence` `\emphà …}` | Accolade ouvrante oubliée devant une lettre accentuée | autofix (`\emph`, `\textbf`, `\textit`, `\cle`, `\esp` + non-ASCII → ajoute `{`) |
| `Undefined control sequence` `\í`, `\¿`, `\¡` | Antislash superflu devant une lettre accentuée | autofix ; **attention** : ne pas toucher `\\¡` (saut de ligne + ¡) |
| `Undefined control sequence` `\n` dans un nœud TikZ | `\n` littéral au lieu de `\\` | autofix limité aux lignes `\node` et à `\n` suivi d'une lettre (ne pas casser la variable `\n` de `\foreach`) |
| `Misplaced alignment tab character &` | `&` dans un titre de boîte ou du texte | autofix `_escape_ampersands` (hors tabular/array/align/tikz) |
| `You can't use macro parameter character #` | Hashtag `#Mot` | autofix : `#` → `\#` |
| `Missing $ inserted` | `_` non échappés (`______` à compléter) | autofix : `_{3,}` → `\_` répété (max 12) |
| `Paragraph ended before \text@command was complete` | `\esp`/`\emph`/`\textbf` qui traverse une ligne vide, ou accolade manquante | `\esp` est « long » (préambule) ; sinon chercher l'accolade avec `bal.py` |
| `File ended while scanning use of \esp / \emph / \textbf` | Accolade fermante manquante | `bal.py`, ajouter `}` |
| `Extra }, or forgotten \endgroup` | Accolade en trop | `bal.py`, retirer `}` |
| `\begin{tcb@savebox} … ended by \end{document}` | Boîte (`exemplebox`, `exercice`, `propriete`…) jamais refermée | autofix `_close_boxes` (ferme avant la boîte/section suivante, supprime les `\end` orphelins) |
| `Lonely \item--perhaps a missing list environment` | Boîte commençant par `\item`, ou `\end{enumerate}` prématuré | autofix `_lonely_items` (enveloppe dans itemize/enumerate) ; sinon relire la structure à la main |
| `Command \itshape invalid in math mode` | `\esp{}` dans une formule | `\esp` gère le mode math (préambule) |
| `Paragraph ended before \end was complete` | `\end{exemplebox>` (faute de frappe) | autofix |
| `Package pgfkeys Error: The key '/tikz/step' requires a value` | Style TikZ nommé `step` | renommer le style (ex. `pasoS`) |
| `No shape named 'xxx' is known` | Faute de frappe dans un nom de nœud | corriger le nom |
| `Missing \endcsname inserted` à la fin | Variable `\n` transformée en `\\` | voir la ligne `\n` ci-dessus |
| `Missing number, treated as zero` en fin de fichier | Sortie dégénérée (répétitions) | supprimer le bloc et le régénérer |
| `File ended while scanning use of \tikz@collect@child@code` | Figure tronquée par la relecture | recopier le brouillon (`cp bloc.tex bloc.rev.tex`) |
| `'utf-8' codec can't decode byte` dans `subprocess` | **Script `tectonic` local** qui coupe la sortie au milieu d'un caractère | utiliser la version de `tools/tectonic-shim.sh` (passe par `iconv -c`) |

## Défauts de contenu (compilent mais sont mauvais)

- **Relecture qui corrompt** : bloc doublé/tronqué, raisonnement du modèle (« Step 4: … The exercise
  statement is cut off in the prompt ») collé dans le LaTeX. → repartir du brouillon du rédacteur.
- **Sortie dégénérée** : `!!!!!!`, `\_\_\_\_…` sur des dizaines de milliers de caractères. → le garde-fou
  de `write_block` relance jusqu'à 3 fois ; si un bloc validé est tout petit, le supprimer et régénérer.
- **API/IPA** : la police n'a pas les glyphes de l'alphabet phonétique ; la consigne système demande de
  transcrire la prononciation en lettres françaises. `\textipa{}` existe en passthrough.
- Les PDF compilés avec le shim **une passe** affichent « ?? » comme nombre total de pages :
  recompiler avec `TWO_PASS=1` (ou avec tectonic).

## Test de non-régression d'une nouvelle règle d'autofix

Après avoir modifié `autofix()` :

```bash
python3 pipeline/check.py E15/s02 E04/s04 E23/methodes E03/s02 E05/s03 E21/s01   # blocs variés déjà validés
```

Ils doivent tous rester « validé ». Les règles de regex sur `\n`, `\\`, `&`, `#`, `_` sont les plus
risquées (elles touchent aussi TikZ, tableaux et math).

## Corrections faites à la main (modèles de réponse)

1. **Accolade manquante/en trop** : `bal.py` → corriger la ligne → `check.py`.
2. **Figure TikZ mindmap** : racine `\node[root concept]{…}` suivie de `child[…]{ node{…} child{…} }`, **sans `;`
   entre les `child`**, un seul `;` à la fin ; `child` et non `\child`.
3. **Nœuds imbriqués** (`\node[title]` dans `\node{…}`) : remplacer le nœud interne par `\textbf{…}\\`.
4. **Boîte parasite** (un `\end{enumerate}` ou `\end{exemplebox}` qui coupe une liste) : supprimer la fermeture
   intempestive, ou restaurer la ligne perdue depuis le brouillon (`build/<id>/<bloc>.tex`).
5. **Relecture inutilisable** : `cp build/<id>/<bloc>.tex build/<id>/<bloc>.rev.tex` puis `check.py`.
