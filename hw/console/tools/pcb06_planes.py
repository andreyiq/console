#!/usr/bin/env python3
"""Земля: сплошной полигон на изнанке, заливка на лице, сшивка переходными.

Расклад сложился так, что половина разводки делается не дорожками. Все 155
деталей стоят на лице (10-mech.md §4.1), изнанка пустая — значит `B.Cu` можно
отдать под **сплошную землю** целиком. Из этого следует всё остальное:

* у каждого сигнала на лице появляется нормальная опорная плоскость под ним —
  для шины дисплея и пары USB это важнее любой длины дорожки;
* **термопад F133 перестаёт быть проблемой.** Он единственная земля чипа
  (08-decoupling.md §2.5) и заперт кольцом из 128 площадок с зазором 0.17 —
  наружу по меди не выйти. Переходные под корпусом упираются прямо в плоскость;
* при домашнем травлении сплошная изнанка это ещё и минимум работы: почти
  нечего вытравливать;
* земля к десяти кнопкам, которую `01-buttons.md §7.6` требовал вести отдельной
  петлёй по периметру, приходит к ним снизу — петля не нужна.

Заливка на лице (тоже `GND`) собирает землю у самих выводов, чтобы не гонять
каждый вывод через переходную. Сшивка связывает две плоскости.

Скрипт идемпотентный: свои зоны и переходные он узнаёт по цепи и слою и кладёт
заново. Запускать после размещения.
"""
import math
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"

OX, OY = 50.0, 40.0
BOARD_W, BOARD_H = 156.0, 74.0
EDGE = 0.5                      # отступ меди от реза, как в правилах платы
CORNER_R = 3.0

VIA_PAD, VIA_DRILL = 0.9, 0.5   # 10-mech.md §7, минимум для CNC3018

# Сшивка: шаг сетки по свободной земле. 8 мм — это λ/4 на 4 ГГц, для наших
# частот с огромным запасом, а заклёпок вручную получается терпимое число.
STITCH_STEP = 8.0

# Термопад F133: девять переходных внутри 5.72 x 5.72. У Xassette их шесть в
# пределах ±4 мм от центра (08-decoupling.md §4), берём чуть плотнее — это
# единственная земля чипа и единственный его теплоотвод.
EPAD_GRID = [(-1.6, -1.6), (0.0, -1.6), (1.6, -1.6),
             (-1.6, 0.0), (0.0, 0.0), (1.6, 0.0),
             (-1.6, 1.6), (0.0, 1.6), (1.6, 1.6)]


def mm(v):
    return pcbnew.FromMM(v)


def pt(x, y):
    return pcbnew.VECTOR2I(mm(OX + x), mm(OY + y))


def board_outline(inset):
    """Контур платы, ужатый внутрь на inset — скругления считаем дугой."""
    w, h, r = BOARD_W, BOARD_H, CORNER_R
    pts, steps = [], 8
    for cx, cy, a0 in ((r, r, 180), (w - r, r, 270), (w - r, h - r, 0), (r, h - r, 90)):
        for i in range(steps + 1):
            a = math.radians(a0 + 90 * i / steps)
            pts.append((cx + (r - inset) * math.cos(a), cy + (r - inset) * math.sin(a)))
    return pts


def wipe(board):
    """Убрать свои зоны и сшивку — всё, что цепь GND и не имеет соседей."""
    gnd = board.FindNet("GND")
    for z in list(board.Zones()):
        if z.GetNetname() == "GND":
            board.Remove(z)
    for t in list(board.GetTracks()):
        if isinstance(t, pcbnew.PCB_VIA) and t.GetNetname() == "GND":
            board.Remove(t)
    return gnd


def plane(board, layer, net, inset):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNet(net)
    z.SetIsFilled(False)
    z.SetLocalClearance(mm(0.25))
    z.SetMinThickness(mm(0.15))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)   # без термобарьеров: паяем феном
    poly = z.Outline()
    poly.NewOutline()
    for x, y in board_outline(inset):
        poly.Append(mm(OX + x), mm(OY + y))
    board.Add(z)
    return z


def via(board, net, x, y):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pt(x, y))
    v.SetWidth(mm(VIA_PAD))
    v.SetDrill(mm(VIA_DRILL))
    v.SetNet(net)
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    board.Add(v)
    return v


def occupied(board):
    """Прямоугольники, куда переходную ставить нельзя.

    Детали с их площадками — и чужие дорожки, если разводка уже лежит. Порядок
    работ именно такой: сначала дорожки, потом заклёпки по оставшемуся полю.
    Обратный порядок мы попробовали и он плох — сотня заклёпок по сетке 8 мм
    превращается для трассировщика в лес столбов, и он разводит заметно хуже.
    """
    boxes = []
    for f in board.GetFootprints():
        f.BuildCourtyardCaches()
        for lay in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
            cy = f.GetCourtyard(lay)
            if cy.OutlineCount():
                bb = cy.BBox()
                boxes.append((pcbnew.ToMM(bb.GetLeft()) - OX - 0.6,
                              pcbnew.ToMM(bb.GetTop()) - OY - 0.6,
                              pcbnew.ToMM(bb.GetRight()) - OX + 0.6,
                              pcbnew.ToMM(bb.GetBottom()) - OY + 0.6))
    return boxes


def wires(board):
    """Отрезки чужих дорожек и радиус, ближе которого заклёпку не поставить.

    Габаритный прямоугольник для косой дорожки врёт вдвое, поэтому меряем
    расстояние до самого отрезка.
    """
    out = []
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA) or t.GetNetname() == "GND":
            continue
        a, b = t.GetStart(), t.GetEnd()
        out.append((pcbnew.ToMM(a.x) - OX, pcbnew.ToMM(a.y) - OY,
                    pcbnew.ToMM(b.x) - OX, pcbnew.ToMM(b.y) - OY,
                    VIA_PAD / 2 + pcbnew.ToMM(t.GetWidth()) / 2 + 0.25))
    return out


def near_wire(x, y, segs):
    for x1, y1, x2, y2, keep in segs:
        dx, dy = x2 - x1, y2 - y1
        n = dx * dx + dy * dy
        t = 0.0 if n == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / n))
        px, py = x1 + t * dx, y1 + t * dy
        if (x - px) ** 2 + (y - py) ** 2 < keep * keep:
            return True
    return False


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    gnd = wipe(board)
    if gnd is None:
        raise SystemExit("цепь GND на плате не найдена")

    plane(board, pcbnew.B_Cu, gnd, EDGE)
    plane(board, pcbnew.F_Cu, gnd, EDGE)

    # сшивка термопада — под корпусом, до посадки чипа (10-mech.md §7)
    u1 = board.FindFootprintByReference("U1")
    ex = pcbnew.ToMM(u1.GetPosition().x) - OX
    ey = pcbnew.ToMM(u1.GetPosition().y) - OY
    n_epad = 0
    for dx, dy in EPAD_GRID:
        via(board, gnd, ex + dx, ey + dy)
        n_epad += 1

    # сшивка по свободному полю
    busy = occupied(board)
    segs = wires(board)
    n_grid = 0
    y = STITCH_STEP
    while y < BOARD_H:
        x = STITCH_STEP
        while x < BOARD_W:
            if (EDGE + 1.0 < x < BOARD_W - EDGE - 1.0
                    and EDGE + 1.0 < y < BOARD_H - EDGE - 1.0
                    and not any(bx1 < x < bx2 and by1 < y < by2
                                for bx1, by1, bx2, by2 in busy)
                    and not near_wire(x, y, segs)):
                via(board, gnd, x, y)
                n_grid += 1
            x += STITCH_STEP
        y += STITCH_STEP

    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    board.Save(str(BOARD))
    print(f"  полигон GND: изнанка сплошная, лицо заливкой")
    print(f"  переходных под термопадом: {n_epad}")
    print(f"  переходных сшивки по полю: {n_grid}")


if __name__ == "__main__":
    main()
