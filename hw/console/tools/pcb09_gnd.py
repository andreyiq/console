#!/usr/bin/env python3
"""Добивка земли: переходная там, где заливка на лице осталась островом.

Изнанка — сплошная плоскость `GND`, лицо — заливка того же `GND`, между ними
сшивка с шагом 8 мм (`pcb06_planes.py`). Но после трассировки дорожки режут
переднюю заливку, и часть её кусков остаётся отрезанной от остальных: медь
есть, а связи с плоскостью нет. DRC честно называет такие места
неподключёнными.

Скрипт спрашивает у DRC, где именно это случилось, и ставит туда переходную.
Ставит только там, где просят, а не по всей плате: каждая переходная у нас —
это заклёпка из проволоки, спаянная руками с двух сторон (10-mech.md §7).

Запускать после `pcb08_ses.py`, можно несколько раз подряд — каждый прогон
добивает то, что осталось.
"""
import re
import subprocess
import tempfile
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"

VIA_PAD, VIA_DRILL = 0.9, 0.5
KEEP = 0.75                     # на сколько отходить от чужой меди


def mm(v):
    return pcbnew.FromMM(v)


def drc_points():
    """Координаты неподключённых элементов цепи GND, из отчёта DRC."""
    with tempfile.TemporaryDirectory() as d:
        rpt = Path(d) / "drc.rpt"
        subprocess.run(["kicad-cli", "pcb", "drc", "--severity-error",
                        "--severity-warning", "-o", str(rpt), str(BOARD)],
                       capture_output=True)
        text = rpt.read_text()
    out = []
    for blk in re.split(r"\n(?=\[)", text):
        if not blk.startswith("[unconnected_items]"):
            continue
        if "[GND]" not in blk:
            continue
        for x, y in re.findall(r"@\(([\d,\.]+) mm, ([\d,\.]+) mm\)", blk):
            out.append((float(x.replace(",", ".")), float(y.replace(",", "."))))
    return out


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    gnd = board.FindNet("GND")

    busy = []
    for f in board.GetFootprints():
        for p in f.Pads():
            if p.GetNetCode() == gnd.GetNetCode():
                continue
            bb = p.GetBoundingBox()
            busy.append((pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()),
                         pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom())))
    have = []
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            pos = t.GetPosition()
            have.append((pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y)))

    added = skipped = 0
    for x, y in drc_points():
        if any(x1 - KEEP < x < x2 + KEEP and y1 - KEEP < y < y2 + KEEP
               for x1, y1, x2, y2 in busy):
            skipped += 1
            continue
        if any((x - vx) ** 2 + (y - vy) ** 2 < 1.2 ** 2 for vx, vy in have):
            skipped += 1
            continue
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
        v.SetWidth(mm(VIA_PAD))
        v.SetDrill(mm(VIA_DRILL))
        v.SetNet(gnd)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        board.Add(v)
        have.append((x, y))
        added += 1

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(str(BOARD))
    print(f"добито переходных по земле: {added}, пропущено (занято): {skipped}")


if __name__ == "__main__":
    main()
