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

VIA_PAD, VIA_DRILL = 0.7, 0.4   # как в разводке, 10-mech.md §7
KEEP = 0.6                      # медь 0.35 плюс зазор 0.2 и запас
STEP, RINGS = 0.4, 6            # на сколько и как далеко искать место рядом
EDGE = 0.5                      # отступ меди от реза, 10-mech.md §7


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

    # Контур платы. Поиск места ходит кольцами до 2.4 мм от точки разрыва и
    # спокойно уходил за рез: пять заклёпок земли встали снаружи платы, в
    # отрицательных координатах. Проверяем не только «внутри», но и «не ближе
    # отступа»: пробуем восемь точек вокруг центра на расстоянии, которое
    # заклёпке нужно от реза. Так обходимся без `Deflate` — контур со
    # скруглениями он портит.
    edges = pcbnew.SHAPE_POLY_SET()
    board.GetBoardPolygonOutlines(edges)
    ring = VIA_PAD / 2 + EDGE

    def in_board(x, y):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                       (0.7071, 0.7071), (0.7071, -0.7071),
                       (-0.7071, 0.7071), (-0.7071, -0.7071)):
            p = pcbnew.VECTOR2I(mm(x + dx * ring), mm(y + dy * ring))
            if not edges.Contains(p):
                return False
        return True

    def free(x, y):
        if not in_board(x, y):
            return False
        if any(x1 - KEEP < x < x2 + KEEP and y1 - KEEP < y < y2 + KEEP
               for x1, y1, x2, y2 in busy):
            return False
        return not any((x - vx) ** 2 + (y - vy) ** 2 < 1.2 ** 2
                       for vx, vy in have)

    added = skipped = 0
    for x0, y0 in drc_points():
        # Ищем место не только в самой точке разрыва: DRC называет середину
        # куска меди, а она нередко стоит впритык к чужой площадке. Обходим
        # кольцами вокруг — заклёпке всё равно, где стоять, лишь бы попасть в
        # тот же кусок.
        spot = None
        for r in range(RINGS + 1):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r:
                        continue
                    x, y = x0 + dx * STEP, y0 + dy * STEP
                    if free(x, y):
                        spot = (x, y)
                        break
                if spot:
                    break
            if spot:
                break
        if spot is None:
            skipped += 1
            continue
        x, y = spot
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
        v.SetWidth(mm(VIA_PAD))
        v.SetDrill(mm(VIA_DRILL))
        v.SetNet(gnd)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        # Метка «это сшивка земли» — по ней `pcb06_planes.py` снимает прежнее
        # перед новой заливкой. Без метки добивка копилась: заливка
        # перекладывается, разрывы теперь в других местах, а заклёпки прошлого
        # захода остаются стоять там, где разрыва больше нет.
        v.SetLocked(True)
        board.Add(v)
        have.append((x, y))
        added += 1

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(str(BOARD))
    print(f"добито переходных по земле: {added}, пропущено (занято): {skipped}")


if __name__ == "__main__":
    main()
