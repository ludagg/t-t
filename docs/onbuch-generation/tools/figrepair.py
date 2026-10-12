#!/usr/bin/env python3
"""Réparation automatique des figures défectueuses par un modèle NVIDIA, avec vérification déterministe.

Pour chaque cours : 1) compile toutes les figures (popfigure) en un mini-document, une par page ; 2) mesure chaque figure (figcheck) ;
3) pour chaque figure de score >= SEUIL, demande au modèle de la redessiner (même texte, mise en page propre), recompile la figure seule,
   et ne l'accepte que si le score baisse nettement ET que le texte est conservé ; sinon l'ancienne figure reste ;
4) réécrit le .tex, recompile le cours entier, restaure l'original si la compilation échoue.
Usage : NVIDIA_API_KEYS=k1,k2 figrepair.py [--jobs N] [--seuil 4] [--max-figs 30] [--out DIR] COURS.tex ...   |   --ordre-checklist N  (les N cours les plus chargés non traités)
Journal : docs/onbuch-generation/rapports/figcheck/repair_log.jsonl"""
import sys,os,re,json,glob,time,random,shutil,argparse,subprocess,collections,csv,itertools
import requests
HERE=os.path.dirname(os.path.abspath(__file__));R=os.path.abspath(os.path.join(HERE,"..","..",".."))
sys.path.insert(0,HERE)
import figcheck,pymupdf
API="https://integrate.api.nvidia.com/v1/chat/completions"
KEYS=[k.strip() for k in os.environ.get("NVIDIA_API_KEYS","").split(",") if k.strip()]
MODELS=os.environ.get("REPAIR_MODELS","nvidia/nemotron-3-ultra-550b-a55b,moonshotai/kimi-k3").split(",")
LOG=os.path.join(R,"docs/onbuch-generation/rapports/figcheck/repair_log.jsonl")
W={"texte/texte":4,"debordement":6,"marqueur":20,"hors-page":6,"trait/texte":1}
BLOC=re.compile(r"\\begin\{popfigure\}.*?\\end\{popfigure\}",re.S)
SYSTEM=r"""Tu es expert TikZ/pgfplots/LaTeX (XeLaTeX). On te donne une figure d'un cours scolaire (environnement popfigure) dont la mise en page est défectueuse : textes qui se chevauchent, traits qui traversent des textes, figure trop large ou trop dense.
Redessine-la PROPREMENT. Règles impératives :
- Conserve TOUS les textes, étiquettes, nombres, valeurs, la légende \legende{...} et le sens pédagogique/scientifique. N'invente rien, ne supprime aucune information (tu peux seulement changer les retours à la ligne et la disposition).
- Largeur totale <= 14 cm. Place les éléments avec la bibliothèque positioning (right=of, below=of, node distance >= 6 mm) ou un matrix ; chaque nœud a text width + align=center (ou left), minimum height, inner sep >= 3 pt. Aucun nœud ne doit en recouvrir un autre.
- Aucun trait ne doit traverser un texte : étiquettes de flèches avec [above] ou [midway, fill=white, inner sep=1pt] sur un segment assez long ; évite les flèches qui se croisent ; route avec |- ou -| ; si > 8 éléments, range-les en grille.
- Diagramme de séquence : une phase par tikzpicture, lignes de vie seulement entre les acteurs concernés, flèches horizontales, étiquette au-dessus de chaque flèche.
- pgfplots : légende hors de la courbe (legend pos=outer north east, ou legend style={at={(0.5,-0.25)},anchor=north,legend columns=2}), étiquettes avec node[pos=..., above], décimaux avec un point dans les coordonnées.
- Texte court dans les nœuds ; police \scriptsize ou \footnotesize si besoin ; échappe & % # _ dans le texte.
- Interdit : \includegraphics, fichiers externes, nouveaux packages, \usetikzlibrary non listées. Bibliothèques disponibles : arrows.meta, positioning, calc, shapes.geometric, shapes.misc, shapes.arrows, fit, backgrounds, matrix, decorations.pathreplacing, decorations.markings, patterns, intersections, angles, quotes ; pgfplots avec fillbetween et groupplots.
- Couleurs autorisées : popink, poporange, popdark, poppurple, popgreen, popblue, poppink, popgold, popmuted, popline, popblueL, popgreenL, poporangeL, poppinkL, popcream, black, white, gray.
Réponds UNIQUEMENT par un bloc ```latex contenant exactement un environnement \begin{popfigure} ... \end{popfigure}, sans explication."""
_ki=itertools.count(random.randrange(100))
def llm(messages,tries=6,max_tokens=12000):
    err=""
    for t in range(tries):
        k=KEYS[next(_ki)%len(KEYS)];m=MODELS[t%len(MODELS)]
        try:
            r=requests.post(API,headers={"Authorization":"Bearer "+k},json={"model":m,"messages":messages,"max_tokens":max_tokens,"temperature":0.3,"top_p":0.95,"stream":False},timeout=420)
            if r.status_code==200:
                c=r.json()["choices"][0]["message"].get("content") or ""
                if c.strip(): return c
            err=str(r.status_code)
        except Exception as e: err=type(e).__name__
        time.sleep(2+3*t)
    raise RuntimeError("LLM indisponible : "+err)
def extract(rep):
    m=re.search(r"```(?:latex|tex)?\s*(.*?)```",rep,re.S);s=m.group(1) if m else rep
    m=BLOC.search(s);return m.group(0) if m else None
def sev(sc): return "A" if sc>=12 else "B" if sc>=4 else "C"
def pscore(l): return sum(W.get(x["kind"],1) for x in l)
def compile_doc(d,text,tag,out):
    os.makedirs(out,exist_ok=True);f=os.path.join(d,"_fr_"+tag+".tex")
    open(f,"w",encoding="utf-8").write(text)
    try:
        r=subprocess.run(["xelatex","-interaction=nonstopmode","-halt-on-error","-output-directory",out,f],cwd=d,capture_output=True,text=True,timeout=600)
    except subprocess.TimeoutExpired: os.remove(f);return None,"timeout"
    finally:
        if os.path.exists(f): os.remove(f)
    pdf=os.path.join(out,"_fr_"+tag+".pdf")
    if r.returncode!=0 or not os.path.exists(pdf):
        m=re.search(r"^!.*?$",r.stdout,re.M);return None,(m.group(0) if m else "échec")
    return pdf,""
def measure(pdf):
    """-> liste par page (une figure par page) : (score, issues, mots)"""
    d=pymupdf.open(pdf);res=[]
    r=figcheck.analyse_pdf(pdf);pp=collections.defaultdict(list)
    for i in r["issues"]: pp[i["page"]].append(i)
    for n,p in enumerate(d,1):
        nf=len(figcheck.figure_rects(p))
        words={w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ0-9]{4,}",p.get_text())}
        res.append((pscore(pp[n]),pp[n],words,nf))
    return res
def desc(issues):
    out=[];seen=set()
    for i in issues:
        k=(i["kind"],i["text"])
        if k in seen: continue
        seen.add(k);out.append(f"- {i['kind']} : « {i['text']} »")
        if len(out)>=14: break
    return "\n".join(out)
def repair_course(tex,seuil,maxfigs,workdir):
    tex=os.path.abspath(tex);d=os.path.dirname(tex);name=os.path.basename(tex)[:-4];cl=d.split("/")[-2]
    top=os.path.relpath(tex,R).split("/")[0];tag=f"{top}__{cl}__{name}"[:90]
    out=os.path.join(workdir,tag);rec={"course":f"{top}|{cl}/{name}","figs":[],"status":"","t":time.strftime("%F %T")}
    src=open(tex,encoding="utf-8").read()
    if "\\begin{document}" not in src: rec["status"]="sans document";return rec
    pre=src.split("\\begin{document}")[0]
    blocs=[m.group(0) for m in BLOC.finditer(src)]
    idx=[i for i,b in enumerate(blocs) if "tikzpicture" in b or "axis" in b]
    if not idx: rec["status"]="aucune figure tikz";return rec
    doc=pre+"\\begin{document}\n"+"\n".join(blocs[i]+"\n\\clearpage" for i in idx)+"\n\\end{document}\n"
    pdf,err=compile_doc(d,doc,"all_"+tag,out)
    if not pdf: rec["status"]="mini-doc KO : "+err;return rec
    ms=measure(pdf);nf=sum(m[3] for m in ms)
    if nf!=len(idx): rec["status"]=f"correspondance figures/pages impossible ({nf}/{len(idx)})";return rec
    pages=[m for m in ms if m[3]]  # une figure par page
    # une figure par page
    todo=[(k,pages[j]) for j,k in enumerate(idx) if pages[j][0]>=seuil]
    todo.sort(key=lambda x:-x[1][0]);rec["avant"]={"figs":len(idx),"a_reparer":len(todo),"A":sum(1 for _,p in todo if sev(p[0])=="A"),"score":sum(p[0] for _,p in todo)}
    new=src;changed=0
    for k,(sc0,iss0,words0,_) in todo[:maxfigs]:
        old=blocs[k];best=None;msgs=[{"role":"system","content":SYSTEM},{"role":"user","content":"Figure à redessiner :\n```latex\n"+old+"\n```\nDéfauts mesurés sur le rendu :\n"+desc(iss0)}]
        for att in range(3):
            try: rep=llm(msgs)
            except Exception as e: rec["figs"].append({"i":k,"err":str(e)});break
            cand=extract(rep)
            if not cand: msgs+= [{"role":"assistant","content":rep},{"role":"user","content":"Réponds uniquement par un bloc ```latex avec un environnement popfigure complet."}];continue
            p1,e1=compile_doc(d,pre+"\\begin{document}\n"+cand+"\n\\end{document}\n","one_"+tag,out)
            if not p1:
                msgs+=[{"role":"assistant","content":rep},{"role":"user","content":"Ce code ne compile pas ("+e1+"). Corrige et renvoie la figure complète."}];continue
            m1=measure(p1)[0];kept=len(words0&m1[2])/max(1,len(words0))
            if kept<0.9:
                msgs+=[{"role":"assistant","content":rep},{"role":"user","content":f"Il manque du texte par rapport à l'original ({int(100*(1-kept))} % des mots). Conserve tout le texte et renvoie la figure complète."}];continue
            if best is None or m1[0]<best[0]: best=(m1[0],cand,kept)
            if m1[0]<=max(2,0.4*sc0): break
            msgs+=[{"role":"assistant","content":rep},{"role":"user","content":"Défauts restants sur le rendu :\n"+desc(m1[1])+"\nCorrige-les (espace les éléments, raccourcis les étiquettes, évite tout trait sur un texte) et renvoie la figure complète."}]
        if best and best[0]<=max(2,0.6*sc0):
            new=new.replace(old,best[1],1);changed+=1;rec["figs"].append({"i":k,"avant":sc0,"apres":best[0],"ok":True})
        else: rec["figs"].append({"i":k,"avant":sc0,"apres":best[0] if best else None,"ok":False})
    if changed:
        open(tex,"w",encoding="utf-8").write(new)
        for _ in range(2): fp,e=compile_doc(d,new,"full_"+tag,out)
        if not fp:
            open(tex,"w",encoding="utf-8").write(src);rec["status"]="compilation finale KO, original restauré : "+e;return rec
        dst=os.path.join(d,name+".pdf");shutil.copy(fp,dst)
        r=figcheck.analyse_pdf(dst);pp=collections.defaultdict(list)
        for i in r["issues"]: pp[i["page"]].append(i)
        rec["apres"]={"A":sum(1 for l in pp.values() if sev(pscore(l))=="A"),"B":sum(1 for l in pp.values() if sev(pscore(l))=="B"),"pages":r["pages"]}
    rec["status"]=f"terminé ({changed}/{len(todo)} figures réparées)";return rec
def worker(a):
    tex,seuil,maxfigs,workdir=a
    try: rec=repair_course(tex,seuil,maxfigs,workdir)
    except Exception as e: rec={"course":tex,"status":"erreur : "+str(e)[:200]}
    with open(LOG,"a") as f: f.write(json.dumps(rec,ensure_ascii=False)+"\n")
    print(rec["course"],rec["status"],flush=True);return rec
if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("tex",nargs="*");ap.add_argument("--jobs",type=int,default=4);ap.add_argument("--seuil",type=int,default=4)
    ap.add_argument("--max-figs",type=int,default=30);ap.add_argument("--out",default="/tmp/figrepair");ap.add_argument("--ordre-checklist",type=int,default=0)
    a=ap.parse_args()
    if not KEYS: sys.exit("NVIDIA_API_KEYS manquant")
    os.makedirs(os.path.dirname(LOG),exist_ok=True);done=set()
    if os.path.exists(LOG):
        for l in open(LOG):
            try:
                x=json.loads(l)
                if x["status"].startswith("terminé") or x["status"].startswith("aucune"): done.add(x["course"])
            except: pass
    lst=[os.path.abspath(t) for t in a.tex]
    if a.ordre_checklist:
        base=collections.Counter()
        for r in csv.DictReader(open(os.path.join(R,"docs/onbuch-generation/rapports/figcheck/pages_signalees.csv"))):
            if r["gravite"]=="A": base[r["pdf"][:-4]+".tex"]+=1
        lst=[os.path.join(R,p) for p,_ in base.most_common()][:a.ordre_checklist]
    def ckey(t):
        p=os.path.relpath(t,R).split("/");return p[0]+"|"+p[2]+"/"+p[-1][:-4]
    lst=[t for t in lst if ckey(t) not in done]
    print(len(lst),"cours à traiter",flush=True)
    from multiprocessing import Pool
    with Pool(a.jobs) as p:
        for _ in p.imap_unordered(worker,[(t,a.seuil,a.max_figs,a.out) for t in lst]): pass
