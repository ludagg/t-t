#!/usr/bin/env python3
"""Génère docs/onbuch-generation/CHECKLIST_FIGURES.md : une case par cours, avec les pages à défauts avant/après.
Sources : rapports/figcheck/pages_signalees.csv (état initial) + un ou plusieurs results.jsonl récents (figcheck.py) + etat_figures.json (statut/notes saisis à la main).
Usage : checklist_figures.py [RESULTS.jsonl ...]   (relancer après chaque lot ; les cases cochées se mettent à jour toutes seules)"""
import sys,os,csv,json,glob,collections
R=os.path.abspath(os.path.join(os.path.dirname(__file__),"..","..",".."))
OUT=os.path.join(R,"docs","onbuch-generation","CHECKLIST_FIGURES.md")
ETAT=os.path.join(R,"docs","onbuch-generation","rapports","figcheck","etat_figures.json")
def key(p):
    if "/lots/" in p:
        q=p.split("/lots/")[1].split("/");cl,_,nm=q[1].partition("__");return q[0]+"|"+cl+"/"+nm
    p=p.replace(R+"/","").split("/");
    if "cours" in p: i=p.index("cours");return p[0]+"|"+p[i+1]+"/"+os.path.splitext(p[-1])[0]
    return p[0]+"|"+os.path.splitext(p[-1])[0]
W={"texte/texte":4,"debordement":6,"marqueur":20,"hors-page":6,"trait/texte":1}
def sev(l):
    sc=sum(W.get(x["kind"],1) for x in l);return "A" if sc>=12 else "B" if sc>=4 else "C"
base=collections.defaultdict(lambda:collections.Counter());tot_pages={}
for r in csv.DictReader(open(os.path.join(R,"docs/onbuch-generation/rapports/figcheck/pages_signalees.csv"))): base[key(r["pdf"])][r["gravite"]]+=1
cours=set()
for f in glob.glob(os.path.join(R,"*","cours","*","*","*.pdf")): cours.add(key(f))
now={}
for rp in sys.argv[1:]:
    for l in open(rp):
        x=json.loads(l);pp=collections.defaultdict(list)
        for i in x["issues"]: pp[i["page"]].append(i)
        c=collections.Counter(sev(v) for v in pp.values());now[key(x["pdf"])]=c
etat=json.load(open(ETAT)) if os.path.exists(ETAT) else {}
byf=collections.defaultdict(list)
for k in sorted(cours): byf[k.split("|")[0]].append(k)
def statut(k):
    e=etat.get(k,{}).get("statut")
    if e: return e
    if k in now: return "terminé" if now[k]["A"]==0 else "en cours"
    return "à faire"
rows=[];tot=collections.Counter()
L=["# Checklist figures — cours à reprendre un par un\n",
"Cocher un cours = **aucune page de gravité A** restante (mesure `figcheck`) et figures relues. On passe ensuite au suivant.\n",
"- Gravité A : à corriger en priorité (chevauchements, débordements). B : à corriger. C : mineur.",
"- *Avant* = état initial (avant le correctif global). *Après* = dernière mesure sur les PDF recompilés. `—` = pas encore recompilé.",
"- Statut et notes manuels : `rapports/figcheck/etat_figures.json` (clé `dossier|cours`, champs `statut`, `note`). Régénérer : `python3 docs/onbuch-generation/tools/checklist_figures.py <results.jsonl>`.\n"]
summ=[]
for f in sorted(byf):
    ks=byf[f];done=sum(1 for k in ks if statut(k) in("terminé",))
    a0=sum(base[k]["A"] for k in ks);a1=sum(now[k]["A"] for k in ks if k in now)
    summ.append((f,len(ks),done,a0,a1,sum(1 for k in ks if k in now)))
L.append("## Avancement par dossier\n");L.append("| Dossier | Cours terminés | Pages A avant | Pages A après (cours mesurés) |");L.append("|---|---:|---:|---:|")
tt=td=0
for f,n,d,a0,a1,m in summ:
    L.append(f"| {f} | {d} / {n} | {a0} | {a1 if m else '—'} ({m} mesurés) |");tt+=n;td+=d
L.insert(L.index("## Avancement par dossier\n"),f"**Total : {td} / {tt} cours terminés.**\n")
nxt=[k for k in cours if statut(k)!="terminé"]
nxt.sort(key=lambda k:-base[k]["A"]*1000-base[k]["B"])
L.append("\n## Prochains à traiter (les plus chargés)\n")
for k in nxt[:15]:
    f,c=k.split("|");L.append(f"- [ ] `{f}` — {c} (A {base[k]['A']}, B {base[k]['B']})")
for f in sorted(byf):
    L.append(f"\n## {f}\n");L.append("| ✔ | Cours | Avant (A/B/C) | Après (A/B/C) | Statut | Note |");L.append("|:-:|---|---|---|---|---|")
    for k in sorted(byf[f],key=lambda k:-base[k]["A"]):
        c=k.split("|")[1];b=base[k];s=statut(k)
        n=now.get(k);ap=f"{n['A']}/{n['B']}/{n['C']}" if n else "—"
        L.append(f"| {'[x]' if s=='terminé' else '[ ]'} | {c} | {b['A']}/{b['B']}/{b['C']} | {ap} | {s} | {etat.get(k,{}).get('note','')} |")
open(OUT,"w",encoding="utf-8").write("\n".join(L)+"\n");print("écrit",OUT,"| terminés",td,"/",tt)
