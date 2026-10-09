#!/usr/bin/env python3
"""Pipeline de génération des cours signés OnBuch+ — Upper Sixth (GCE Advanced Level), sous-système anglophone.

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


# Profil par matière : (rôle, langue du cours, consignes spécifiques)
PROFILES = {
    "physics": ("Physics", "English", "a senior Physics teacher and GCE Advanced Level examiner",
                "Derive laws properly (free-body diagrams, differential equations, energy balances, dimensional analysis). Every numerical result carries its SI unit and sensible significant figures. Physical constants: G = 6.67e-11 N m^2 kg^-2, e = 1.60e-19 C, h = 6.63e-34 J s, c = 3.00e8 m/s, m_e = 9.11e-31 kg, g = 9.81 m/s^2 (or 10 where the question says so). Circuit diagrams: circuitikz (see contract). Nuclear equations with mhchem."),
    "chemistry": ("Chemistry", "English", "a senior Chemistry teacher and GCE Advanced Level examiner",
                  "Use mhchem (\\ce{...}) for every formula and equation; give balanced equations with state symbols, conditions and mechanisms (curly arrows can be drawn with TikZ). Quantities with units and sensible significant figures. Atomic masses: H 1, C 12, N 14, O 16, Na 23, Cl 35.5, S 32, Cu 63.5. Include practical (qualitative analysis, titration) knowledge where the syllabus asks."),
    "biology": ("Biology", "English", "a senior Biology teacher and GCE Advanced Level examiner",
                "Explain processes step by step with accurate terminology; draw clear labelled diagrams with TikZ (cells, organelles, pathways, cycles, pedigree charts, graphs). Use correct genetics notation and worked genetics/ecology/statistics calculations. Link to Cameroon (malaria, sickle cell, agriculture, biodiversity of Mount Cameroon, etc.) when natural."),
    "geology": ("Geology", "English", "a senior Geology teacher and GCE Advanced Level examiner",
                "Use correct geological terminology; draw labelled cross-sections, stratigraphic columns, stereograms, maps and tables with TikZ/tabular. Include Cameroon geology (Cameroon Volcanic Line, Mount Cameroon, Congo craton, Benue trough, Lake Nyos) when relevant."),
    "computer-science": ("Computer Science", "English", "a senior Computer Science teacher and GCE Advanced Level examiner",
                         "Present algorithms in clear pseudocode (use the verbatim-free style: \\texttt lines inside itemize/enumerate or a tabular, NEVER the lstlisting or verbatim environments), trace tables, flowcharts (TikZ), logic circuits (TikZ), SQL/Boolean/number-system examples worked in detail."),
    "ict": ("ICT", "English", "a senior ICT teacher and GCE Advanced Level examiner",
            "Be practical: concepts, tools (spreadsheets, databases, networking, web, security), worked examples, annotated diagrams (TikZ) and tables; link to Cameroon (mobile money, ANTIC, CAMTEL, e-government, MTN/Orange) when natural."),
    "economics": ("Economics", "English", "a senior Economics teacher and GCE Advanced Level examiner",
                  "Explain theory with precise definitions, diagrams (supply/demand, cost curves, AD/AS, etc. drawn with pgfplots/TikZ), numerical worked examples, and evaluation (advantages/limits) in essay style. Use Cameroon/CEMAC/Africa examples (cocoa, oil, SONARA, CFA franc, AfCFTA)."),
    "geography": ("Geography", "English", "a senior Geography teacher and GCE Advanced Level examiner",
                  "Use precise terminology, case studies (Cameroon, Africa, world), schematic maps and diagrams drawn with TikZ, data tables and graphs (pgfplots), and essay-style evaluation. Never invent statistics: use rounded orders of magnitude and say 'about'."),
    "world-history": ("World History", "English", "a senior History teacher and GCE Advanced Level examiner",
                      "Present causes, events, consequences and historiography with accurate dates; timelines and maps drawn with TikZ; document-analysis and essay technique. Never invent quotations or precise figures; prefer well established facts."),
    "philosophy": ("Philosophy", "English", "a senior Philosophy teacher and GCE Advanced Level examiner",
                   "Present each thinker's arguments faithfully and critically, with short well-known quotations only if you are certain of them (otherwise paraphrase), objections and replies, examples from African and Cameroonian life, and essay technique. No fabricated quotations."),
    "english-language": ("English Language", "English", "a senior English Language teacher and GCE Advanced Level examiner",
                         "Teach through model texts (written by you), annotated examples, language-awareness tables and exam-style tasks (comprehension, summary, essay, speech, letter, report, oral). Provide full model answers."),
    "literature-in-english": ("Literature in English", "English", "a senior Literature in English teacher and GCE Advanced Level examiner",
                              "Analyse set texts and genres with accurate plot, character, theme, style and context; quote only short passages you are certain of, otherwise paraphrase; provide essay plans and model paragraphs."),
    "french": ("French", "French", "un professeur de français chevronné et examinateur du GCE Advanced Level (French)",
               "Rédige les cours en FRANÇAIS (les consignes de ce prompt sont en anglais, mais le contenu destiné à l'élève est en français), avec exemples, exercices et corrigés en français ; les titres de boîtes du préambule restent en anglais."),
    "francais-intensif": ("Français intensif", "French", "un professeur de français chevronné (cours intensif pour élèves anglophones)",
                          "Rédige les cours en FRANÇAIS simple et progressif adapté à des élèves anglophones (avec glossaire anglais–français si utile), exemples, exercices et corrigés ; les titres de boîtes du préambule restent en anglais."),
    "pure-maths-mechanics": ("Pure Mathematics with Mechanics", "English", "a senior Mathematics teacher and GCE Advanced Level examiner",
                             "Prove or derive results rigorously, with fully worked examples; graphs with pgfplots, vector/force diagrams with TikZ; include mechanics (kinematics, Newton's laws, momentum, energy, circular motion, SHM) wherever the syllabus requires."),
    "pure-maths-statistics": ("Pure Mathematics with Statistics", "English", "a senior Mathematics teacher and GCE Advanced Level examiner",
                              "Prove or derive results rigorously, with fully worked examples; graphs with pgfplots; include probability and statistics (distributions, hypothesis tests, regression, correlation) with tables of values worked by hand."),
    "further-maths": ("Further Mathematics", "English", "a senior Further Mathematics teacher and GCE Advanced Level examiner",
                      "Prove or derive results rigorously (complex numbers, matrices, series, hyperbolic functions, differential equations, polar curves, vectors, etc.) with fully worked examples; graphs with pgfplots, diagrams with TikZ."),
}
PROFILE = PROFILES.get(SUBJECT, (SUBJECT, "English", "a senior teacher and GCE Advanced Level examiner", ""))
LANG = PROFILE[1]
MATIERE_LABEL = PROFILE[0]

SYSTEM = r"""You are """ + PROFILE[2] + r""", author of reference textbooks for Cameroonian secondary schools (Anglophone sub-system, GCE Advanced Level, Upper Sixth), and designer of PREMIUM digital courses for the OnBuch+ app. Your courses are known as the clearest, most complete and most rigorous: every notion is introduced by an observation, an example or a concrete situation, then explained and justified properly, illustrated with worked examples and figures, and finally consolidated with exercises and warnings about common mistakes. You follow strictly the official MINESEC / Cameroon GCE Board Upper Sixth syllabus for the chapter given in the instruction. Course language: """ + LANG + r""". Tone: clear, rigorous but accessible, warm and motivating, at the level of an Upper Sixth student preparing the GCE Advanced Level examination. Use Cameroonian examples when pertinent and natural (never at the expense of rigour). Never invent precise statistics, dates, laws or quotations you are not sure of.
SUBJECT-SPECIFIC GUIDANCE: """ + PROFILE[3] + r"""

You produce ONLY LaTeX code (document body, compiled with XeLaTeX), with no explanation around it and no Markdown ``` fences.

LaTeX CONTRACT (mandatory):
- Forbidden: \documentclass, \usepackage, \begin{document}, \end{document}, \section, \subsection, \chapter, \includegraphics, \begin{figure}, \begin{table}, \input, \newcommand, \def, \label/\ref, Markdown (**bold**, # headings), \verb, verbatim/lstlisting, emojis, any non-Latin script characters (except where the course language requires accents).
- Headings: \coursec{Section title} (level 1, numbered automatically) and \courssub{Subtitle} (level 2, numbered automatically). Level 3: \textbf{...}\par. NEVER number headings yourself.
- Pedagogical boxes (optional title in square brackets):
  \begin{definition}[Title]...\end{definition}
  \begin{propriete}[Title]...\end{propriete}  (theorem, property, law, formula — say whether a proof is examinable)
  \begin{aretenir}[Title]...\end{aretenir}  (key points)
  \begin{exemplebox}[Title]...\end{exemplebox}
  \begin{methode}[Title]...\end{methode}  (numbered step-by-step method)
  \begin{attention}[Title]...\end{attention}  (common mistakes, traps)
  \begin{experience}[Title]...\end{experience}  (experiment / practical / guided demonstration / case study)
  \begin{savaistu}[Title]...\end{savaistu}  ("Did you know?": history, applications, Cameroon)
  \begin{exoresolu}[Title]Statement ... \tcblower Detailed solution ...\end{exoresolu}
  \begin{exercice}[Title]...\end{exercice}
  \begin{corrige}[Exercise n]...\end{corrige}
  NEVER nest a box inside another box.
- Highlighted keyword: \cle{word}. Bold: \textbf{}. Italic: \emph{}.
- Maths: $...$ inline, \[ ... \] for display, \begin{align*} ... \end{align*} for aligned multi-line work (use \\ and &). Sets: \mathbb{R}, \mathbb{N}, \mathbb{Z}, \mathbb{C}. Matrices: \begin{pmatrix} a & b \\ c & d \end{pmatrix}. Systems: \begin{cases} ... \end{cases}. DECIMAL NUMBERS USE A POINT (3.14).
- Quantities with units: \SI{9.81}{m.s^{-2}} or $9.81\ \mathrm{m\,s^{-2}}$.
- VARIATION / SIGN TABLES: one column per remarkable value AND one column between two values; ALL rows have EXACTLY the same number of cells as declared columns. Model: \begin{tabular}{|c|ccccc|}\hline $x$ & $a$ & & $c$ & & $b$ \\ \hline $f'(x)$ & & $-$ & $0$ & $+$ & \\ \hline $f$ & $f(a)$ & $\searrow$ & $f(c)$ & $\nearrow$ & $f(b)$ \\ \hline\end{tabular}
- Tables: \begin{center}\begin{tabularx}{\linewidth}{|l|X|X|}\hline ... \end{tabularx}\end{center} or tabular + booktabs. Header colours: \rowcolor{popblueL}. EVERY row must have exactly as many cells as columns. No \hline outside a tabular.
- Illustrations: put every figure in \begin{popfigure} ... \legende{Caption}\end{popfigure}. Use TikZ (diagrams, maps, graphs, flowcharts, mind maps, cycles, cross-sections) and pgfplots (function curves, scatter plots, charts). Allowed colours: popink, poporange, popdark, poppurple, popgreen, popblue, poppink, popgold, popmuted, popline, popblueL, popgreenL, and the standard black/white/gray.
- IN ALL TikZ / pgfplots CODE, decimals use a POINT: (7.389,2), xtick={1,2.718}, 0.55cm. Keep node text SHORT; never put \n or a literal line break inside node text (use \\ with text width / align=center); escape & as \& in node text.
- ELECTRIC CIRCUITS (circuitikz inside a tikzpicture, option [european]): \draw (0,0) to[R=$R$] (2,0) to[C=$C$] (4,0) to[L=$L$] (4,-2) to[sV=$u$] (0,-2) -- (0,0); components: R, C, L, sV, V, battery1, lamp, ammeter, voltmeter, D, nos, npn (anchors .B .C .E), thermistor, photoresistor.
- CHEMISTRY / NUCLEAR: \ce{H2SO4 + 2NaOH -> Na2SO4 + 2H2O}, \ce{^{235}_{92}U}, \ce{^{14}_{6}C -> ^{14}_{7}N + ^{0}_{-1}e}.
- Vectors: \vec{F}; cross product \wedge or \times; time derivatives \dot{x}, \ddot{x} or \dfrac{dx}{dt}.
- Lists: standard itemize / enumerate. Inside enumerate/itemize every entry starts with \item (never bare "a) ..." lines).
- The % symbol must be escaped \%; the characters & _ # must be escaped outside the environments that expect them (so NOT inside $...$, \[...\], pmatrix, cases, tabular).
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
HEADER = r"""\newcommand{\DOCMATIERE}{Test}\newcommand{\DOCNIVEAU}{Upper Sixth}\newcommand{\DOCMODULE}{Test}\newcommand{\DOCLECON}{00}\newcommand{\DOCTITRE}{Test}
\input{preamble.tex}
\begin{document}
"""


_BR = r"(?:\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})"
_PROT = re.compile("|".join([
    r"\\begin\{(tikzpicture|axis|align\*?|equation\*?|gather\*?|multline\*?|pmatrix|bmatrix|cases|eqnarray\*?|displaymath|math)\}.*?\\end\{\1\}",
    r"\$\$.*?\$\$", r"(?<!\\)\$(?:[^$\\]|\\.)*\$", r"\\\[.*?\\\]", r"\\\(.*?\\\)",
    r"\\ce" + _BR,
    r"\\(?:SI|si|num|qty|ang|SIrange|numrange|SIlist|numlist)" + _BR + "(?:" + _BR + ")?(?:" + _BR + ")?",
    r"\\(?:ensuremath|mathrm|mathbf|mathit|label|ref|legende|tikzmarknode)" + _BR,
    r"\\[_^%&#$]", r"%[^\n]*",
]), re.S)
_SUP = re.compile(r"\^(\{[^{}]*\}|[0-9]+[+\-]?|[+\-]|[A-Za-z](?![A-Za-z]))")
_GREEK = re.compile(r"\\(Delta|Lambda|Omega|Sigma|Pi|Phi|Psi|Gamma|Theta|alpha|beta|gamma|delta|epsilon|theta|lambda|mu|nu|pi|rho|sigma|tau|phi|omega|rightarrow|leftarrow|rightleftharpoons|approx|times|pm|leq|geq|neq|infty)(?![A-Za-z])((?:_\{[^{}]*\}|_[A-Za-z0-9]|\^\{[^{}]*\}|\^[A-Za-z0-9])?)")
_SUB = re.compile(r"(?<=[A-Za-z0-9)\]])_(\{[^{}]*\}|[0-9]+)")


def _text_scripts(seg):
    """Exposants/indices/lettres grecques en texte -> math ; _ nu -> \\_ (les $...$ créés sont mis à l'abri pendant l'échappement)."""
    store = []

    def keep(txt):
        store.append("$" + txt + "$")
        return "\x01%d\x02" % (len(store) - 1)
    seg = _GREEK.sub(lambda m: keep("\\" + m.group(1) + m.group(2)), seg)
    seg = _SUP.sub(lambda m: keep("^{" + m.group(1).strip("{}") + "}"), seg)
    seg = _SUB.sub(lambda m: keep("_{" + m.group(1).strip("{}") + "}"), seg)
    seg = re.sub(r"(?<!\\)_", lambda _m: "\\_", seg)   # _ nu en texte -> \_
    return re.sub("\x01(\\d+)\x02", lambda m: store[int(m.group(1))], seg)


def text_scripts_fix(body):
    """^ et _ hors mode math (texte courant, cellules de tableau) -> exposants/indices en math."""
    if len(re.findall(r"(?<!\\)\$", body)) % 2:   # $ déséquilibrés : on ne touche à rien (risque d'inverser texte et math)
        return body
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
    body = re.sub(r"(\\\[(?:(?!\\\]).)*?\n)\\\[(\\boxed\{[^\n]*\})\\\]\s*\n(\\\])", lambda m: m.group(1) + m.group(2) + "\n" + m.group(3), body, flags=re.S)
    body = re.sub(r"\^\\prime(?=[a-z_^])", "'", body)
    body = re.sub(r"\$\^\\prime\$(?=[a-z])", "'", body)
    def strip(m):
        return re.sub(r"\$([\^_]\{[^{}]*\})\$", r"\1", m.group(0))
    body = re.sub(r"\\(?:ensuremath|mathrm|SI|si|SIrange|numrange)" + _BR + "(?:" + _BR + ")?(?:" + _BR + ")?", strip, body)
    # \n littéral dans \texttt{...} (code C, chaînes)
    body = re.sub(r"\\texttt\{((?:[^{}]|\\[{}])*)\}", lambda m: "\\texttt{" + re.sub(r"(?<!\\)\\n(?![A-Za-z])", lambda _k: "\\textbackslash{}n", m.group(1)) + "}", body)
    return body


_AMP_PROT = re.compile("|".join([
    r"\\begin\{(tabular\*?|tabularx|array|align\*?|aligned|alignedat|gather\*?|multline\*?|matrix|pmatrix|bmatrix|vmatrix|cases|split|eqnarray\*?|tikzpicture|axis)\}.*?\\end\{\1\}",
    r"\$\$.*?\$\$", r"(?<!\\)\$(?:[^$\\]|\\.)*\$", r"\\\[.*?\\\]", r"\\\(.*?\\\)",
    r"\\ce" + _BR, r"\\[&_^%#$]", r"%[^\n]*",
]), re.S)


def amp_fix(body):
    """& nu hors tableau/alignement -> \\&."""
    if len(re.findall(r"(?<!\\)\$", body)) % 2:
        return body
    out, pos = [], 0
    for m in _AMP_PROT.finditer(body):
        out.append(re.sub(r"(?<!\\)&", lambda _k: "\\&", body[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(re.sub(r"(?<!\\)&", lambda _k: "\\&", body[pos:]))
    return "".join(out)


def node_lists_fix(body):
    """Liste (itemize/enumerate) dans un nœud TikZ sans largeur de texte -> ajoute text width."""
    out, pos = [], 0
    for m in re.finditer(r"\\node\[([^\]]*)\]", body):
        if m.start() < pos:
            continue
        j = body.find("{", m.end())
        if j < 0 or ";" in body[m.end():j]:
            continue
        depth, k = 0, j
        while k < len(body):
            c = body[k]
            if c == "\\":
                k += 2
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        content = body[j:k]
        if re.search(r"\\begin\{(itemize|enumerate)\}", content) and "text width" not in m.group(1):
            out.append(body[pos:m.start(1)] + "text width=11cm, align=left, " + m.group(1))
            pos = m.end(1)
    out.append(body[pos:])
    return "".join(out)


def us_fix(body):
    """Règles propres à l'Upper Sixth (erreurs fréquentes observées)."""
    body = repair_damage(body)
    body = node_lists_fix(body)
    body = re.sub(r"\$\$(\\[A-Za-z]+)\$\$", lambda m: "$" + m.group(1) + "$", body)     # $$\rightarrow$$ (dégât ancien)
    body = re.sub(r"(\\(?:textbf|textit|emph)\{[^{}\n]*?)(\\begin\{)", lambda m: m.group(1) + "}" + m.group(2), body)   # accolade non fermée avant un \begin
    body = ce_fix(body)
    body = text_scripts_fix(body)
    body = amp_fix(body)
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
        L, out, depth = body.split("\n"), [], 0
        for l in L:
            m = re.match(r"^[ \t]*(\\boxed\{.*\})[ \t]*$", l)
            if m and depth <= 0:
                out.append("\\[" + m.group(1).replace("$", "") + "\\]")
                continue
            depth += l.count("\\[") - l.count("\\]")
            depth += l.count("\\begin{align") + l.count("\\begin{equation") + l.count("\\begin{gather") - l.count("\\end{align") - l.count("\\end{equation") - l.count("\\end{gather")
            out.append(l)
        return "\n".join(out)
    body = _boxed(body)
    body = re.sub(r"\$\$(\\[A-Za-z]+)\$ ", lambda m: "$" + m.group(1) + " ", body)       # $$\Delta$ h = .. $ -> $\Delta h = .. $
    body = re.sub(r"\\texttt\{((?:[^{}]|\\[{}])*)\}", lambda m: "\\texttt{" + re.sub(r"\\(?=[A-Z][a-z]|[A-Z]{2})(?!Delta|Gamma|Lambda|Omega|Sigma|Theta|Phi|Psi|Pi\b)", lambda _k: "\\textbackslash{}", m.group(1)) + "}", body)  # C:\Windows
    body = re.sub(r"\\addlegendentry\{((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}", lambda m: "\\addlegendentry{" + re.sub(r"\\to(?![A-Za-z])", lambda _k: "\\rightarrow ", re.sub(r"\\bar\b", lambda _k: "\\overline", m.group(1))).replace("{,}", ",").replace(",", "{,}") + "}", body)  # \bar est une commande pgfplots
    body = re.sub(r"\bcircuit logic\b(?! US| IEC| CDH)", lambda _m: "circuit logic US", body)
    def _gates(m):   # ancres des portes logiques, figure par figure : input N (input pour une porte NON) / output
        t = m.group(0)
        if not re.search(r"gate US|logic gate", t):
            return t
        nots = set(re.findall(r"\\node\[[^\]]*not gate[^\]]*\][^;]*?\((\w+)\)\s*\{", t))
        t = re.sub(r"\((\w+)\.in(?: (\d))?\)", lambda k: "(" + k.group(1) + (".input)" if k.group(1) in nots else ".input " + (k.group(2) or "1") + ")"), t)
        return re.sub(r"\.out\)", lambda _k: ".output)", t)
    body = re.sub(r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}", _gates, body, flags=re.S)
    body = re.sub(r"(?<=[\d(])%(?=[ \t]?[A-Za-z)\d])", lambda _m: "\\%", body)   # % de prose non échappé : (% GDP), 50%x
    body = re.sub(r"\\og\s*", lambda _m: "``", body)                       # \og ... \fg (guillemets français)
    body = re.sub(r"\s*\\fg\b\s*", lambda _m: "''", body)
    body = re.sub(r"\\(begin|end)\{(\w+)>", lambda m: "\\" + m.group(1) + "{" + m.group(2) + "}", body)   # \end{exemplebox>
    def _lonely_items(text):   # \item sans liste -> itemize
        L, out, depth, opened = text.split("\n"), [], 0, False
        for l in L:
            st = l.strip()
            if st.startswith("\\item") and depth == 0 and not opened:
                out.append("\\begin{itemize}"); opened = True
            elif opened and depth == 0 and (not st or st.startswith("\\end{") or st.startswith("\\tcblower") or (st.startswith("\\begin{") and not st.startswith("\\begin{itemize}") and not st.startswith("\\begin{enumerate}"))):
                out.append("\\end{itemize}"); opened = False
            depth += len(re.findall(r"\\begin\{(?:itemize|enumerate|description)\}", l)) - len(re.findall(r"\\end\{(?:itemize|enumerate|description)\}", l))
            out.append(l)
        if opened:
            out.append("\\end{itemize}")
        return "\n".join(out)
    body = _lonely_items(body)
    body = re.sub(r"\\clip\[[^\]\n]*\]", lambda _m: "\\clip", body)   # \clip n'accepte pas d'options
    body = re.sub(r"\\label\b(?!\s*\{)", lambda _m: "\\lbl", body)           # \label utilisé comme variable
    body = re.sub(r"\\degree(?![A-Za-z])", lambda _m: "\\ensuremath{{}^{\\circ}}", body)
    body = body.replace("\\then ", "then ").replace("\\celsius", "\\ensuremath{{}^{\\circ}\\mathrm{C}}")
    body = re.sub(r"\\(begin|end)\{(examtip|examtips|tip|keypoint|keypoints|note|remark|remarque|example|worked|summary|info|law|theorem|lemma|corollary|rule|principle|caution|warning|activity|case|casestudy|task|question|problem)\}",
                  lambda m: "\\" + m.group(1) + ("{aretenir}" if m.group(2) in ("examtip", "examtips", "tip", "keypoint", "keypoints", "summary") else "{propriete}" if m.group(2) in ("law", "theorem", "lemma", "corollary", "rule", "principle") else "{exemplebox}" if m.group(2) in ("example", "worked", "case", "casestudy", "activity", "task", "question", "problem") else "{attention}"), body)
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
    return (f"SUBJECT: {MATIERE_LABEL} — Upper Sixth (GCE Advanced Level), Anglophone sub-system of Cameroon\n"
            f"CHAPTER {L['id']}: {L['titre']}\n"
            f"Term: {L.get('trimestre') or '-'}\n\n"
            "OFFICIAL SYLLABUS CONTENT FOR THIS CHAPTER (lessons/topics listed in the MINESEC progression sheet; "
            "they describe the content of the chapter and must ALL be covered):\n- " + "\n- ".join(L["contenu"]))


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

Design the DETAILED PLAN of the premium course for this chapter. It must cover ALL the syllabus items above, in a progressive and logical order, without needless off-syllabus material (brief extras useful for the GCE exam may be added and flagged as such). The course language is {LANG}.

Reply ONLY with a valid JSON object (no Markdown):
{{
 "accroche": "an introductory hook/problem situation rooted in Cameroonian daily life or a striking observation (3-5 sentences)",
 "description": "2 sentences presenting the chapter for the cover page",
 "objectifs": ["By the end of this chapter I can ...", "... (6 to 10 objectives written as skills)"],
 "prerequis": ["notion from Lower Sixth / earlier useful here", "... (3 to 5)"],
 "sections": [
   {{"titre": "short section title", "contenu": ["precise point to cover", "..."], "illustrations": ["figure TikZ/pgfplots to produce", "..."], "boites": ["definition of ...", "property/law/theorem ...", "method for ...", "warning: common mistake ..."]}}
 ],
 "activite": {{"titre": "title of the integration activity", "objectif": "...", "idee": "an authentic problem/case study mobilising several skills of the chapter, doable in class"}},
 "exercices_idees": ["exam-style exercise idea", "..."]
}}
Constraints: exactly {ns} sections (the chapter is large: split it sensibly so that each section is a coherent part of at most about 2500 words); each section has 4 to 10 precise content points, at least 1 relevant illustration when natural, and 3 to 6 pedagogical boxes."""
    plan = None
    for attempt in range(4):
        txt = llm([{"role": "system", "content": f"You are a MINESEC / GCE Board pedagogical inspector for {MATIERE_LABEL} and a designer of premium courses. You reply only with valid JSON."},
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


def degenerate(t):
    """Détecte une sortie de modèle dégénérée (charabia, boucle de répétition, autre alphabet)."""
    if len(t.strip()) < 500:
        return "trop court"
    if re.search(r"[\u0400-\u04FF\u0590-\u06FF\u3000-\u9FFF\uAC00-\uD7AF\uFF00-\uFFEF]", t):
        return "caractères d'un autre alphabet"
    if re.search(r"(?:\.[a-z]){6}", t):
        return "charabia"
    for env in ("exercice", "corrige", "exoresolu", "definition", "propriete", "methode", "exemplebox", "aretenir", "attention"):
        if t.count("\\begin{%s}" % env) != t.count("\\end{%s}" % env):
            return "bloc tronqué (environnement %s non fermé)" % env
    if "\\begin{}" in t or "\u0308" in t or "<|" in t or re.search(r"\\(?:begin|end)(?![{A-Za-z@])", t):
        return "marqueurs parasites"
    for l in t.splitlines():
        opts = re.findall(r"\b([a-z ]+=[0-9a-z.]+(?:em|cm|pt|mm)?)\b", l)
        if opts and max(opts.count(o) for o in set(opts)) >= 6:
            return "options répétées"
    lines = [l.strip() for l in t.splitlines() if len(l.strip()) > 3]
    if len(lines) > 30 and len(set(lines)) < 0.6 * len(lines):
        return "répétitions"
    if t.count("\\begin{") + 5 < t.count("\\end{") or t.count("\\end{") + 5 < t.count("\\begin{"):
        return "environnements déséquilibrés"
    return None


def drop_fault(body, tries=8):
    """Correction déterministe de dernier recours (sans modèle) : supprime la ligne fautive, ou à défaut le bloc
    (figure, tableau, paragraphe) qui la contient, pourvu que \\begin/\\end restent équilibrés."""
    for _ in range(tries):
        ok, err, line = compile_check(body)
        if ok:
            return body, True
        if line is None:
            return body, False
        lines = body.splitlines()
        txt = lines[line - 1]
        if not re.search(r"\\(begin|end)\{", txt) and txt.strip() and not txt.strip().startswith("%"):
            lines[line - 1] = "% (ligne supprimée : erreur de compilation)"
        else:
            a, b = fault_span(lines, line)
            frag = "\n".join(lines[a:b])
            if len(re.findall(r"\\begin\{", frag)) != len(re.findall(r"\\end\{", frag)):
                return body, False
            lines[a:b] = ["% (bloc supprimé : erreur de compilation)"]
        body = "\n".join(lines)
    ok, _, _ = compile_check(body)
    return body, ok


def write_block(L, plan, d, name, instruction, what):
    """Rédige → relit → compile un bloc. Cache : name.tex (brut), name.rev.tex, name.ok.tex."""
    ok_f = d / f"{name}.ok.tex"
    if ok_f.exists():
        return ok_f.read_text()
    raw_f, rev_f = d / f"{name}.tex", d / f"{name}.rev.tex"
    ctx = lesson_brief(L) + "\n\nGENERAL PLAN OF THE CHAPTER:\n" + plan_summary(plan)
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
                            {"role": "user", "content": ctx + "\n\nHere is a block of the course written by a colleague:\n\n" + raw + r"""

You are now an EXPERT REVIEWER. Review this block with maximum rigour and return the COMPLETE FINAL corrected and improved VERSION:
1. Accuracy: check every definition, law, derivation, calculation, numerical value, unit, date and fact (correct anything doubtful; remove anything you cannot stand behind).
2. Conformity with the official Upper Sixth syllabus (neither too much nor too little) and the vocabulary expected in the GCE Advanced Level examination.
3. Pedagogy: step-by-step explanations, transitions, no logical jumps; complete anything that is only skimmed; keep the illustrations and improve them if useful.
4. Strict conformity with the LaTeX CONTRACT (allowed environments, standard maths syntax, no nested boxes, % escaped, tables with consistent cell counts, TikZ decimals with a point).
Do not shorten the content: the final version must be at least as rich. Reply only with the final LaTeX."""}],
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
    if not ok and os.environ.get("LLM_FIX", "0") == "1":
        log(f"  ↻ réparation par le modèle {L['id']}/{name}")
        body2, ok2 = compile_fix(body, f"{L['id']}/{name}", tries=4)
        if ok2 and not degenerate(body2):
            body = autofix(body2)
            ok, err, line = compile_check(body)
            if ok:
                rev_f.write_text(body)
    if not ok and os.environ.get("DROP_FAULT", "0") == "1":
        body3, ok3 = drop_fault(body)
        if ok3:
            log(f"  ✂ figure/tableau fautif supprimé dans {L['id']}/{name}")
            body, ok = body3, True
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
        opening = ("Open the section with the following hook, presented vividly, then state the problem: " + plan.get("accroche", "")
                   if i == 0 else "Make a natural transition from the previous section (" + str(prev) + ").")
        instr = f"""WRITE IN FULL SECTION {i+1}/{n}: "{s['titre']}".

Content to cover (everything, in depth):
- """ + "\n- ".join(s["contenu"]) + """

Illustrations to produce (TikZ / pgfplots, inside popfigure):
- """ + "\n- ".join(s.get("illustrations", [])) + """

Pedagogical boxes to include:
- """ + "\n- ".join(s.get("boites", [])) + f"""

Instructions:
- Start with \\coursec{{{s['titre']}}} then organise with \\courssub{{...}}.
- {opening}
- Explain each notion as the best teacher in the world would: intuition first, then a rigorous statement (definition, law or property), derivation when examinable or instructive, then worked examples, then common mistakes.
- At least one quality illustration when the notion lends itself to it, and at least one worked example or worked exercise (exoresolu).
- Target length: 1500 to 2800 words of content (excluding figure code). Do NOT cover the other sections of the plan."""
        jobs.append((f"s{i+1:02d}", instr, f"section {i+1}/{n}"))
    extra = [(k, EXTRA[k][1](L, plan), k) for k in ("activite", "methodes", "exercices", "bilan")]
    with cf.ThreadPoolExecutor(len(jobs) + len(extra)) as ex:
        futs = [ex.submit(write_block, L, plan, d, *j) for j in jobs + extra]
        res = [f.result() for f in futs]
    return res[:n], dict(zip(("activite", "methodes", "exercices", "bilan"), res[n:]))


EXTRA = {
    "activite": ("Integration activity", lambda L, p: f"""WRITE THE PART "INTEGRATION ACTIVITY" of the chapter (the \\coursec part title is already placed: start directly with \\courssub).
Proposed activity: {p['activite'].get('titre','')} — aim: {p['activite'].get('objectif','')} — idea: {p['activite'].get('idee','')}.
Structure: \\courssub{{Aims of the activity}}, \\courssub{{Situation}} (full statement of an authentic, contextualised problem situation with all necessary data), \\courssub{{Tasks}} (guided numbered questions mobilising several skills progressively), \\courssub{{Model answer}} (a complete worked answer).
The situation must be realistic, rooted in a Cameroonian context if possible, and strictly at Upper Sixth level. 1200 to 2000 words."""),
    "methodes": ("Methods and worked examples", lambda L, p: """WRITE THE PART "METHODS AND WORKED EXAMPLES" (the \\coursec title is already placed: start directly with \\courssub).
For each skill of this chapter give a \\begin{methode}[...] card (numbered steps, reflexes, key formulas/ideas to remember), immediately followed by a \\begin{exoresolu}[...] applying it (GCE-style statement, then after \\tcblower a VERY detailed solution). 5 to 8 such pairs, of increasing difficulty. 1800 to 3000 words."""),
    "exercices": ("Exercises", lambda L, p: """WRITE THE PART "EXERCISES" (the \\coursec title is already placed; start directly with \\courssub).
Three levels, each introduced by \\courssub: "I check my knowledge" (4 short exercises: MCQ, true/false with justification, definitions, direct calculations), "I practise" (4 direct-application exercises), "I prepare for the GCE" (3 longer exam-style exercises / essay questions of the GCE Advanced Level type, with marks in brackets).
Each exercise in an environment \\begin{exercice}[short title]...\\end{exercice}. DO NOT give the answers here. Possible ideas: """ + "; ".join(p.get("exercices_idees", []))),
    "bilan": ("Summary sheet", lambda L, p: """WRITE THE "SUMMARY SHEET" of the chapter (the \\coursec title is already placed; do not add a \\coursec).
Content: (1) a readable TikZ mind map of the chapter (central node + 5 to 7 coloured branches, short text, inside popfigure, width \\linewidth), (2) a \\begin{bilan} ... \\end{bilan} environment containing the essentials in compact lists: key definitions, laws/formulas/theorems to know, methods, common mistakes, GCE tips. 600 to 1000 words."""),
}


def step_extra(L, plan, d, key):
    title, mk = EXTRA[key]
    return title, write_block(L, plan, d, key, mk(L, plan), key)


def step_corriges(L, plan, d, exercices):
    title = "Solutions to the exercises"
    f_ok = d / "corriges.ok.tex"
    if f_ok.exists():
        return title, f_ok.read_text()
    instr = r"""WRITE THE PART "SOLUTIONS TO THE EXERCISES" (the \coursec title is already placed; start directly). Here are the exercises, numbered in order of appearance from 1:

""" + exercices + r"""

For EACH exercise, in order, an environment \begin{corrige}[Exercise N — title] ... \end{corrige} with a complete and rigorous solution: justified answers, detailed calculations, full proofs when asked, and for the GCE-type exercises an indicative mark scheme and the typical examiner expectations. Do not skip any exercise."""
    return title, write_block(L, plan, d, "corriges", instr, "solutions")


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
