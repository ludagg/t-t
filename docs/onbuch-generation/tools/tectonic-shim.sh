#!/bin/bash
# shim : tectonic -> xelatex (secours quand tectonic est indisponible)
# TWO_PASS=1 : deuxième passe (numéros de page « N / total », sommaire) — à activer pour build.py
f="${@: -1}"
xelatex -interaction=nonstopmode -halt-on-error "$f" >/tmp/xelatex.$$.out 2>&1; rc=$?
[ $rc -eq 0 ] && [ -n "$TWO_PASS" ] && xelatex -interaction=nonstopmode -halt-on-error "$f" >/dev/null 2>&1
tail -c 3000 /tmp/xelatex.$$.out | iconv -c -f utf-8 -t utf-8 >&2; rm -f /tmp/xelatex.$$.out
exit $rc
