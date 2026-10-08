#!/usr/bin/env python3
"""Pipeline de génération des cours signés OnBuch+ — classes SH (Première/Terminale), sous-système francophone.

Une matière à la fois : SUBJECT=<slug> (fichier pipeline/subjects/<slug>.json). Unité de génération = le chapitre.
(Texte d'origine ci-dessous, hérité du pipeline de Physique.)

Beaucoup de leçons ont un contenu identique entre plusieurs séries : chaque
leçon n'est donc rédigée QU'UNE SEULE FOIS (champ "series" de lessons.json)
et réutilisée telle quelle pour toutes les séries listées — build.py se
charge de dupliquer uniquement la mise en page (page de garde, en-tête).
Quand une série ajoute un peu de contenu au socle commun, ce supplément est
une section "extra" générée à part (rapide) et insérée uniquement dans les
séries concernées, sans regénérer toute la leçon (champ "extra").

Étapes par leçon (chaque étape est mise en cache dans build/<id>/, un nouvel
appel reprend là où il s'était arrêté) :

  1. plan.json        — plan détaillé calé sur le programme officiel
  2. sNN.tex          — rédaction de chaque section (modèle « rédacteur »)
  3. sNN.rev.tex      — relecture scientifique + pédagogique (modèle « relecteur »)
  4. compilation      — chaque bloc est compilé seul ; en cas d'erreur, le log
                        est renvoyé au modèle qui corrige (plusieurs essais)
  5. parties annexes  — activité d'intégration, méthodes & exercices résolus,
                        exercices, corrigés, fiche bilan
  6. extra_<id>       — sections complémentaires propres à certaines séries

Le contenu final est assemblé par build.py.

Clés : variable d'environnement NVIDIA_API_KEYS (séparées par des virgules).
Ne jamais les committer.
"""
import concurrent.futures as cf
import itertools
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PREAMBLE = ROOT / "preamble.tex"
FONTS = ROOT / "fonts"
TECTONIC = os.environ.get("TECTONIC", "tectonic")
API = "https://integrate.api.nvidia.com/v1/chat/completions"

KEYS = [k.strip() for k in os.environ.get("NVIDIA_API_KEYS", "").split(",") if k.strip()]
WRITER_MODELS = os.environ.get("WRITER_MODELS", "moonshotai/kimi-k3,nvidia/nemotron-3-ultra-550b-a55b").split(",")
REVIEW_MODELS = os.environ.get("REVIEW_MODELS", "nvidia/nemotron-3-ultra-550b-a55b,moonshotai/kimi-k3").split(",")
_key_cycle = itertools.cycle(range(max(1, len(KEYS))))
_lock = threading.Lock()
# Une file par modèle : les longues rédactions Kimi ne bloquent plus les
# relectures (et inversement).
_slots_per_model = {}


def _slots(model):
    with _lock:
        if model not in _slots_per_model:
            n = int(os.environ.get("MAX_CONCURRENT", "12"))
            _slots_per_model[model] = threading.BoundedSemaphore(n)
        return _slots_per_model[model]


def _busy(model):
    sem = _slots(model)
    return sem._value == 0  # noqa: SLF001


def log(*a):
    with _lock:
        print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
def _call(model, key, messages, max_tokens, temperature):
    """Appel en streaming (évite les coupures sur les longues générations)."""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens,
            "temperature": temperature, "top_p": 0.95, "stream": True}
    r = requests.post(API, json=body, stream=True, timeout=(30, 600),
                      headers={"Authorization": f"Bearer {key}", "Accept": "text/event-stream"})
    if r.status_code == 429:
        raise RateLimited()
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    r.encoding = "utf-8"  # le flux SSE n'annonce pas de charset (sinon décodé en latin-1)
    out, finish = [], None
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            ch = json.loads(data)["choices"][0]
        except (KeyError, IndexError, json.JSONDecodeError):
            continue
        delta = ch.get("delta") or {}
        if delta.get("content"):
            out.append(delta["content"])
        finish = ch.get("finish_reason") or finish
    text = "".join(out)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if not text:
        raise RuntimeError("réponse vide")
    return text, finish


class RateLimited(Exception):
    pass


def llm(messages, models, max_tokens=16000, temperature=0.6, tries=6):
    last = None
    attempt, waits = 0, 0
    while attempt < tries:
        # Modèle principal d'abord ; les modèles de repli prennent le relais
        # après des erreurs répétées ou lorsque le quota (429) sature.
        idx = attempt if attempt >= 2 else 0
        idx += waits // 3
        model = models[idx % len(models)]
        # Si le modèle principal est saturé, on bascule sur le suivant plutôt
        # que d'attendre (les deux modèles rédigent et relisent bien).
        if attempt == 0 and _busy(model) and len(models) > 1 and not _busy(models[(idx + 1) % len(models)]):
            model = models[(idx + 1) % len(models)]
        with _lock:
            key = KEYS[next(_key_cycle)]
        try:
            t0 = time.time()
            with _slots(model):
                text, finish = _call(model, key, messages, max_tokens, temperature)
            log(f"    ↳ {model} {len(text)} car. en {time.time()-t0:.0f}s ({finish})")
            if finish == "length":
                # Sortie tronquée : on demande la suite une fois.
                cont = messages + [{"role": "assistant", "content": text},
                                   {"role": "user", "content": "Continue EXACTEMENT là où tu t'es arrêté, sans rien répéter, sans commentaire."}]
                with _slots(model):
                    more, _ = _call(model, key, cont, max_tokens, temperature)
                text += more
            return text
        except RateLimited:
            # Quota momentanément dépassé : on attend sans consommer d'essai.
            waits += 1
            if waits % 10 == 1:
                log(f"    … 429 sur {model} (attente n°{waits})")
            if waits > 300:
                raise RuntimeError("429 persistants")
            time.sleep(random.uniform(10, 30))
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"    ! {model}: {e} — nouvel essai")
            time.sleep(min(60, 5 * 2 ** attempt))
            attempt += 1
    raise RuntimeError(f"échec LLM : {last}")


# ---------------------------------------------------------------------------
# Contrat LaTeX transmis aux modèles
# ---------------------------------------------------------------------------
SUBJECT = os.environ.get("SUBJECT", "").strip()
SUBJ_FILE = ROOT / "pipeline" / "subjects" / f"{SUBJECT}.json"
BUILD = ROOT / "build" / SUBJECT if SUBJECT else ROOT / "build"


def load_catalog():
    if not SUBJECT or not SUBJ_FILE.exists():
        sys.exit("Définir SUBJECT=<slug> (fichier pipeline/subjects/<slug>.json)")
    return json.loads(SUBJ_FILE.read_text())


# Profil par matière : (libellé, langue, rôle, consignes spécifiques)
PROFILES = {
    "francais": ("Français", "français", "un professeur de lettres agrégé, auteur de manuels de référence et membre de jury d'examen",
                 "Traite la langue (grammaire, conjugaison, lexique, orthographe), la lecture méthodique, les méthodes écrites (dissertation, commentaire composé, contraction de texte, résumé) avec des modèles complets rédigés par toi, des plans détaillés et des exercices corrigés. Cite uniquement des extraits d'œuvres ou de textes dont tu es certain (sinon invente un texte original et dis-le) ; ne fabrique jamais de fausse citation d'auteur."),
    "histoire": ("Histoire", "français", "un professeur d'histoire agrégé et formateur d'enseignants",
                 "Présente causes, déroulement, conséquences, acteurs et dates exactes ; cartes et frises chronologiques dessinées en TikZ ; analyse de documents et méthode du commentaire ; liens avec le Cameroun et l'Afrique. N'invente ni citations ni chiffres précis : préfère des ordres de grandeur et des faits solidement établis."),
    "geographie": ("Géographie", "français", "un professeur de géographie agrégé et formateur d'enseignants",
                   "Utilise une terminologie précise, des études de cas (Cameroun, Afrique, monde), des schémas, croquis et cartes simples dessinés en TikZ, des tableaux et graphiques (pgfplots). N'invente jamais de statistiques précises : donne des ordres de grandeur et indique « environ »."),
    "ecm": ("Éducation à la citoyenneté et à la morale (ECM)", "français", "un professeur d'ECM et formateur d'enseignants",
            "Appuie-toi sur les textes officiels camerounais et internationaux que tu connais avec certitude (Constitution, Déclaration universelle des droits de l'homme), des situations de vie concrètes, des études de cas et des activités de réflexion ; ne cite pas d'article ou de date dont tu n'es pas sûr."),
    "informatique-tic": ("Informatique (TIC)", "français", "un professeur d'informatique et formateur d'enseignants",
                         "Sois très pratique : concepts, outils (système d'exploitation, bureautique, tableur, présentation, réseaux, sécurité, algorithmique de base), manipulations pas à pas, captures d'écran remplacées par des schémas TikZ ou des tableaux ; exemples camerounais (mobile money, ANTIC, e-administration). Pas d'environnement verbatim/lstlisting : écris le code ou les formules avec \\texttt dans des listes."),
    "philosophie": ("Philosophie", "français", "un professeur de philosophie agrégé et formateur d'enseignants",
                    "Présente fidèlement et de façon critique les arguments des auteurs ; ne cite que de courtes citations dont tu es certain (sinon paraphrase) ; objections et réponses, exemples africains et camerounais, méthodologie de la dissertation et du commentaire de texte avec plans modèles. Aucune citation inventée."),
    "sciences": ("Sciences (SVT)", "français", "un professeur de SVT agrégé et formateur d'enseignants",
                 "Explique les processus pas à pas avec une terminologie exacte ; schémas annotés en TikZ (cellules, organes, cycles, arbres généalogiques), graphiques et tableaux de résultats ; exercices types examen (analyse de documents, schémas à annoter). Relie au contexte camerounais (santé, nutrition, paludisme, drépanocytose, agriculture) quand c'est naturel."),
    "mathematiques": ("Mathématiques", "français", "un professeur de mathématiques agrégé et formateur d'enseignants",
                      "Démontre ou établis rigoureusement les résultats, avec des exemples entièrement résolus ; courbes en pgfplots, figures géométriques en TikZ."),
}
PROFILE = PROFILES.get(SUBJECT.rsplit("-", 1)[0] if SUBJECT.rsplit("-", 1)[-1] in ("1sh", "tsh") else SUBJECT,
                       (SUBJECT, "français", "un professeur agrégé", ""))
LANG = PROFILE[1]
MATIERE_LABEL = PROFILE[0]
NIVEAU = "Première SH" if SUBJECT.endswith("-1sh") else "Terminale SH" if SUBJECT.endswith("-tsh") else "SH"

SYSTEM = r"""Tu es """ + PROFILE[2] + r""", auteur de manuels de référence pour les lycées du Cameroun (sous-système francophone, enseignement général, série SH), et concepteur de cours numériques PREMIUM pour l'application OnBuch+. Tes cours sont réputés être les plus clairs, les plus complets et les plus rigoureux : chaque notion est introduite par une observation, une situation concrète ou un exemple, puis expliquée et justifiée proprement, illustrée par des exemples résolus et des figures, et enfin consolidée par des exercices et des mises en garde sur les erreurs fréquentes. Tu respectes strictement le programme officiel MINESEC pour la classe de """ + NIVEAU + r""" (approche par les compétences) et le chapitre précisé dans la consigne. Tu écris en français impeccable, rigoureux mais accessible, au niveau d'un élève de """ + NIVEAU + r""", avec un ton chaleureux et motivant. Tu utilises des exemples du contexte camerounais quand c'est pertinent et naturel, sans jamais sacrifier la rigueur. Tu n'inventes jamais de statistiques précises, de dates, de lois ou de citations dont tu n'es pas sûr.
CONSIGNES PROPRES À LA MATIÈRE : """ + PROFILE[3] + r"""

Tu produis UNIQUEMENT du code LaTeX (corps de document, compilé avec XeLaTeX), sans aucune explication autour, sans balises Markdown ``` .

CONTRAT LaTeX (obligatoire) :
- Interdit : \documentclass, \usepackage, \begin{document}, \end{document}, \section, \subsection, \chapter, \includegraphics, \begin{figure}, \begin{table}, \input, \newcommand, \def, \label/\ref, Markdown (**gras**, # titres), \verb, verbatim/lstlisting, emojis, caractères d'un alphabet non latin.
- Titres : \coursec{Titre de section} (niveau 1, numéroté automatiquement) et \courssub{Sous-titre} (niveau 2, numéroté automatiquement). Niveau 3 : \textbf{...}\par. Ne numérote JAMAIS les titres toi-même.
- Boîtes pédagogiques (titre optionnel entre crochets) :
  \begin{definition}[Titre]...\end{definition}
  \begin{propriete}[Titre]...\end{propriete}  (propriété, règle, loi, formule)
  \begin{aretenir}[Titre]...\end{aretenir}
  \begin{exemplebox}[Titre]...\end{exemplebox}
  \begin{methode}[Titre]...\end{methode}  (démarche en étapes numérotées)
  \begin{attention}[Titre]...\end{attention}  (erreurs fréquentes, pièges)
  \begin{experience}[Titre]...\end{experience}  (expérience, travaux pratiques, étude de cas, démonstration guidée)
  \begin{savaistu}[Titre]...\end{savaistu}  (histoire, applications, Cameroun)
  \begin{exoresolu}[Titre]Énoncé ... \tcblower Solution détaillée ...\end{exoresolu}
  \begin{exercice}[Titre]...\end{exercice}
  \begin{corrige}[Exercice n]...\end{corrige}
  Ne jamais imbriquer une boîte dans une autre boîte.
- Mot-clé mis en valeur : \cle{mot}. Gras : \textbf{}. Italique : \emph{}.
- Mathématiques : $...$ en ligne, \[ ... \] pour les formules centrées, \begin{align*} ... \end{align*} pour les calculs alignés. Décimaux avec virgule dans le texte et les formules (3,14).
- Tableaux : \begin{center}\begin{tabularx}{\linewidth}{|l|X|X|}\hline ... \end{tabularx}\end{center} ou tabular + booktabs. Chaque ligne a EXACTEMENT autant de cellules que de colonnes. Pas de \hline hors d'un tabular. En-têtes colorés : \rowcolor{popblueL}.
- Illustrations : place chaque figure dans \begin{popfigure} ... \legende{Légende}\end{popfigure}. Utilise TikZ (schémas, cartes simples, frises, cartes mentales, diagrammes) et pgfplots (courbes, histogrammes). Couleurs autorisées : popink, poporange, popdark, poppurple, popgreen, popblue, poppink, popgold, popmuted, popline, popblueL, popgreenL, et black/white/gray.
- DANS TOUT CODE TikZ / pgfplots, les décimaux s'écrivent avec un POINT : (7.389,2), 0.55cm. Texte des nœuds COURT ; jamais de \n ni de saut de ligne littéral dans un nœud (utilise \\ avec align=center) ; échappe & en \& dans le texte d'un nœud.
- Listes : itemize / enumerate classiques ; chaque entrée commence par \item (jamais de lignes nues « a) ... »).
- Le symbole % doit être échappé \% ; les caractères & _ # doivent être échappés hors des environnements qui les attendent (donc PAS dans $...$, \[...\], tabular).
"""


def strip_fences(t):
    t = re.sub(r"^```[a-zA-Z]*\s*\n", "", t.strip())
    t = re.sub(r"\n```\s*$", "", t)
    t = t.replace("```latex", "").replace("```", "")
    return t.strip()


def sanitize(t):
    # Réponse du type « Voici ... ```latex ... ``` » : on garde le plus long bloc de code.
    blocks = re.findall(r"```[a-zA-Z]*\s*\n(.*?)```", t, flags=re.S)
    if blocks:
        t = max(blocks, key=len)
    m = re.search(r"\\begin\{document\}(.*?)(\\end\{document\}|$)", t, flags=re.S)
    if m:
        t = m.group(1)
    t = strip_fences(t)
    for pat in [r"\\documentclass.*?\n", r"\\usepackage.*?\n", r"\\begin\{document\}", r"\\end\{document\}"]:
        t = re.sub(pat, "", t)
    t = re.sub(r"\\(sub)*section\*?\{", r"\\courssub{", t)
    return t.strip()


# ---------------------------------------------------------------------------
# Compilation d'essai
# ---------------------------------------------------------------------------
HEADER = r"""\newcommand{\DOCMATIERE}{Test}\newcommand{\DOCNIVEAU}{SH}\newcommand{\DOCMODULE}{Test}\newcommand{\DOCLECON}{00}\newcommand{\DOCTITRE}{Test}
\input{preamble.tex}
\begin{document}
"""


_BR = r"(?:\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})"
_PROT = re.compile("|".join([
    r"\\begin\{(tikzpicture|axis|align\*?|equation\*?|gather\*?|multline\*?|pmatrix|bmatrix|cases|eqnarray\*?|displaymath|math)\}.*?\\end\{\1\}",
    r"\$\$.*?\$\$", r"\$[^$]*\$", r"\\\[.*?\\\]", r"\\\(.*?\\\)",
    r"\\ce" + _BR,
    r"\\(?:SI|si|num|qty|ang)" + _BR + "(?:" + _BR + ")?",
    r"\\(?:ensuremath|mathrm|mathbf|mathit|label|ref|legende|tikzmarknode)" + _BR,
    r"\\[_^%&#$]", r"%[^\n]*",
]), re.S)
_SUP = re.compile(r"\^(\{[^{}]*\}|[0-9]+[+\-]?|[+\-]|[A-Za-z](?![A-Za-z]))")
_GREEK = re.compile(r"\\(Delta|Lambda|Omega|Sigma|Pi|Phi|Psi|Gamma|Theta|alpha|beta|gamma|delta|epsilon|theta|lambda|mu|nu|pi|rho|sigma|tau|phi|omega|rightarrow|leftarrow|rightleftharpoons|approx|times|pm|leq|geq|neq|infty)(?![A-Za-z])((?:_\{[^{}]*\}|_[A-Za-z0-9]|\^\{[^{}]*\}|\^[A-Za-z0-9])?)")
_SUB = re.compile(r"(?<=[A-Za-z0-9)\]])_(\{[^{}]*\}|[0-9]+)")


def _text_scripts(seg):
    seg = _GREEK.sub(lambda m: "$\\" + m.group(1) + m.group(2) + "$", seg)
    seg = _SUP.sub(lambda m: "$^{" + m.group(1).strip("{}") + "}$", seg)
    return _SUB.sub(lambda m: "$_{" + m.group(1).strip("{}") + "}$", seg)


def text_scripts_fix(body):
    """^ et _ hors mode math (texte courant, cellules de tableau) -> exposants/indices en math."""
    out, pos = [], 0
    for m in _PROT.finditer(body):
        out.append(_text_scripts(body[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_text_scripts(body[pos:]))
    return "".join(out)


def _ce_math(inner):
    """Version « math romain » d'une formule chimique que mhchem refuse."""
    s = inner.replace("$", "")
    s = s.replace("<=>", r"\rightleftharpoons ").replace("->", r"\rightarrow ").replace("<-", r"\leftarrow ")
    s = re.sub(r"\^(\d*[+\-])", r"^{\1}", s)
    s = re.sub(r"(?<=[A-Za-z\)\]])(\d+)(?![^{]*\})", r"_{\1}", s)
    s = re.sub(r"(?<=[A-Za-z\)\]])([+\-])(?=[\s\)\]}(]|$)", r"^{\1}", s)
    s = s.replace("&", "}}&\\ensuremath{\\mathrm{")
    return "\\ensuremath{\\mathrm{" + s + "}}"


def ce_fix(body):
    """mhchem : caractères/constructions que \\ce{} refuse."""
    def one(m):
        s = m.group(0)
        for x, y in (("·", "."), ("→", "->"), ("⇌", "<=>"), ("⇄", "<=>"), ("−", "-"), ("–", "-"), ("’", "'"), ("°", "^\\circ ")):
            s = s.replace(x, y)
        s = re.sub(r"\s<\s", " $<$ ", s)
        s = re.sub(r"\s>\s", " $>$ ", s)
        inner = s[4:-1]
        if re.search(r"\\|\$|<-|\bF\d?B|'", inner):
            return _ce_math(inner)
        arrow = "->" in inner or "<=>" in inner or "<-" in inner
        if arrow:
            return re.sub(r"(?<=[A-Z\)])'", lambda _k: "$^\\prime$", s)
        if ("_{" in inner or "'" in inner) and "\\" not in inner and "$" not in inner:
            return "\\ensuremath{\\mathrm{" + inner + "}}"
        return s
    return re.sub(r"\\ce" + _BR, one, body)


def repair_damage(body):
    """Répare les dégâts d'anciennes règles : $^{..}$ à l'intérieur d'un contexte déjà math."""
    body = re.sub(r"\\\[\s*\\\[(\\boxed\{[^\n]*\})\\\]\s*\\\]", lambda m: "\\[" + m.group(1) + "\\]", body)
    body = re.sub(r"\^\\prime(?=[a-z_^])", "'", body)
    body = re.sub(r"\$\^\\prime\$(?=[a-z])", "'", body)
    def strip(m):
        return re.sub(r"\$([\^_]\{[^{}]*\})\$", r"\1", m.group(0))
    return re.sub(r"\\(?:ensuremath|mathrm|SI|si)" + _BR + "(?:" + _BR + ")?", strip, body)


def us_fix(body):
    """Règles propres au pipeline SH (erreurs fréquentes observées)."""
    body = repair_damage(body)
    body = ce_fix(body)
    body = text_scripts_fix(body)
    def _fmt(m):  # ^ / _ dans \textbf/\emph/\textit (y compris dans les nœuds TikZ)
        inner = m.group(2)[1:-1]
        if "$" in inner or "\\" in inner:
            return m.group(0)
        inner = re.sub(r"\^(\{[^{}]*\}|\w)", lambda k: "$^{" + k.group(1).strip("{}") + "}$", inner)
        inner = re.sub(r"(?<!\\)_(\{[^{}]*\}|\w)", lambda k: "$_{" + k.group(1).strip("{}") + "}$", inner)
        return m.group(1) + "{" + inner + "}"
    body = re.sub(r"(\\(?:emph|textbf|textit))(\{(?:[^{}]|\{[^{}]*\})*\})", lambda m: _fmt(m) if re.search(r"[\^_]", m.group(2)) else m.group(0), body)
    # ^ / _ dans les étiquettes de pgfplots (texte, pas math)
    body = re.sub(r"\b((?:x|y|z)?label|title)\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}",
                  lambda m: m.group(1) + "={" + (m.group(2) if "$" in m.group(2) else _text_scripts(m.group(2))) + "}", body)
    # \boxed{..} seul sur sa ligne, hors math -> formule centrée (sans doubler un \[ existant)
    def _boxed(body):
        L, out = body.split("\n"), []
        for i, l in enumerate(L):
            m = re.match(r"^[ \t]*(\\boxed\{.*\})[ \t]*$", l)
            prev = next((x.strip() for x in reversed(out) if x.strip()), "")
            if m and prev not in ("\\[", "$$") and not prev.endswith("\\begin{align*}"):
                l = "\\[" + m.group(1).replace("$", "") + "\\]"
            out.append(l)
        return "\n".join(out)
    body = _boxed(body)
    body = re.sub(r"\\clip\[[^\]\n]*\]", lambda _m: "\\clip", body)   # \clip n'accepte pas d'options
    body = re.sub(r"\\label\b(?!\s*\{)", lambda _m: "\\lbl", body)           # \label utilisé comme variable
    body = re.sub(r"(?<![\w}])\\degree(?![A-Za-z])", lambda _m: "\\ensuremath{{}^{\\circ}}", body)
    body = body.replace("\\then ", "then ").replace("\\celsius", "\\ensuremath{{}^{\\circ}\\mathrm{C}}")
    body = re.sub(r"\\(begin|end)\{(examtip|examtips|tip|keypoint|keypoints|note|remark|remarque|example|worked|summary|info)\}",
                  lambda m: "\\" + m.group(1) + ("{aretenir}" if m.group(2) in ("examtip", "examtips", "tip", "keypoint", "keypoints", "summary") else "{exemplebox}" if m.group(2) in ("example", "worked") else "{attention}"), body)
    body = re.sub(r"\\\]\^(\{[^{}]*\}|\d+[+\-]?)", lambda m: "]$^{" + m.group(1).strip("{}") + "}$", body)
    body = re.sub(r"^[ \t]*\\courssubtitle\{[^\n]*\}[ \t]*\n", "", body, flags=re.M)
    body = body.replace("\\courssubtitle", "\\courssub")
    body = re.sub(r"(Stealth|Latex|To|Triangle)\[([^\]\}\n]*)\}\]", r"\1[\2]}", body)
    body = re.sub(r"(pop[A-Za-z]+)\s+/", r"\1/", body)
    body = re.sub(r"/\s+(pop[A-Za-z]+)", r"/\1", body)
    body = re.sub(r"\{(pop[A-Za-z]+)\s+\}", r"{\1}", body)

    def _wrap(m):
        env, head, inner = m.group(1), m.group(2) or "", m.group(3)
        lst = "enumerate" if env == "methode" else "itemize"
        return f"\\begin{{{env}}}{head}\n\\begin{{{lst}}}\n{inner.rstrip()}\n\\end{{{lst}}}\n\\end{{{env}}}"
    body = re.sub(r"\\begin\{(methode|aretenir|attention|propriete|definition|exemplebox|experience|savaistu)\}(\[[^\n]*\])?[ \t]*\n(\s*\\item\b.*?)\\end\{\1\}",
                  _wrap, body, flags=re.S)
    return body


def autofix(body):
    body = us_fix(body)
    return _autofix(body)


def _autofix(body):
    """Corrections mécaniques sûres, appliquées avant compilation (sans modèle).
    - virgule décimale dans une dimension TikZ : 0,55cm -> 0.55cm, aspect=2,6 -> 2.6
    - coordonnée calculée non protégée dans un \\foreach : (\\x,\\y) déjà sûr, rien à faire
    """
    body = re.sub(r"((?:=|\band)\s*-?)(\d+),(\d+)\s*(cm|mm|pt|em|ex)\b", r"\1\2.\3\4", body)
    body = re.sub(r"\b(aspect|scale|xscale|yscale|opacity|line width|inner sep|outer sep|minimum size|minimum width|minimum height|text width|samples|domain|xmin|xmax|ymin|ymax)\s*=\s*(-?\d+),(\d+)",
                  r"\1=\2.\3", body)
    # domain=-1,45:1,45 -> domain=-1.45:1.45
    body = re.sub(r"domain\s*=\s*(-?\d+(?:[.,]\d+)?)\s*:\s*(-?\d+(?:[.,]\d+)?)",
                  lambda m: f"domain={m.group(1).replace(',', '.')}:{m.group(2).replace(',', '.')}", body)
    # 0,9\linewidth -> 0.9\linewidth
    body = re.sub(r"(?<![\w.])(\d+),(\d+)\s*\\(linewidth|textwidth)", r"\1.\2\\\3", body)
    # at={(0,02,0,98)} (2D écrit à la française : 3 virgules) -> (0.02,0.98)
    body = re.sub(r"\((-?\d+),(\d+),(-?\d+),(\d+)\)", r"(\1.\2,\3.\4)", body)
    # Titre de boîte contenant [ ou ] (intervalles ]a;b[) : on le protège par
    # des accolades, sinon l'argument optionnel [..] est coupé.
    def _protect(m):
        title = m.group(2)
        if ("[" in title or "]" in title) and not (title.startswith("{") and title.endswith("}")):
            return f"\\begin{{{m.group(1)}}}[{{{title}}}]"
        return m.group(0)
    body = re.sub(r"\\begin\{(definition|propriete|aretenir|exemplebox|methode|attention|experience|savaistu|exoresolu|exercice|corrige)\}\[(.*)\][ \t]*$",
                  _protect, body, flags=re.M)
    # Titres numérotés à la main (\\courssub{2.3 Titre}) : la numérotation est
    # automatique, on retire le numéro tapé par le modèle.
    body = re.sub(r"\\(coursec|courssub)\{\s*\d+(?:\.\d+)*\s*[.)\-–:]?\s+", r"\\\1{", body)
    body = _tabularx_sans_x(body)
    # \tcblower est un séparateur, pas un environnement
    body = body.replace("\\begin{tcblower}", "\\tcblower").replace("\\end{tcblower}", "")
    # \node[...]{texte avec \\} sans align= : TikZ refuse le saut de ligne
    body = _node_align(body)
    body = _cases_math(body)
    # \textbf{mot** (fermeture Markdown) -> \textbf{mot}
    body = re.sub(r"\\textbf\{([^{}*\n]*)\*\*", r"\\textbf{\1}", body)
    # Markdown oublié : **gras** -> \textbf{gras}
    body = re.sub(r"\*\*([^*\n$]+?)\*\*", r"\\textbf{\1}", body)
    # mhchem : étiquette de flèche en commande math (->[\alpha]) et \dots -> mode math
    def _ce(m):
        t = re.sub(r"(->|<=>|<-)\[(\\[A-Za-z]+[^\]$]*)\]", r"\1[$\2$]", m.group(0))
        return t.replace("\\dots", "$\\cdots$").replace("\\ldots", "$\\cdots$")
    body = re.sub(r"\\ce\{(?:[^{}]|\{[^{}]*\})*\}", _ce, body)
    # exercice non fermé avant son corrigé -> \end{exercice} inséré
    body = re.sub(r"(\\begin\{exercice\}(?:(?!\\end\{exercice\}|\\begin\{exercice\}).)*?)(\n\s*\\begin\{corrige\})",
                  r"\1\n\\end{exercice}\n\2", body, flags=re.S)
    # pgf calcule sin/cos en degrés : un argument contenant « pi » est en radians -> deg(...)
    _trig = re.compile(r"\b(sin|cos|tan)\(((?:[^()]|\((?:[^()]|\([^()]*\))*\))*?\bpi\b(?:[^()]|\((?:[^()]|\([^()]*\))*\))*?)\)")
    body = "\n".join(_trig.sub(lambda m: f"{m.group(1)}(deg({m.group(2)}))", l)
                     if (("addplot" in l or "plot" in l) and "deg(" not in l) else l
                     for l in body.split("\n"))
    # \textbf{3x^2+...} dans une formule : \textbf n'accepte pas ^ et _ -> \boldsymbol
    body = re.sub(r"(?<!\\)\$([^$]+)\$",
                  lambda m: "$" + re.sub(r"\\textbf\{([^{}]*[\^_][^{}]*)\}", r"\\boldsymbol{\1}", m.group(1)) + "$", body)
    # calc TikZ : ($(2)*(U)$) -> ($2*(U)$) (un nombre entre parenthèses est pris pour un nœud)
    body = re.sub(r"\(\$\s*\((-?[\d.]+)\)\s*\*", r"($\1*", body)
    body = body.replace("\\not\\implies", "\\nRightarrow").replace("\\not\\iff", "\\nLeftrightarrow").replace("\\not\\Longrightarrow", "\\nRightarrow")
    # popfigure : \begin{center} non refermé -> \end{center} avant la légende
    def _fig(m):
        t = m.group(0)
        k = t.count("\\begin{center}") - t.count("\\end{center}")
        if k > 0:
            i = t.rfind("\\legende")
            i = i if i >= 0 else t.rfind("\\end{popfigure}")
            t = t[:i] + "\\end{center}\n" * k + t[i:]
        return t
    body = re.sub(r"\\begin\{popfigure\}.*?\\end\{popfigure\}", _fig, body, flags=re.S)
    # TikZ : font=\small\textbf -> \bfseries (\textbf attend un argument)
    body = re.sub(r"(font\s*=\s*\{?[^,\]}]*?)\\textbf\b", r"\1\\bfseries", body)
    # pgfplots : coordinates (a,b) (c,d); sans accolades -> boucle infinie
    body = re.sub(r"\bcoordinates\s*((?:\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)\s*)+);", lambda m: "coordinates {" + m.group(1).strip() + "};", body)
    # environnement inventé "erreur" -> attention (boîte prévue par le contrat)
    body = body.replace("\\begin{erreur}", "\\begin{attention}").replace("\\end{erreur}", "\\end{attention}")
    body = body.replace("\\begin{astuce}", "\\begin{methode}").replace("\\end{astuce}", "\\end{methode}")
    # tikzpicture[scale=petit] + plot domain=... : dépassement de dimension TeX
    # (la coordonnée brute dépasse ~576cm avant application de scale) -> x=/y=
    def _scale_to_xy(m):
        pic = m.group(0)
        sm = re.search(r"scale\s*=\s*(0?\.\d+)", pic)
        if sm and float(sm.group(1)) < 0.3 and "domain=" in pic and re.search(r"\bplot\b", pic):
            val = sm.group(1)
            pic = pic[:sm.start()] + f"x={val}cm, y={val}cm" + pic[sm.end():]
        return pic
    body = re.sub(r"\\begin\{tikzpicture\}\[[^\]]*\].*?\\end\{tikzpicture\}", _scale_to_xy, body, flags=re.S)
    body = _box_as_command(body)
    body = _lonely_items(body)
    # circuitikz : étiquette l=$...$ non protégée (virgule, parenthèses) -> l={$...$}
    body = re.sub(r"(to\[[^\]]*?\b(?:l|l_|l\^|v|v_|v\^|i|i_|i\^|a|a_|a\^)=)\$([^$]*)\$",
                  lambda m: m.group(1) + "{$" + m.group(2) + "$}", body)
    # enumerate[a)] (syntaxe enumerate.sty) -> enumitem : label=\alph*)
    _lab = {"a": r"\alph*", "A": r"\Alph*", "i": r"\roman*", "I": r"\Roman*", "1": r"\arabic*"}
    body = re.sub(r"\\begin\{enumerate\}\[(\(?)([aAiI1])([.)\]]?)\]",
                  lambda m: "\\begin{enumerate}[label=" + m.group(1) + _lab[m.group(2)] + m.group(3) + "]", body)
    # circuitikz : Tnpn/Tpnp n'ont pas les ancres .B/.C/.E -> npn/pnp
    body = re.sub(r"\bT(npn|pnp)\b", r"\1", body)
    # la boîte bilan n'a pas de titre optionnel : [..] s'imprimerait tel quel
    body = re.sub(r"\\begin\{bilan\}\[[^\]\n]*\]", r"\\begin{bilan}", body)
    # Fautes de frappe sur \begin : \begin{savaistu][Titre] ou \begin{tabularx{\linewidth}
    body = re.sub(r"\\begin\{([a-zA-Z*]+)\]\[", r"\\begin{\1}[", body)
    body = re.sub(r"\\begin\{(tabularx|tabular|array|minipage)\{", r"\\begin{\1}{", body)
    # calc TikZ : le coefficient précède le point, ($(U)*0.55$) -> ($0.55*(U)$)
    body = re.sub(r"\(\$\s*\(([^()$]+)\)\s*\*\s*(-?[\d.]+)\s*\$\)", r"($\2*(\1)$)", body)
    body = _widen_tabular(body)
    # label=above right:$L(1,2,5)$ : les virgules de l'étiquette coupent les options
    body = re.sub(r"(?<![{\w])label=([a-z ]+:\$[^$]*,[^$]*\$)", r"label={\1}", body)
    # Indice/exposant fait d'une commande à argument : x_\mathcal{P} -> x_{\mathcal{P}}
    body = re.sub(r"([_^])\\(math[a-z]+|text|operatorname)\{([^{}]*)\}", r"\1{\\\2{\3}}", body)
    body = _align_close(body)
    body = _fill_const(body)
    # \textbf{C.V}^{-1} dans un \text{} : exposant hors mode math -> \textsuperscript{$-1$}
    body = re.sub(r"(\\textbf\{[^{}$]*\})\^\{([^{}$]*)\}", r"\1\\textsuperscript{$\2$}", body)
    # \SI{6,67e-11} sans unité (1 seul argument) : \SI avale l'argument suivant -> \num
    body = re.sub(r"\\SI\{([^{}]*)\}(?!\{)", r"\\num{\1}", body)
    body = _safe_sqrt(body)
    body = _close_lists(body)
    # Libellés de graduations : $0,05$ dans une liste {…} coupe à la virgule -> $0{,}05$
    # (virgule entre deux chiffres DANS un $...$ de la liste, même suivie de \\,E ou \\tau)
    body = re.sub(r"((?:x|y)ticklabels\s*=\s*\{)((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)(\})",
                  lambda m: m.group(1) + re.sub(r"\$[^$]*\$", lambda k: re.sub(r"(\d),(\d)", r"\1{,}\2", k.group(0)), m.group(2)) + m.group(3), body)
    return body


def _widen_tabular(body):
    """Tableau simple (spec faite de c/l/r et |) dont les lignes ont PLUS de
    cases que de colonnes déclarées (cas typique des tableaux de variations) :
    on ajoute les colonnes manquantes en fin de spec."""
    def cells(row):
        depth, n = 0, 1
        for ch in row:
            depth += (ch == "{") - (ch == "}")
            n += (ch == "&" and depth == 0)
        return n

    def fix(m):
        spec, content = m.group(1), m.group(2)
        k = len(re.findall(r"[lcr]", spec))
        rows = [r for r in re.split(r"\\\\", content) if "&" in r]
        if not rows or "\\multicolumn" in content:
            return m.group(0)
        need = max(cells(r) for r in rows)
        if need <= k:
            return m.group(0)
        spec2 = spec.rstrip("|") + "c" * (need - k) + ("|" if spec.endswith("|") else "")
        return "\\begin{tabular}{" + spec2 + "}" + content + "\\end{tabular}"
    return re.sub(r"\\begin\{tabular\}\{([|lcr ]+)\}(.*?)\\end\{tabular\}", fix, body, flags=re.S)


def _fill_const(body):
    """fill between[of=F and {0}] : pgfplots exige deux chemins nommés. Chaque
    constante {k} devient une droite horizontale invisible y = k, nommée et
    tracée sur le domaine du soft clip."""
    counter = [0]

    def fix(m):
        indent, opts, a, b, rest = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        dom = re.search(r"domain\s*=\s*([-\d.]+\s*:\s*[-\d.]+)", rest)
        dom = f", domain={dom.group(1)}" if dom else ""
        pre, names = [], []
        for side in (a, b):
            c = re.fullmatch(r"\{\s*(-?[\d.]+)\s*\}", side.strip())
            if c:
                counter[0] += 1
                name = f"hconst{counter[0]}"
                pre.append(f"{indent}\\addplot[draw=none, forget plot, name path={name}{dom}] {{{c.group(1)}}};\n")
                names.append(name)
            else:
                names.append(side.strip())
        return "".join(pre) + f"{indent}\\addplot[{opts}] fill between[of={names[0]} and {names[1]}{rest}"
    return re.sub(r"(?m)^([ \t]*)\\addplot\[([^\]]*)\]\s*fill between\[of=\s*(\{[^{}]*\}|[\w-]+)\s+and\s+(\{[^{}]*\}|[\w-]+)(.*)$", fix, body)


def _align_close(body):
    """\\begin{align*} refermé par \\] au lieu de \\end{align*} (erreur fréquente)."""
    def fix(m):
        inner = m.group(2)
        if "\\end{" + m.group(1) + "}" in inner or "\\[" in inner:
            return m.group(0)
        return "\\begin{" + m.group(1) + "}" + inner + "\\end{" + m.group(1) + "}"
    return re.sub(r"\\begin\{(align\*?)\}(.*?)\\\]", fix, body, flags=re.S)


def _safe_sqrt(body):
    """Dans les tracés TikZ/pgfplots, sqrt(x) avec x négatif par arrondi aux
    bornes (ellipses, hyperboles) fait échouer la compilation : on protège
    par sqrt(max(0, x)), sans effet là où x est positif. Hors tracés (texte,
    formules LaTeX), rien n'est modifié."""
    lines = body.split("\n")
    for i, l in enumerate(lines):
        if "sqrt(" not in l or not re.search(r"plot|\\addplot|domain|declare function|\\draw", l):
            continue
        out, k = [], 0
        while True:
            j = l.find("sqrt(", k)
            if j < 0:
                out.append(l[k:])
                break
            start = j + len("sqrt(")
            depth, e = 1, start
            while e < len(l) and depth:
                depth += (l[e] == "(") - (l[e] == ")")
                e += 1
            inner = l[start:e - 1]
            out.append(l[k:start])
            out.append(inner if inner.startswith("max(0,") else f"max(0,{inner})")
            out.append(")")
            k = e
        lines[i] = "".join(out)
    return "\n".join(lines)


_ENV_RE = re.compile(r"\\(begin|end)\{([^}]+)\}")


def _close_lists(body):
    """Ferme les listes (enumerate/itemize) oubliées : si un environnement se
    ferme alors qu'une liste ouverte à l'intérieur ne l'est pas, on insère les
    \\end{...} manquants juste avant."""
    out, last, stack = [], 0, []
    for m in _ENV_RE.finditer(body):
        kind, env = m.group(1), m.group(2)
        if kind == "begin":
            stack.append(env)
            continue
        if env in stack and stack[-1] != env:
            # listes ouvertes au-dessus de l'environnement qui se ferme
            missing = []
            while stack and stack[-1] != env and stack[-1] in ("enumerate", "itemize"):
                missing.append(stack.pop())
            if stack and stack[-1] == env:
                out.append(body[last:m.start()])
                out.append("".join(f"\\end{{{e}}}\n" for e in missing))
                last = m.start()
            else:
                stack.extend(reversed(missing))
        if stack and stack[-1] == env:
            stack.pop()
    out.append(body[last:])
    return "".join(out)


_NODE_RE = re.compile(r"\bnode((?:[ \t]*(?:\[[^\]]*\]|\([^()]*\)|at\s*\([^()]*\)))*)[ \t]*(\{)")


_MATH_TOK = re.compile(r"\\\$|\$\$|\$|\\\[|\\\]|\\\(|\\\)|\\begin\{(align\*?|equation\*?|gather\*?|multline\*?|cases)\}|\\end\{(align\*?|equation\*?|gather\*?|multline\*?|cases)\}")


def _cases_math(body):
    """\\begin{cases} écrit hors mode math -> entouré de \\[ ... \\]."""
    out, last, dollar, disp, envs = [], 0, False, 0, []
    for m in _MATH_TOK.finditer(body):
        t = m.group(0)
        if t == "\\$":
            continue
        if t == "$":
            dollar = not dollar
        elif t in ("\\[", "\\("):
            disp += 1
        elif t in ("\\]", "\\)"):
            disp = max(0, disp - 1)
        elif m.group(1):
            in_math = dollar or disp > 0 or any(e != "cases!" for e in envs)
            if m.group(1) == "cases" and not in_math:
                out.append(body[last:m.start()] + "\\[")
                last = m.start()
                envs.append("cases!")
            else:
                envs.append(m.group(1))
        elif m.group(2):
            if envs:
                e = envs.pop()
                if e == "cases!":
                    out.append(body[last:m.end()] + "\\]")
                    last = m.end()
    out.append(body[last:])
    return "".join(out)


_BOX = "definition|propriete|aretenir|exemplebox|methode|attention|experience|savaistu|exoresolu|exercice|corrige|bilan"


def _lonely_items(body):
    """Boîte dont le contenu commence directement par \\item (sans liste) -> itemize."""
    def fix(m):
        inner = m.group(3)
        if re.match(r"\s*\\item\b", inner) and not re.search(r"\\begin\{(itemize|enumerate|description)\}", inner):
            inner = "\n\\begin{itemize}" + inner.rstrip() + "\n\\end{itemize}\n"
        return m.group(1) + inner + m.group(4)
    return re.sub(r"(\\begin\{(" + _BOX + r")\}(?:\[(?:[^\[\]]|\{[^{}]*\})*\])?)(.*?)(\\end\{\2\})", fix, body, flags=re.S)


def _box_as_command(body):
    """\\aretenir[Titre]{contenu} (boîte écrite comme une commande) -> environnement."""
    pat = re.compile(r"\\(" + _BOX + r")(\[(?:[^\[\]]|\{[^{}]*\})*\])?\{")
    out, pos = [], 0
    while True:
        m = pat.search(body, pos)
        if not m:
            break
        depth, j = 0, m.end() - 1
        while j < len(body):
            depth += (body[j] == "{") - (body[j] == "}")
            if depth == 0:
                break
            j += 1
        if j >= len(body):
            break
        out.append(body[pos:m.start()])
        out.append("\\begin{" + m.group(1) + "}" + (m.group(2) or "") + body[m.end():j] + "\\end{" + m.group(1) + "}")
        pos = j + 1
    out.append(body[pos:])
    body = "".join(out)
    # \\savaistu[Titre] seul sur sa ligne, fermé plus loin par \\end{savaistu}
    for env in _BOX.split("|"):
        if body.count("\\end{" + env + "}") > body.count("\\begin{" + env + "}"):
            body = re.sub(r"^\\" + env + r"(\[[^\n]*\])?[ \t]*$", lambda m: "\\begin{" + env + "}" + (m.group(1) or ""), body, flags=re.M)
        else:
            # \\savaistu[Titre] seul sur sa ligne, jamais fermé : la boîte couvre le paragraphe qui suit
            body = re.sub(r"^\\" + env + r"(\[[^\n]*\])[ \t]*\n((?:[^\n]+\n)*?[^\n]+)(?=\n\s*\n|\n?\Z)",
                          lambda m: "\\begin{" + env + "}" + m.group(1) + "\n" + m.group(2) + "\n\\end{" + env + "}", body, flags=re.M)
    return body


def _node_align(body):
    out, last = [], 0
    for m in _NODE_RE.finditer(body):
        # contenu du nœud : de l'accolade ouvrante à sa fermante appariée
        start = m.end() - 1
        depth, j = 0, start
        while j < len(body):
            if body[j] == "{":
                depth += 1
            elif body[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        text, header = body[start:j + 1], m.group(1) or ""
        opt_m = re.search(r"\[([^\]]*)\]", header)
        opts = opt_m.group(1) if opt_m else ""
        if "\\\\" in text and "align" not in opts and "text width" not in opts:
            out.append(body[last:m.start()])
            if opt_m:
                new_header = header[:opt_m.start()] + "[" + opts + ", align=center]" + header[opt_m.end():]
            else:
                new_header = "[align=center]" + header
            out.append("node" + new_header + m.group(2))
            last = m.end()
    out.append(body[last:])
    return "".join(out)



def _tabularx_sans_x(body):
    """Met en cohérence tabular / tabularx (fin appariée par une pile) :
    - tabularx sans colonne X -> tabular (sinon bande vide à droite) ;
    - tabular avec des colonnes X -> tabularx{\\linewidth} (sinon erreur)."""
    tok = re.compile(r"\\begin\{(tabularx?)\}(\{[^{}]*\})?\{((?:[^{}]|\{[^{}]*\})*)\}|\\end\{(tabularx?)\}")
    out, last, stack = [], 0, []
    for m in tok.finditer(body):
        out.append(body[last:m.start()])
        if m.group(0).startswith("\\begin"):
            env, width, spec = m.group(1), m.group(2), m.group(3)
            if env == "tabularx" and not width:
                # \\begin{tabularx}{spec} sans largeur : le 1er groupe était la spec
                env_out = m.group(0)
                target = "tabularx"
            else:
                has_x = any(c in spec for c in "XCLR")
                target = "tabularx" if has_x else "tabular"
                if target == "tabularx":
                    w = width[1:-1] if width else "\\linewidth"
                    env_out = "\\begin{tabularx}{" + w + "}{" + spec + "}"
                else:
                    env_out = f"\\begin{{tabular}}{{{spec}}}"
            stack.append(target)
            out.append(env_out)
        else:
            target = stack.pop() if stack else m.group(4)
            out.append(f"\\end{{{target}}}")
        last = m.end()
    out.append(body[last:])
    return "".join(out)

def compile_check(body):
    """Compile le bloc seul. Renvoie (ok, extrait_du_log, ligne_fautive_ou_None)."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "preamble.tex").write_text(PREAMBLE.read_text())
        (d / "fonts").symlink_to(FONTS)
        (d / "t.tex").write_text(HEADER + body + "\n\\end{document}\n")
        try:
            p = subprocess.run([TECTONIC, "--keep-logs", "-c", "minimal", "t.tex"], cwd=d,
                               capture_output=True, text=True, timeout=400)
        except subprocess.TimeoutExpired:
            return False, "Compilation interrompue (délai dépassé) : boucle infinie probable dans le code TikZ/pgfplots.", None
        ok = p.returncode == 0 and (d / "t.pdf").exists()
        out = p.stdout + p.stderr
        logf = d / "t.log"
        line = None
        if not ok and logf.exists():
            lines = logf.read_text(errors="ignore").splitlines()
            errs = []
            for i, l in enumerate(lines):
                if l.startswith("!"):
                    errs.append("\n".join(lines[i:i + 6]))
                    if line is None:
                        for l2 in lines[i:i + 40]:
                            m = re.match(r"l\.(\d+)", l2)
                            if m:
                                line = int(m.group(1)) - HEADER.count("\n")
                                break
            out = "\n---\n".join(errs[:4]) or out
        out = re.sub(r"t\.tex:(\d+)", lambda m: f"ligne {int(m.group(1)) - HEADER.count(chr(10))}", out)
        if line is not None and not (1 <= line <= len(body.splitlines())):
            line = None
        return ok, out[-3000:], line


BLOCK_ENVS = ("popfigure", "tikzpicture", "tabularx", "tabular", "align*", "align", "center", "itemize", "enumerate",
              "cases", "pmatrix", "bmatrix", "definition", "propriete", "aretenir", "exemplebox", "methode",
              "attention", "experience", "savaistu", "exoresolu", "exercice", "corrige", "objectifs", "prerequis", "bilan")


def fault_span(lines, n):
    """Plage [a, b) du fragment à corriger autour de la ligne n (1-indexée) :
    le plus petit environnement « bloc » qui la contient (une figure entière de
    préférence), sinon le paragraphe."""
    i = n - 1
    best = None
    for a in range(i, -1, -1):
        m = re.search(r"\\begin\{([^}]+)\}", lines[a])
        if not m or m.group(1) not in BLOCK_ENVS:
            continue
        env = m.group(1)
        depth = 0
        for b in range(a, len(lines)):
            depth += lines[b].count("\\begin{" + env + "}") - lines[b].count("\\end{" + env + "}")
            if depth <= 0:
                break
        if b >= i:
            best = (a, b + 1)
            if env in ("popfigure",) or b - a > 25:
                break
            if env != "tikzpicture":
                break
    if best and best[1] - best[0] <= 220:
        return best
    a = i
    while a > 0 and lines[a - 1].strip() and i - a < 15:
        a -= 1
    b = i + 1
    while b < len(lines) and lines[b].strip() and b - i < 15:
        b += 1
    return a, b


def compile_fix(body, what, tries=6):
    for k in range(tries):
        ok, err, line = compile_check(body)
        if ok:
            return body, True
        log(f"  ✗ compilation {what} (essai {k+1}) : {err.strip().splitlines()[0] if err.strip() else '?'}")
        lines = body.splitlines()
        if line is not None and k < tries - 1:
            a, b = fault_span(lines, line)
            frag = "\n".join(lines[a:b])
            fixed = sanitize(llm([
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Le fragment LaTeX ci-dessous (extrait d'un cours plus long) provoque cette erreur de compilation XeLaTeX (l'erreur est signalée vers sa ligne {line - a}) :\n{err}\n\nFRAGMENT :\n{frag}\n\n"
                 "Renvoie UNIQUEMENT le fragment corrigé, complet, prêt à remplacer l'original (même début, même fin, mêmes environnements ouverts/fermés), en conservant tout le contenu. "
                 "Corrige la cause réelle de l'erreur (syntaxe mathématique, TikZ/pgfplots/circuitikz, tableau). Si une figure est trop complexe, simplifie-la en gardant son intention."}],
                REVIEW_MODELS, max_tokens=12000, temperature=0.2))
            if fixed.strip():
                body = "\n".join(lines[:a] + fixed.splitlines() + lines[b:])
            continue
        numbered = "\n".join(f"{n+1:4d}| {l}" for n, l in enumerate(lines))
        body = sanitize(llm([
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Ce code LaTeX ne compile pas avec XeLaTeX. Erreurs :\n{err}\n\nCode (numéroté pour référence) :\n{numbered}\n\n"
             "Renvoie le code COMPLET corrigé (sans les numéros de ligne), en conservant intégralement le contenu pédagogique. "
             "Corrige la cause de l'erreur ; si une figure TikZ est trop complexe, simplifie-la en gardant son intention. Réponds uniquement avec le LaTeX."}],
            REVIEW_MODELS, max_tokens=24000, temperature=0.2))
    ok, _, _ = compile_check(body)
    return body, ok


# ---------------------------------------------------------------------------
# Étapes
# ---------------------------------------------------------------------------
def lesson_brief(L):
    return (f"MATIÈRE : {MATIERE_LABEL} — classe de {NIVEAU} (enseignement général, sous-système francophone du Cameroun)\n"
            f"CHAPITRE {L['id']} : {L['titre']}\n"
            f"Période : {L.get('trimestre') or '-'}\n\n"
            "CONTENU OFFICIEL DE CE CHAPITRE (leçons / séances de la fiche de progression MINESEC ; elles décrivent le contenu du chapitre "
            "et doivent TOUTES être couvertes) :\n- " + "\n- ".join(L["contenu"]))


def n_sections(L):
    k = len(L["contenu"])
    return max(4, min(14, 3 + round(k / 3.5)))


def step_plan(L, d):
    f = d / "plan.json"
    if f.exists():
        return json.loads(f.read_text())
    log(f"[{L['id']}] plan")
    ns = n_sections(L)
    prompt = lesson_brief(L) + f"""

Conçois le PLAN DÉTAILLÉ du cours premium correspondant. Il doit couvrir TOUS les éléments du programme ci-dessus, dans un ordre progressif et logique, sans hors-programme inutile (de brefs compléments utiles à l'examen sont possibles, signalés comme tels). Le cours est rédigé en {LANG}.

Réponds UNIQUEMENT avec un objet JSON valide (pas de Markdown) :
{{
 "accroche": "situation d'introduction ancrée dans le quotidien camerounais ou une observation frappante (3-5 phrases)",
 "description": "2 phrases présentant le chapitre pour la page de garde",
 "objectifs": ["À la fin de ce chapitre, je sais ...", "... (6 à 10 objectifs formulés en savoir-faire)"],
 "prerequis": ["notion de la classe précédente utile ici", "... (3 à 5)"],
 "sections": [
   {{"titre": "titre court de la section", "contenu": ["point précis à traiter", "..."], "illustrations": ["figure TikZ/pgfplots à produire", "..."], "boites": ["définition de ...", "règle/propriété ...", "méthode pour ...", "attention : erreur fréquente ..."]}}
 ],
 "activite": {{"titre": "titre de l'activité d'intégration", "objectif": "...", "idee": "situation-problème authentique mobilisant plusieurs savoir-faire du chapitre, réalisable en classe"}},
 "exercices_idees": ["idée d'exercice type examen", "..."]
}}
Contraintes : exactement {ns} sections (le chapitre est volumineux : découpe-le intelligemment pour que chaque section forme une partie cohérente d'au plus 2500 mots environ) ; chaque section a 4 à 10 points de contenu précis, au moins 1 illustration pertinente quand c'est naturel, et 3 à 6 boîtes pédagogiques."""
    plan = None
    for attempt in range(4):
        txt = llm([{"role": "system", "content": f"Tu es un inspecteur pédagogique de {MATIERE_LABEL} au MINESEC (Cameroun) et concepteur de cours premium. Tu réponds uniquement en JSON valide."},
                   {"role": "user", "content": prompt}], WRITER_MODELS[attempt % 2:] + WRITER_MODELS[:attempt % 2], max_tokens=8000, temperature=0.4)
        m = re.search(r"\{.*\}", strip_fences(txt), re.S)
        try:
            plan = json.loads(m.group(0))
            if plan.get("sections") and plan.get("objectifs") and plan.get("prerequis") and plan.get("activite"):
                break
        except Exception:  # noqa: BLE001
            pass
        log(f"[{L['id']}] plan invalide, nouvel essai {attempt+1}/4")
        plan = None
    if plan is None:
        raise RuntimeError("plan JSON invalide après 4 essais")
    f.write_text(json.dumps(plan, ensure_ascii=False, indent=1))
    return plan


def plan_summary(plan):
    return "\n".join(f"{i+1}. {s['titre']}" for i, s in enumerate(plan["sections"]))


def write_block(L, plan, d, name, instruction, what):
    """Rédige → relit → compile un bloc. Cache : name.tex (brut), name.rev.tex, name.ok.tex."""
    ok_f = d / f"{name}.ok.tex"
    if ok_f.exists():
        return ok_f.read_text()
    raw_f, rev_f = d / f"{name}.tex", d / f"{name}.rev.tex"
    ctx = lesson_brief(L) + "\n\nPLAN GÉNÉRAL DU CHAPITRE :\n" + plan_summary(plan)
    if not raw_f.exists():
        log(f"[{L['id']}] rédaction {what}")
        for attempt in range(4):
            raw = sanitize(llm([{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": ctx + "\n\n" + instruction}],
                               WRITER_MODELS[attempt % 2:] + WRITER_MODELS[:attempt % 2], max_tokens=20000,
                               temperature=0.6 if attempt == 0 else 0.4))
            why = degenerate(raw)
            if not why:
                break
            log(f"[{L['id']}] rédaction {what} dégénérée ({why}), nouvel essai {attempt+1}/4")
        else:
            (d / f"{name}.err").write_text(f"ligne None\nRédaction dégénérée ({why})")
            return None
        raw_f.write_text(raw)
    raw = raw_f.read_text()
    if not rev_f.exists():
        log(f"[{L['id']}] relecture {what}")
        rev = sanitize(llm([{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": ctx + "\n\nVoici un bloc de cours rédigé par un collègue :\n\n" + raw + r"""

Tu es maintenant RELECTEUR EXPERT. Relis ce bloc avec une exigence maximale et renvoie la VERSION FINALE COMPLÈTE corrigée et améliorée :
1. Exactitude : vérifie chaque définition, règle, démonstration, calcul, valeur numérique, date et fait (corrige ou supprime tout ce dont tu n'es pas sûr).
2. Conformité au programme officiel de la classe (ni trop, ni trop peu) et au vocabulaire attendu à l'examen.
3. Pédagogie : explications pas à pas, transitions, pas de sauts logiques ; complète ce qui est survolé ; garde les illustrations et améliore-les si utile.
4. Conformité stricte au CONTRAT LaTeX (environnements autorisés, pas de boîtes imbriquées, % échappés, tableaux aux cellules cohérentes, décimaux TikZ avec un point).
Ne raccourcis pas le contenu : la version finale doit être au moins aussi riche. Réponds uniquement avec le LaTeX final."""}],
                           REVIEW_MODELS, max_tokens=22000, temperature=0.3))
        if degenerate(rev):
            log(f"[{L['id']}] relecture {what} dégénérée ({degenerate(rev)}), version initiale conservée")
            rev = raw
        elif len(rev) < 0.7 * len(raw):
            log(f"[{L['id']}] relecture {what} trop courte ({len(rev)} < {len(raw)}), version initiale conservée")
            rev = raw
        elif len(rev) > 2.5 * len(raw) or ((name in ("bilan", "corriges") or name.startswith("extra_")) and len(rev) > 1.6 * len(raw)):
            log(f"[{L['id']}] relecture {what} gonflée ({len(rev)} > {len(raw)}), version initiale conservée")
            rev = raw
        rev_f.write_text(rev)
    body = autofix(rev_f.read_text())
    ok, err, line = compile_check(body)
    if not ok and os.environ.get("LLM_FIX", "1") == "1":
        log(f"  ↻ réparation par le modèle {L['id']}/{name}")
        body2, ok2 = compile_fix(body, f"{L['id']}/{name}", tries=4)
        if ok2 and not degenerate(body2):
            body = autofix(body2)
            ok, err, line = compile_check(body)
            if ok:
                rev_f.write_text(body)
    if not ok:
        (d / f"{name}.err").write_text(f"ligne {line}\n{err}")
        log(f"  ✗ À CORRIGER {L['id']}/{name} (ligne {line}) : {err.strip().splitlines()[0] if err.strip() else '?'}")
        return None
    (d / f"{name}.err").unlink(missing_ok=True)
    ok_f.write_text(body)
    return body


def step_sections(L, plan, d):
    n = len(plan["sections"])
    jobs = []
    for i, s in enumerate(plan["sections"]):
        prev = plan["sections"][i - 1]["titre"] if i else None
        opening = ("Ouvre la section par l'accroche suivante, présentée de façon vivante, puis annonce la problématique : " + plan.get("accroche", "")
                   if i == 0 else "Fais une transition naturelle avec la section précédente (« " + str(prev) + " »).")
        instr = f"""RÉDIGE INTÉGRALEMENT LA SECTION {i+1}/{n} : « {s['titre']} ».

Contenu à traiter (tout, en profondeur) :
- """ + "\n- ".join(s["contenu"]) + """

Illustrations à produire (TikZ / pgfplots, dans popfigure) :
- """ + "\n- ".join(s.get("illustrations", [])) + """

Boîtes pédagogiques à inclure :
- """ + "\n- ".join(s.get("boites", [])) + f"""

Consignes :
- Commence par \\coursec{{{s['titre']}}} puis organise en \\courssub{{...}}.
- {opening}
- Explique chaque notion comme le meilleur professeur du monde : intuition d'abord, puis énoncé rigoureux (définition, règle ou propriété), établissement quand c'est exigible ou formateur, puis exemples résolus, puis erreurs fréquentes.
- Au moins une illustration de qualité quand la notion s'y prête, et au moins un exemple ou exercice résolu (exoresolu).
- Longueur visée : 1500 à 2800 mots de contenu (hors code des figures). Ne traite PAS les autres sections du plan."""
        jobs.append((f"s{i+1:02d}", instr, f"section {i+1}/{n}"))
    extra = [(k, EXTRA[k][1](L, plan), k) for k in ("activite", "methodes", "exercices", "bilan")]
    with cf.ThreadPoolExecutor(len(jobs) + len(extra)) as ex:
        futs = [ex.submit(write_block, L, plan, d, *j) for j in jobs + extra]
        res = [f.result() for f in futs]
    return res[:n], dict(zip(("activite", "methodes", "exercices", "bilan"), res[n:]))


EXTRA = {
    "activite": ("Activité d'intégration", lambda L, p: f"""RÉDIGE LA PARTIE « ACTIVITÉ D'INTÉGRATION » du chapitre (le titre de partie \\coursec est déjà posé : commence directement par \\courssub).
Activité proposée : {p['activite'].get('titre','')} — objectif : {p['activite'].get('objectif','')} — idée : {p['activite'].get('idee','')}.
Structure : \\courssub{{Objectifs de l'activité}}, \\courssub{{Situation}} (énoncé complet d'une situation-problème authentique et contextualisée, avec toutes les données nécessaires), \\courssub{{Consignes}} (questions guidées numérotées, mobilisant progressivement plusieurs savoir-faire), \\courssub{{Proposition de corrigé}} (réponse complète).
La situation doit être réaliste, ancrée dans un contexte camerounais si possible, et strictement au niveau de la classe. 1200 à 2000 mots."""),
    "methodes": ("Méthodes et exercices résolus", lambda L, p: """RÉDIGE LA PARTIE « MÉTHODES ET EXERCICES RÉSOLUS » (le titre \\coursec est déjà posé : commence directement par \\courssub).
Pour chaque savoir-faire du chapitre, donne une fiche \\begin{methode}[...] (démarche en étapes numérotées, réflexes, formules ou idées à retenir), immédiatement suivie d'un \\begin{exoresolu}[...] qui l'applique (énoncé type examen, puis après \\tcblower une solution TRÈS détaillée). 5 à 8 couples, de difficulté croissante. 1800 à 3000 mots."""),
    "exercices": ("Exercices", lambda L, p: """RÉDIGE LA PARTIE « EXERCICES » (le titre \\coursec est déjà posé ; commence directement par \\courssub).
Trois niveaux, chacun introduit par \\courssub : « Je vérifie mes connaissances » (4 exercices courts : QCM, vrai/faux justifié, définitions, applications directes), « Je m'entraîne » (4 exercices d'application directe), « Je me prépare à l'examen » (3 exercices longs de type examen officiel, avec barème entre parenthèses).
Chaque exercice dans un environnement \\begin{exercice}[titre court]...\\end{exercice}. NE DONNE PAS les corrections ici. Idées possibles : """ + "; ".join(p.get("exercices_idees", []))),
    "bilan": ("Fiche bilan", lambda L, p: """RÉDIGE LA « FICHE BILAN » du chapitre (le titre \\coursec est déjà posé ; ne mets pas de \\coursec).
Contenu : (1) une carte mentale TikZ lisible du chapitre (nœud central + 5 à 7 branches colorées, texte court, dans popfigure, largeur \\linewidth), (2) un environnement \\begin{bilan} ... \\end{bilan} contenant l'essentiel en listes compactes : définitions clés, règles/formules à connaître, méthodes, erreurs fréquentes, conseils pour l'examen. 600 à 1000 mots."""),
}


def step_extra(L, plan, d, key):
    title, mk = EXTRA[key]
    return title, write_block(L, plan, d, key, mk(L, plan), key)


def step_corriges(L, plan, d, exercices):
    title = "Corrigés des exercices"
    f_ok = d / "corriges.ok.tex"
    if f_ok.exists():
        return title, f_ok.read_text()
    instr = r"""RÉDIGE LA PARTIE « CORRIGÉS DES EXERCICES » (le titre \coursec est déjà posé ; commence directement). Voici les exercices, numérotés dans l'ordre d'apparition à partir de 1 :

""" + exercices + r"""

Pour CHAQUE exercice, dans l'ordre, un environnement \begin{corrige}[Exercice N — titre] ... \end{corrige} avec une correction complète et rigoureuse : réponses justifiées, calculs ou raisonnements détaillés, et pour les exercices type examen un barème indicatif et les attentes du correcteur. N'oublie aucun exercice."""
    return title, write_block(L, plan, d, "corriges", instr, "corrigés")


def step_lesson_extras(L, plan, d):
    return {}


def run_lesson(L):
    d = BUILD / L["id"]
    d.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    plan = step_plan(L, d)
    secs, extra = step_sections(L, plan, d)
    exo = extra["exercices"] if extra["exercices"] is not None else (d / "exercices.rev.tex").read_text()
    _, cor = step_corriges(L, plan, d, exo)
    results = list(secs) + list(extra.values()) + [cor]
    if any(r is None for r in results):
        log(f"[{L['id']}] ⚠ généré, mais {sum(r is None for r in results)} bloc(s) à corriger à la main")
        return
    (d / "DONE").write_text("ok")
    log(f"[{L['id']}] ✔ terminé en {(time.time()-t0)/60:.1f} min")


def main():
    if not KEYS:
        sys.exit("NVIDIA_API_KEYS manquant")
    cat = load_catalog()
    only = set(sys.argv[1:])
    todo = [L for L in cat["lessons"] if (not only or L["id"] in only)]
    workers = int(os.environ.get("WORKERS", "6"))
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(run_lesson, L): L["id"] for L in todo}
        for f in cf.as_completed(futs):
            try:
                f.result()
            except Exception as e:  # noqa: BLE001
                log(f"[{futs[f]}] ÉCHEC : {e}")


if __name__ == "__main__":
    main()
