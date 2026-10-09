#!/usr/bin/env python3
"""Insère (ou remplace) le bloc FIGFIX dans tous les cours .tex (avant \\begin{document}) et dans chaque preamble.tex (en fin de fichier).
Idempotent. Usage : apply_figfix.py BLOC.tex [RACINE_DEPOT]"""
import sys,re,glob,os
bloc=open(sys.argv[1],encoding="utf-8").read();root=sys.argv[2] if len(sys.argv)>2 else "."
pat=re.compile(r"%% FIGFIX-BEGIN.*?%% FIGFIX-END\n",re.S)
n=0;skip=0
for f in glob.glob(os.path.join(root,"*","cours","**","*.tex"),recursive=True)+glob.glob(os.path.join(root,"*","preamble.tex")):
    s=open(f,encoding="utf-8").read()
    if "\\begin{document}" in s and "cours" in f: 
        s2=pat.sub("",s);s2=s2.replace("\\begin{document}",bloc+"\\begin{document}",1)
    elif f.endswith("preamble.tex"):
        s2=pat.sub("",s).rstrip("\n")+"\n"+bloc
    else: skip+=1;continue
    if s2!=s: open(f,"w",encoding="utf-8").write(s2);n+=1
print("modifiés",n,"ignorés",skip)
