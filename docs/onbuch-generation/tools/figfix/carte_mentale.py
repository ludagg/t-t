#!/usr/bin/env python3
"""Remplace les cartes mentales TikZ (mindmap, grow cyclic) par une mise en page robuste en boîtes (tcbraster) :
centre en tête, une boîte colorée par branche, sous-éléments en liste. Texte conservé tel quel.
Usage : carte_mentale.py FICHIER.tex...  (réécrit en place ; idempotent ; consigne les cartes non reconnues)"""
import sys,re
OPEN=r"\begin{tikzpicture}[mindmap"
END=r"\end{tikzpicture}"
def grp(s,i,o="{",c="}"):
    """lit un groupe équilibré commençant à s[i]==o ; retourne (contenu, index après)"""
    assert s[i]==o;d=0;j=i
    while j<len(s):
        ch=s[j]
        if ch=="\\" : j+=2;continue
        if ch==o: d+=1
        elif ch==c:
            d-=1
            if d==0: return s[i+1:j],j+1
        j+=1
    raise ValueError("groupe non fermé")
def skipws(s,i):
    while i<len(s) and s[i] in " \t\r\n": i+=1
    return i
def parse_children(s):
    """s = suite de child[opts]{node[opts]{texte} child...} ; retourne liste de (couleur,texte,[enfants])"""
    out=[];i=skipws(s,0)
    while i<len(s):
        if s.startswith("child",i):
            i=skipws(s,i+5);opts=""
            if s[i]=="[": opts,i=grp(s,i,"[","]");i=skipws(s,i)
            body,i=grp(s,i);b=skipws(body,0)
            if not body[b:].startswith("node"): raise ValueError("child sans node")
            b=skipws(body,b+4);nopts=""
            if body[b]=="[": nopts,b=grp(body,b,"[","]");b=skipws(body,b)
            text,b=grp(body,b);rest=body[b:]
            col=re.search(r"concept color=([A-Za-z0-9!.]+)",opts+" "+nopts)
            out.append((col.group(1) if col else None,text.strip(),parse_children(rest)))
            i=skipws(s,i)
        elif s[i]==";" or s[i] in " \t\r\n": i+=1
        else: raise ValueError("contenu inattendu : "+s[i:i+30])
    return out
def clean(t): return re.sub(r"\s*\\\\\s*(\[[^\]]*\])?\s*"," ",t).strip()
def items(ch,depth=0):
    if not ch: return ""
    env="itemize"
    r="\\begin{itemize}[nosep,leftmargin=*,itemsep=1pt,font=\\scriptsize]\n" if depth==0 else "\\begin{itemize}[nosep,leftmargin=1.1em,itemsep=0pt]\n"
    for col,t,sub in ch:
        r+="\\item "+clean(t)+"\n"+items(sub,depth+1)
    return r+"\\end{itemize}\n"
def convert(block):
    m=re.match(r"\\begin\{tikzpicture\}\[mindmap.*?\]\s*(?=(?:\\path|\\node|\\draw))",block,re.S)
    # options de l'environnement : jusqu'au ']' qui ferme, en tenant compte des crochets imbriqués
    i=len(r"\begin{tikzpicture}");opts,i=grp(block,i,"[","]");body=block[i:-len(END)]
    body=re.sub(r"%[^\n]*","",body)
    j=body.index("\\node");j2=j+5;j2=skipws(body,j2);ropts=""
    if body[j2]=="[": ropts,j2=grp(body,j2,"[","]");j2=skipws(body,j2)
    root,j2=grp(body,j2);ch=parse_children(body[j2:])
    if not ch: raise ValueError("aucune branche")
    default=["popblue","poporange","popgreen","poppurple","popgold","poppink"]
    ncol=2 if len(ch)<=4 else 3
    rc=re.search(r"concept color=([A-Za-z0-9!.]+)",body[:j2]) or re.search(r"root concept[^\n]*concept color=([A-Za-z0-9!.]+)",opts)
    rcol=rc.group(1) if rc else "popink"
    out="\\begin{center}\\tcbox[colback="+rcol+",colframe="+rcol+",coltext=white,arc=9pt,boxsep=4pt,left=6pt,right=6pt]{\\bfseries\\small "+clean(root)+"}\\end{center}\n\\vspace{-2pt}\n"
    out+="\\begin{tcbraster}[raster columns="+str(ncol)+",raster equal height=rows,raster column skip=6pt,raster row skip=6pt,enhanced,blankest]\n"
    for k,(col,t,sub) in enumerate(ch):
        col=col or default[k%6]
        if not sub:
            out+="\\begin{tcolorbox}[enhanced,colback="+col+",colframe="+col+",coltext=white,boxrule=0.9pt,arc=3pt,left=4pt,right=4pt,top=3pt,bottom=3pt,boxsep=1pt,halign=left]\\bfseries\\scriptsize "+clean(t)+"\\end{tcolorbox}\n"
            continue
        out+="\\begin{tcolorbox}[enhanced,colback=white,colframe="+col+",boxrule=0.9pt,arc=3pt,title={"+clean(t)+"},fonttitle=\\bfseries\\scriptsize,coltitle=white,colbacktitle="+col+",left=4pt,right=4pt,top=2pt,bottom=2pt,boxsep=1pt,toptitle=1.5pt,bottomtitle=1.5pt,halign=left]\n"
        out+=items(sub)+"\\end{tcolorbox}\n"
    return out+"\\end{tcbraster}\n"
def process(path):
    s=open(path,encoding="utf-8").read();res=[];pos=0;n=0;bad=0
    while True:
        a=s.find(OPEN,pos)
        if a<0: break
        b=s.find(END,a)+len(END)
        try: res.append(s[pos:a]);res.append(convert(s[a:b]));n+=1
        except Exception as e:
            res[-1:]=[s[pos:a]] if len(res)%2==1 else res[-1:];res.append(s[a:b]);bad+=1;print("NON RECONNUE",path,str(e)[:60])
        pos=b
    res.append(s[pos:])
    if n: open(path,"w",encoding="utf-8").write("".join(res))
    return n,bad
if __name__=="__main__":
    tn=tb=0
    for p in sys.argv[1:]:
        n,b=process(p);tn+=n;tb+=b
    print("converties",tn,"non reconnues",tb)
