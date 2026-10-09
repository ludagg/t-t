#!/usr/bin/env python3
"""Compile un cours avec un bloc figfix injecté puis compare figcheck avant/après.
Usage : figfix_test.py BLOC.tex COURS.tex [OUT]"""
import sys,os,re,subprocess,shutil,json
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import figcheck,collections
bloc,tex=sys.argv[1],os.path.abspath(sys.argv[2]);out=sys.argv[3] if len(sys.argv)>3 else "/tmp/figfix_out"
os.makedirs(out,exist_ok=True)
d=os.path.dirname(tex);name=os.path.basename(tex)[:-4]
src=open(tex,encoding="utf-8").read();b=open(bloc,encoding="utf-8").read()
src2=re.sub(r"%% FIGFIX-BEGIN.*?%% FIGFIX-END\n","",src,flags=re.S)
src2=src2.replace("\\begin{document}",b+"\\begin{document}",1)
tmp=os.path.join(d,"_ff_"+name+".tex");open(tmp,"w",encoding="utf-8").write(src2)
try:
    for _ in range(2):
        r=subprocess.run(["xelatex","-interaction=nonstopmode","-halt-on-error","-output-directory",out,tmp],cwd=d,capture_output=True,text=True)
    pdf=os.path.join(out,"_ff_"+name+".pdf")
    ok=os.path.exists(pdf) and r.returncode==0
finally: os.remove(tmp)
def stats(p):
    r=figcheck.analyse_pdf(p);pp=collections.defaultdict(list)
    for i in r["issues"]: pp[i["page"]].append(i)
    w={"texte/texte":4,"debordement":6,"marqueur":20,"hors-page":6,"trait/texte":1}
    sev=collections.Counter()
    for l in pp.values():
        sc=sum(w.get(x["kind"],1) for x in l);sev["A" if sc>=12 else "B" if sc>=4 else "C"]+=1
    k=collections.Counter(i["kind"] for i in r["issues"])
    return r["pages"],len(pp),dict(sev),dict(k)
print("compilé" if ok else "ÉCHEC",name)
print("avant",stats(tex[:-4]+".pdf"))
if ok: print("après",stats(pdf))
