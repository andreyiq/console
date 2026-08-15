#!/usr/bin/env python3
"""Уложить на плату медь из `.ses` — ответа freerouting.

Своё старое кладём заново каждый раз: скрипт сначала снимает с платы все
дорожки и все переходные, кроме сшивочных по земле (их ставит
`pcb06_planes.py`, и они к трассировке отношения не имеют), а потом кладёт то,
что пришло. Так его можно гонять сколько угодно раз подряд, и результат
зависит только от входного `.ses`.

Единицы в `.ses` — микрометры, ось Y направлена вверх, в KiCad вниз; отсюда
смена знака. Проверено на размещении: `U1` стоит на плате в (148.0, 75.0) мм,
в `.dsn` записан как (148000, −75000).

Ширина дорожки берётся из самого `.ses`: freerouting возвращает ту, которую мы
задали ему в правилах, и спорить с ней тут не о чем.

Запускать: `python3 pcb08_ses.py <файл.ses>`, потом `pcb09_gnd.py` — добить
землю там, где дорожки отрезали куски передней заливки.
"""
import re
import sys
from pathlib import Path

import pcbnew

import dsn

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"

LAYER = {"F.Cu": pcbnew.F_Cu, "B.Cu": pcbnew.B_Cu}
VIA_PAD, VIA_DRILL = 0.9, 0.5


def mm(v):
    return pcbnew.FromMM(v)


def xy(x, y):
    """Точка `.ses` (микрометры, Y вверх) в координатах платы."""
    return pcbnew.VECTOR2I(mm(x / 1000.0), mm(-y / 1000.0))


def via_size(padstack):
    """Медь и сверло переходной из имени колодки, вида `Via[0-1]_800:400_um`.

    Размер задаём мы сами в правилах, но верить лучше тому, что вернулось:
    сверло 0,5 — это предел нашего набора (10-mech.md §7), и молча положить
    вместо него что-то мельче нельзя.
    """
    m = re.search(r"_(\d+):(\d+)_um", padstack or "")
    if not m:
        return VIA_PAD, VIA_DRILL
    return int(m.group(1)) / 1000.0, int(m.group(2)) / 1000.0


def clear(board):
    """Снять прежнюю трассировку, оставив сшивку по земле."""
    gnd = board.FindNet("GND").GetNetCode()
    doomed = []
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA) and t.GetNetCode() == gnd:
            continue
        doomed.append(t)
    for t in doomed:
        board.RemoveNative(t)
    return len(doomed)


def main():
    if len(sys.argv) != 2:
        sys.exit("нужен путь к .ses")
    items = dsn.parse_ses(Path(sys.argv[1]).read_text())

    board = pcbnew.LoadBoard(str(BOARD))
    nets = {n.GetNetname(): n for n in board.GetNetsByName().values()}
    dropped = clear(board)

    wires = vias = 0
    unknown = set()
    for kind, layer, width, pts, net in items:
        n = nets.get(net)
        if n is None:
            unknown.add(net)
            continue
        if kind == "via":
            pad, drill = via_size(layer)
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(xy(*pts[0]))
            v.SetWidth(mm(pad))
            v.SetDrill(mm(drill))
            v.SetNet(n)
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            board.Add(v)
            vias += 1
            continue
        if layer not in LAYER:
            unknown.add(layer)
            continue
        for a, b in zip(pts, pts[1:]):
            if a == b:
                continue
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(xy(*a))
            t.SetEnd(xy(*b))
            t.SetWidth(mm(width / 1000.0))
            t.SetLayer(LAYER[layer])
            t.SetNet(n)
            board.Add(t)
            wires += 1

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(str(BOARD))
    print(f"снято прежней меди: {dropped}; положено отрезков: {wires}, "
          f"переходных: {vias}")
    if unknown:
        print("не понял (цепь или слой):", ", ".join(sorted(unknown)[:10]))


if __name__ == "__main__":
    main()
