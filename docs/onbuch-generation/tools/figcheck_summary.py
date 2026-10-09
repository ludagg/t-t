#!/usr/bin/env python3
"""Résume results.jsonl de figcheck.py : summary.md + pages_signalees.csv. Usage : figcheck_summary.py DIR"""
import sys,json,os,collections,csv
d=sys.argv[1];rows=[json.loads(l) for l in open(os.path.join(d,"results.jsonl"))]
root=os.path.abspath(os.path.join(os.path.dirname(__file__),"..","..",".."))+os.sep
def folder(p): return os.path.relpath(p,root).split(os.sep)[0]
def rel(p): return os.path.relpath(p,root)
by=collections.defaultdict(lambda:[0,0,0,0,0]);kinds=collections.Counter();pages=[]
tot_pages=0;err=[];sevc=collections.Counter()
for r in rows:
    f=folder(r["pdf"]);b=by[f];b[0]+=1;tot_pages+=r["pages"];b[4]+=r["pages"]
    if r["error"]: err.append((rel(r["pdf"]),r["error"]))
    pp=collections.defaultdict(list)
    for i in r["issues"]: pp[i["page"]].append(i);kinds[i["kind"]]+=1
    if pp: b[1]+=1
    b[2]+=len(pp);b[3]+=len(r["issues"])
    for pg,l in pp.items():
        w={"texte/texte":4,"debordement":6,"marqueur":20,"hors-page":6,"trait/texte":1}
        sc=sum(w.get(x["kind"],1) for x in l);sev="A" if sc>=12 else "B" if sc>=4 else "C"
        sevc[sev]+=1
        pages.append((rel(r["pdf"]),pg,len(l),"/".join(sorted({x["kind"] for x in l})),"; ".join(x["text"] for x in l[:3]),sev,sc))
pages.sort(key=lambda x:-x[6])
with open(os.path.join(d,"pages_signalees.csv"),"w",newline="") as f:
    w=csv.writer(f);w.writerow(["pdf","page","nb_defauts","types","exemples","gravite","score"]);w.writerows(pages)
n=len(rows);bad=sum(1 for r in rows if r["issues"])
L=["# Rapport figcheck (phase 1)\n",f"- PDF analysés : **{n}** ({tot_pages} pages)",f"- PDF avec au moins une page signalée : **{bad}** ({100*bad/max(n,1):.0f} %)",f"- Pages signalées : **{len(pages)}** ({100*len(pages)/max(tot_pages,1):.1f} % des pages)",f"- Défauts : "+", ".join(f"{k} {v}" for k,v in kinds.most_common()),f"- Gravité A (à corriger en priorité) : **{sevc['A']}**, B (à corriger) : **{sevc['B']}**, C (mineur) : **{sevc['C']}**",f"- Erreurs de lecture : {len(err)}\n","## Par dossier\n","| Dossier | PDF | PDF signalés | Pages signalées | Défauts | Pages |","|---|---:|---:|---:|---:|---:|"]
for f,b in sorted(by.items(),key=lambda x:-x[1][2]): L.append(f"| {f} | {b[0]} | {b[1]} | {b[2]} | {b[3]} | {b[4]} |")
L+=["\n## Pages les plus chargées (40)\n","| PDF | page | grav. | défauts | types | exemples |","|---|---:|---|---:|---|---|"]
for p in pages[:40]: L.append(f"| {p[0].split('/cours/')[-1][:60]} ({p[0].split('/')[0]}) | {p[1]} | {p[5]} | {p[2]} | {p[3]} | {p[4][:80]} |")
open(os.path.join(d,"summary.md"),"w").write("\n".join(L)+"\n");print("\n".join(L[:12]))
