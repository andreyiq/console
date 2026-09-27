#!/usr/bin/env python3
"""Плата глазами домашнего производства: ЛУТ, сверловка, пайка феном.

Числа, по которым выбирают между вариантами разводки, кроме «разведено ли»:

* **сверловка** — сколько отверстий и сколько разных свёрл. Каждое отверстие —
  минута у станка, каждое новое сверло — смена и новый ноль;
* **заклёпки** — отверстие, в которое паяется проволочка с двух сторон; под
  корпусом (термопады) — ставятся до посадки детали;
* **тесные места** — сколько пар «медь—чужая медь» ближе 0.25 и 0.3 мм. ЛУТ
  держит 0.2 (10-mech.md §5), но именно там тонер сливается или подтравливает,
  и брак на плате случается в этих местах. Считает сам DRC KiCad с поднятым
  зазором на копии проекта — не свой геометрический код;
* **поясок** — медь вокруг отверстия. При ручной сверловке сверло уходит, и
  тонкий поясок срывается первым;
* **соседи для паяльника** — детали, у которых до ближайшей соседней меньше
  1 мм между площадками разных деталей: туда не влезает жало.

Запуск: `python3 tools/pcb_fab.py` — только читает плату.
"""
import collections
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"
TIGHT = (0.25, 0.3)
IRON = 1.0


def tight_places(gap):
    """Нарушения зазора при общем правиле `gap` — по DRC на копии проекта.

    Исключение для корпуса F133 (0.15, `console.kicad_dru`) оставляем как
    есть: у его площадок теснота — свойство корпуса, а не разводки.
    """
    tmp = Path(tempfile.mkdtemp())
    for f in ("console.kicad_pcb", "console.kicad_pro", "console.kicad_dru"):
        shutil.copy(ROOT / f, tmp / f)
    pro = json.loads((tmp / "console.kicad_pro").read_text())
    rules = pro["board"]["design_settings"]["rules"]
    rules["min_clearance"] = gap
    # класс цепей Default задаёт свой зазор — поднимаем и его
    for nc in pro.get("net_settings", {}).get("classes", []):
        nc["clearance"] = max(nc.get("clearance", 0), gap)
    (tmp / "console.kicad_pro").write_text(json.dumps(pro, indent=2))
    out = tmp / "drc.json"
    subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "-o",
                    str(out), str(tmp / "console.kicad_pcb")],
                   capture_output=True, text=True)
    d = json.loads(out.read_text())
    return sum(1 for v in d["violations"] if v["type"] == "clearance")


def main():
    b = pcbnew.LoadBoard(str(BOARD))
    drills = collections.Counter()
    rivets = collections.Counter()
    ring_min = None
    for t in b.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            d = round(pcbnew.ToMM(t.GetDrill()), 2)
            drills[d] += 1
            rivets["земля" if t.GetNetname() == "GND" else "сигнал/питание"] += 1
            ring = (pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) - d) / 2
            ring_min = ring if ring_min is None else min(ring_min, ring)
    npth = 0
    for f in b.GetFootprints():
        for p in f.Pads():
            if p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                d = round(pcbnew.ToMM(p.GetDrillSize().x), 2)
                drills[d] += 1
                if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                    npth += 1

    # соседи для паяльника: минимальный просвет между площадками разных деталей
    pads = [(f.GetReference(), p.GetBoundingBox())
            for f in b.GetFootprints() for p in f.Pads()
            if p.IsOnLayer(pcbnew.F_Cu) and p.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH]
    crowd = collections.Counter()
    for i, (ra, a) in enumerate(pads):
        for rb, bb in pads[i + 1:]:
            if ra == rb:
                continue
            dx = max(0, max(a.GetLeft(), bb.GetLeft()) - min(a.GetRight(), bb.GetRight()))
            dy = max(0, max(a.GetTop(), bb.GetTop()) - min(a.GetBottom(), bb.GetBottom()))
            if math.hypot(dx, dy) < pcbnew.FromMM(IRON):
                crowd[tuple(sorted((ra, rb)))] += 1

    values = collections.Counter()
    for f in b.GetFootprints():
        if f.GetReference()[0] in "RC" and f.GetReference()[1:].isdigit():
            values[(f.GetReference()[0], f.GetValue())] += 1

    print(f"сверловка: {sum(drills.values())} отверстий, свёрл {len(drills)} — "
          + ", ".join(f"⌀{d} ×{n}" for d, n in sorted(drills.items())))
    print(f"заклёпок: {sum(rivets.values())} ("
          + ", ".join(f"{k} {v}" for k, v in rivets.items()) + ")"
          + f"; поясок не меньше {ring_min:.2f} мм")
    for g in TIGHT:
        print(f"мест теснее {g} мм: {tight_places(g)}")
    print(f"пар деталей ближе {IRON} мм между площадками: {len(crowd)}")
    print(f"номиналов R/C: {len(values)} на {sum(values.values())} деталей")


if __name__ == "__main__":
    main()
