#!/usr/bin/env python3
"""Avancement de la génération des cours OnBuch+.

  python3 docs/onbuch-generation/status.py              # tous les dossiers *-onbuch
  python3 docs/onbuch-generation/status.py espagnol1    # filtre sur un nom

Pour chaque leçon : blocs validés (*.ok.tex) / blocs attendus, DONE, erreurs (*.err), PDF.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLOCS_FIXES = ["activite", "methodes", "exercices", "corriges", "bilan"]


def main():
    filtre = sys.argv[1] if len(sys.argv) > 1 else ""
    for d in sorted(ROOT.glob("*-onbuch")):
        if filtre not in d.name or not (d / "pipeline" / "lessons.json").exists():
            continue
        cat = json.loads((d / "pipeline" / "lessons.json").read_text())
        pdfs = {p.parent.name for p in (d / "cours").glob("*/*/*.pdf")}
        termine = pdf = 0
        lignes = []
        for L in cat["lessons"]:
            b = d / "build" / L["id"]
            nom = f"{L['id']}-{L['slug']}"
            plan = b / "plan.json"
            if plan.exists():
                n = len(json.loads(plan.read_text())["sections"])
                attendu = n + len(BLOCS_FIXES) + len(L.get("extra", []))
            else:
                attendu = None
            ok = len(list(b.glob("*.ok.tex"))) if b.exists() else 0
            errs = [e.stem for e in b.glob("*.err")] if b.exists() else []
            done = (b / "DONE").exists()
            has_pdf = nom in pdfs
            termine += done or has_pdf
            pdf += has_pdf
            etat = "PDF ✔" if has_pdf else ("DONE (à compiler)" if done else
                   (f"{ok}/{attendu} blocs" if attendu else "pas commencé"))
            if errs:
                etat += "  ⚠ à corriger : " + ", ".join(errs)
            lignes.append(f"  {L['id']:5s} {etat}")
        print(f"\n{d.name} — {cat['matiere']} {cat['classe']} {cat['series']} : "
              f"{termine}/{len(cat['lessons'])} leçons terminées, {pdf} PDF")
        if filtre or d.name.startswith("espagnol"):
            print("\n".join(lignes))
        else:
            reste = [l for l in lignes if "PDF ✔" not in l]
            print("\n".join(reste) if reste else "  (tout est compilé)")


if __name__ == "__main__":
    main()
