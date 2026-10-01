#!/bin/sh
# Recompile des PDF existants d'un dossier de cours avec tectonic.
# Usage : rebuild-all.sh <matiere-onbuch> <classe> [ID_DUCOURS...]
# tectonic relance les passes necessaires : les numeros de page "N / total"
# sortent corrects (contre "N / ??" avec une seule passe).
set -u
TECTONIC=/home/daytona/bin/tectonic
MAT="$1"
CLASSE="$2"
shift 2
cd "$MAT/cours/$CLASSE" || exit 1
for id in "$@"; do
  d=$(ls -d ${id}-* 2>/dev/null | head -1)
  [ -n "$d" ] || { echo "INTROUVABLE $id"; continue; }
  base=$(basename "$d")
  if [ ! -f "$d/$base.tex" ]; then echo "SANS TEX $d"; continue; fi
  out=$( (cd "$d" && "$TECTONIC" -c minimal "$base.tex" 2>&1) )
  if echo "$out" | grep -qi "^error"; then
    echo "ECHEC  $id"
    echo "$out" | grep -i "^error" | head -3
  else
    echo "OK     $id"
  fi
done
echo "=== TERMINE ==="