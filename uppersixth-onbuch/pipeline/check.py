#!/usr/bin/env python3
"""Outil de correction manuelle.

  python3 pipeline/check.py            liste les blocs à corriger (*.err)
  python3 pipeline/check.py M04/s03    recompile build/M04/s03.rev.tex ; si OK,
                                       le valide (s03.ok.tex) et supprime le .err,
                                       sinon affiche l'erreur et le contexte.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import generate as g  # noqa: E402


def check(ref):
    lid, name = ref.split("/")
    d = g.BUILD / lid
    body = g.autofix((d / f"{name}.rev.tex").read_text())
    (d / f"{name}.rev.tex").write_text(body)
    ok, err, line = g.compile_check(body)
    if ok:
        (d / f"{name}.ok.tex").write_text(body)
        (d / f"{name}.err").unlink(missing_ok=True)
        print(f"✔ {ref} validé")
        return True
    (d / f"{name}.err").write_text(f"ligne {line}\n{err}")
    print(f"✗ {ref} (ligne {line})\n{err[:800]}")
    if line:
        lines = body.splitlines()
        for i in range(max(0, line - 4), min(len(lines), line + 3)):
            print(f"{i+1:5d}| {lines[i][:200]}")
    return False


def finalize():
    """Marque DONE les leçons dont tous les blocs sont validés (y compris
    celles dont un bloc a été corrigé à la main après le passage du générateur)."""
    import json
    cat = g.load_catalog()
    for L in cat["lessons"]:
        d = g.BUILD / L["id"]
        if (d / "DONE").exists() or not (d / "plan.json").exists():
            continue
        n = len(json.loads((d / "plan.json").read_text())["sections"])
        need = [f"s{i+1:02d}" for i in range(n)] + ["activite", "methodes", "exercices", "corriges", "bilan"] + \
               [f"extra_{e['id']}" for e in L.get("extra", [])]
        if all((d / f"{k}.ok.tex").exists() for k in need):
            (d / "DONE").write_text("ok")
            print(f"✔ {L['id']} complète (finalisée)")


def llm_repair(ref):
    """Réparation déterministe (règles autofix, puis suppression de la ligne/du bloc fautif) : aucun appel au modèle."""
    lid, name = ref.split("/")
    d = g.BUILD / lid
    ok = False
    cands = [f for f in (f"{name}.rev.tex", f"{name}.tex") if (d / f).exists() and not g.degenerate((d / f).read_text())]
    for drop_ in (False, True):          # d'abord sans rien supprimer (relecture puis brut), ensuite avec suppression
        for src in cands:
            body = g.autofix((d / src).read_text())
            ok, _, _ = g.compile_check(body)
            if not ok and drop_ and os.environ.get('DROP_FAULT') == '1':
                body, ok = g.drop_fault(body)
            if ok:
                break
        if ok:
            break
    if ok and not g.degenerate(body):
        (d / f"{name}.rev.tex").write_text(body)
        (d / f"{name}.ok.tex").write_text(body)
        (d / f"{name}.err").unlink(missing_ok=True)
        print(f"✔ {ref} corrigé")
    else:
        print(f"✗ {ref} : correction impossible")


def drop(ref):
    lid, name = ref.split("/")
    d = g.BUILD / lid
    body = g.autofix((d / f"{name}.rev.tex").read_text())
    body2, ok = g.drop_fault(body)
    if ok:
        (d / f"{name}.rev.tex").write_text(body2)
        (d / f"{name}.ok.tex").write_text(body2)
        (d / f"{name}.err").unlink(missing_ok=True)
        print(f"✂ {ref} : bloc fautif supprimé, validé")
    else:
        print(f"✗ {ref} : suppression impossible")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--llm"]:
        for r in sys.argv[2:]:
            llm_repair(r)
        sys.exit()
    if sys.argv[1:2] == ["--drop"]:
        for r in sys.argv[2:]:
            drop(r)
        sys.exit()
    if sys.argv[1:] == ["--finalize"]:
        finalize()
        sys.exit()
    if len(sys.argv) == 1:
        for e in sorted(g.BUILD.glob("*/*.err")):
            first = e.read_text().splitlines()
            print(f"{e.parent.name}/{e.stem}: {first[0]} {first[1] if len(first) > 1 else ''}")
    else:
        for r in sys.argv[1:]:
            check(r)
