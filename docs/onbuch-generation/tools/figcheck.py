#!/usr/bin/env python3
"""Détecteur de défauts visuels dans les figures (TikZ/pgfplots) des PDF de cours OnBuch+.
Lit les vecteurs et le texte des PDF (pymupdf), sans modèle ni compilation.
Défauts : texte recouvert par un autre texte, trait qui traverse un texte, texte hors page.
Usage : figcheck.py [--out DIR] [--jobs N] [PDF|DOSSIER ...]   (défaut : tous les cours du dépôt)
Sortie : DIR/results.jsonl (un objet par PDF) puis DIR/summary.md"""
import sys,os,json,glob,itertools,argparse,math
from multiprocessing import Pool
import pymupdf
pymupdf.TOOLS.mupdf_display_errors(False)

def bez(p0,p1,p2,p3,n=6):
    for k in range(1,n):
        t=k/n;u=1-t
        yield (u**3*p0.x+3*u*u*t*p1.x+3*u*t*t*p2.x+t**3*p3.x, u**3*p0.y+3*u*u*t*p1.y+3*u*t*t*p2.y+t**3*p3.y)

def segments(p):
    segs=[];boxes=[]
    for dr in p.get_drawings():
        stroked=dr.get("color") is not None
        pts=[]
        for it in dr["items"]:
            if it[0]=="l": segs.append((it[1].x,it[1].y,it[2].x,it[2].y))
            elif it[0]=="c":
                prev=(it[1].x,it[1].y)
                for q in bez(it[1],it[2],it[3],it[4]):
                    segs.append((prev[0],prev[1],q[0],q[1]));prev=q
                segs.append((prev[0],prev[1],it[4].x,it[4].y))
            elif it[0]=="re" and stroked:
                r=it[1]; segs += [(r.x0,r.y0,r.x1,r.y0),(r.x1,r.y0,r.x1,r.y1),(r.x1,r.y1,r.x0,r.y1),(r.x0,r.y1,r.x0,r.y0)]
            elif it[0]=="qu" and stroked:
                q=it[1];c=[q.ul,q.ur,q.lr,q.ll]
                for a,b in zip(c,c[1:]+c[:1]): segs.append((a.x,a.y,b.x,b.y))
        boxes.append(dr["rect"])
    return segs,boxes

def seg_hits(rect,s):
    """le segment traverse-t-il le rectangle (Liang-Barsky) sur une longueur >= 2 pt ?"""
    x0,y0,x1,y1=s;dx=x1-x0;dy=y1-y0;t0,t1=0.0,1.0
    for p,q in ((-dx,x0-rect.x0),(dx,rect.x1-x0),(-dy,y0-rect.y0),(dy,rect.y1-y0)):
        if p==0:
            if q<0: return False
        else:
            r=q/p
            if p<0:
                if r>t1: return False
                t0=max(t0,r)
            else:
                if r<t0: return False
                t1=min(t1,r)
    return (t1-t0)*math.hypot(dx,dy)>=2.0

POPLINE=(0.91765,0.87451,0.82353)
def figure_rects(p):
    """cadres \\popfigure : forme pleine couleur popline (#EADFD2), sans trait, large"""
    out=[]
    for dr in p.get_drawings():
        f=dr.get("fill");r=dr["rect"]
        if f and dr.get("color") is None and all(abs(a-b)<0.01 for a,b in zip(f,POPLINE)) and r.width>150 and r.height>50:
            out.append(pymupdf.Rect(r.x0+2,r.y0+2,r.x1-2,r.y1-2))
    return out

def analyse_page(p):
    if p.number==0: return []
    figs=figure_rects(p)
    if not figs: return []
    segs,boxes=segments(p)
    def infig(r): 
        c=pymupdf.Point((r.x0+r.x1)/2,(r.y0+r.y1)/2)
        return any(f.contains(c) for f in figs)
    segs=[s_ for s_ in segs if any(f.contains(pymupdf.Point((s_[0]+s_[2])/2,(s_[1]+s_[3])/2)) for f in figs)]
    if len(segs)<3: return []
    sp=[]
    for b in p.get_text("dict")["blocks"]:
        for l in b.get("lines",[]):
            for s in l["spans"]:
                t=s["text"].strip()
                r=pymupdf.Rect(s["bbox"])
                if t and not (len(t)<=1 and not t.isalnum()) and 62<r.y0 and r.y1<p.rect.y1-72 and infig(r): sp.append((r,t,s["size"]))
    # zone des figures : texte proche d'un dessin non rectiligne ou de >=6 segments
    res=[]
    # texte / texte
    sp.sort(key=lambda a:a[0].x0)
    for i,(r1,t1,s1) in enumerate(sp):
        for r2,t2,s2 in sp[i+1:]:
            if r2.x0>=r1.x1: break
            w=min(r1.x1,r2.x1)-max(r1.x0,r2.x0);h=min(r1.y1,r2.y1)-max(r1.y0,r2.y0)
            if w>2 and h>0.35*min(r1.height,r2.height): res.append(("texte/texte",t1[:24]+" ↔ "+t2[:24],tuple(round(v) for v in (r1|r2))))
    # trait / texte : un trait traverse nettement le texte (bande centrale, marges de 1,5 pt)
    for r,t,sz in sp:
        band=pymupdf.Rect(r.x0+1.5,r.y0+r.height*0.3,r.x1-1.5,r.y1-r.height*0.3)
        if band.is_empty or band.width<3: continue
        for s_ in segs:
            if max(s_[0],s_[2])<band.x0 or min(s_[0],s_[2])>band.x1 or max(s_[1],s_[3])<band.y0 or min(s_[1],s_[3])>band.y1: continue
            if not seg_hits(band,s_): continue
            dx=abs(s_[2]-s_[0]);dy=abs(s_[3]-s_[1])
            if dy>dx and dx<1.0 and dy<band.height*0.5: continue      # court trait vertical : bord de cellule/curseur
            if dx>=dy and dy<0.5 and dx<min(6,band.width*0.4): continue  # petit trait horizontal : fraction, soulignement
            res.append(("trait/texte",t[:30],tuple(round(v) for v in r)));break
    for r,t,sz in sp:
        if r.x1>p.rect.x1+4 or r.x0<-4: res.append(("hors-page",t[:30],tuple(round(v) for v in r)))
    return res

def analyse_pdf(path):
    out={"pdf":path,"pages":0,"issues":[],"error":None}
    try:
        d=pymupdf.open(path);out["pages"]=len(d)
        for i,p in enumerate(d):
            for kind,txt,bb in analyse_page(p):
                out["issues"].append({"page":i+1,"kind":kind,"text":txt,"bbox":bb})
    except Exception as e: out["error"]=str(e)[:200]
    return out

def find_pdfs(args):
    if not args: args=[os.path.join(os.path.dirname(__file__),"..","..","..")]
    res=[]
    for a in args:
        if os.path.isfile(a): res.append(a)
        else:
            for f in glob.glob(os.path.join(a,"**","cours","**","*.pdf"),recursive=True): res.append(os.path.abspath(f))
    return sorted(set(res))

if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("paths",nargs="*");ap.add_argument("--out",default="figcheck-report");ap.add_argument("--jobs",type=int,default=4)
    a=ap.parse_args();pdfs=find_pdfs(a.paths);os.makedirs(a.out,exist_ok=True)
    done=set()
    rp=os.path.join(a.out,"results.jsonl")
    if os.path.exists(rp):
        for l in open(rp):
            try: done.add(json.loads(l)["pdf"])
            except: pass
    todo=[p for p in pdfs if p not in done]
    print(len(pdfs),"PDF,",len(todo),"à analyser",flush=True)
    with open(rp,"a") as f, Pool(a.jobs) as pool:
        for n,r in enumerate(pool.imap_unordered(analyse_pdf,todo,chunksize=1),1):
            f.write(json.dumps(r,ensure_ascii=False)+"\n");f.flush()
            if n%25==0: print(n,"/",len(todo),flush=True)
