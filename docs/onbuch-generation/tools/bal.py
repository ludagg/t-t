import re,sys
t=open(sys.argv[1]).read()
t=re.sub(r'(?<!\\)%.*','',t)
depth=0
for i,l in enumerate(t.split('\n'),1):
    l2=l.replace('\\{','').replace('\\}','')
    d=l2.count('{')-l2.count('}')
    depth+=d
    if d: print(i,d,depth,l[:150]+' ... '+l[-110:])
