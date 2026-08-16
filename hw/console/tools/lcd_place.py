#!/usr/bin/env python3
"""Куда поставить разъём шлейфа, чтобы шина дисплея легла без крюка.

Шина дисплея — 23 линии, четверть всей платы, и она же самая тяжёлая: банк
`PD` расходуется целиком (06-display.md §6.1). Ставить разъём «где влезло» тут
дороже всего.

Что мы можем и чего не можем.

**Выводы чипа не двигаются.** Линии дисплея — выводы 52…76, и они лежат
подряд, огибая левый верхний угол корпуса: 52…64 по верхней стороне справа
налево, 67…76 дальше вниз по левой. Это лента, и разорвать её нельзя.

**Порядок бит данных — наш.** Панель кормим своим кодом: перестановка бита в
кадре вносится в палитру и стоит нуля, команды i8080 мы пишем сами и
переставим их один раз. Значит какой вывод чипа на какой бит панели — вопрос
удобства разводки, а не данность. Управляющие линии (`CS`, `RS`, `WR`, `RD`,
`RST`, `IM`, подсветка) переставлять нельзя, у них на шлейфе своё место.

**Разъём двигается в пределах вылета шлейфа.** Спека панели, вид сзади: сгиб
`1.0 MAX`, вылет по изнанке `43.72 ± 0.5`. Панель занимает x 35.965…120.035,
и от того, каким торцом она положена, зависит всё: при выходе шлейфа влево
разъём не уедет правее ~79.7, при развороте панели на 180° — не левее ~76.3,
зато достаёт до самого чипа. Картинку в перевёрнутой панели ставит на место
либо `MADCTL`, либо наш же порядок вывода кадра — даром и там, и там.

Скрипт перебирает место и поворот разъёма, для каждого варианта решает
назначение бит данных (венгерский алгоритм) и считает две вещи: суммарную
длину шины и число пересечений. Печатает лучшие.

Запуск: `python3 lcd_place.py`.
"""
import itertools
import math
from pathlib import Path

import pcbnew
from scipy.optimize import linear_sum_assignment

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"

OX, OY = 50.0, 40.0
PANEL = (35.965, 9.72, 120.035, 64.28)      # габарит панели на плате
REACH = 43.72                                # вылет шлейфа по изнанке панели

# Что на каком выводе шлейфа — 06-display.md §6.1. Данные переставляемы,
# управление нет.
FFC_DATA = {f"LCD-DB{i}": 17 + i for i in range(16)}
FFC_FIX = {"LCD-CS": 9, "LCD-RS": 10, "LCD-WR": 11, "LCD-RD": 12,
           "LCD-RST": 15, "LCD-IM": 38, "LCD-BL": 34}


def pads_of(fp):
    out = {}
    for p in fp.Pads():
        q = p.GetPosition()
        out[p.GetPadName()] = (pcbnew.ToMM(q.x) - OX, pcbnew.ToMM(q.y) - OY)
    return out


def chip_lcd(board):
    """Вывод чипа -> точка, только линии дисплея."""
    u1 = board.FindFootprintByReference("U1")
    out = {}
    for p in u1.Pads():
        n = p.GetNetname()
        if n.startswith("LCD"):
            q = p.GetPosition()
            out[n] = (pcbnew.ToMM(q.x) - OX, pcbnew.ToMM(q.y) - OY)
    return out


def conn_geometry(board):
    """Смещения площадок разъёма от его центра, в его собственных осях."""
    j = board.FindFootprintByReference("J601")
    q = j.GetPosition()
    cx, cy = pcbnew.ToMM(q.x) - OX, pcbnew.ToMM(q.y) - OY
    rot = math.radians(j.GetOrientationDegrees())
    out = {}
    for name, (px, py) in pads_of(j).items():
        dx, dy = px - cx, py - cy
        # снимаем нынешний поворот, чтобы дальше ставить любой
        c, s = math.cos(-rot), math.sin(-rot)
        out[name] = (dx * c + dy * s, -dx * s + dy * c)
    return out


def placed(geom, x, y, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return {n: (x + dx * c - dy * s, y + dx * s + dy * c)
            for n, (dx, dy) in geom.items()}


def crossings(pairs):
    """Сколько пар связей пересекается — на плоскости это цена переходных."""
    def cross(a, b, c, d):
        def sgn(p, q, r):
            v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
            return (v > 1e-9) - (v < -1e-9)
        return (sgn(a, b, c) * sgn(a, b, d) < 0
                and sgn(c, d, a) * sgn(c, d, b) < 0)
    n = 0
    for (a, b), (c, d) in itertools.combinations(pairs, 2):
        if cross(a, b, c, d):
            n += 1
    return n


def score(chip, conn):
    """Длина шины и пересечения при лучшем назначении бит данных."""
    fixed = [(chip[n], conn[str(p)]) for n, p in FFC_FIX.items()
             if n in chip and str(p) in conn]
    ctrl = sum(math.dist(a, b) for a, b in fixed)

    src = [chip[n] for n in FFC_DATA if n in chip]
    dst = [conn[str(p)] for p in FFC_DATA.values() if str(p) in conn]
    cost = [[math.dist(a, b) for b in dst] for a in src]
    r, c = linear_sum_assignment(cost)
    data = [(src[i], dst[j]) for i, j in zip(r, c)]
    return ctrl + sum(cost[i][j] for i, j in zip(r, c)), crossings(fixed + data)


def busy(board, movable=()):
    """Габариты деталей — куда разъём не поставить.

    `movable` не считаем помехой: кольцо развязки мы кладём сами и можем
    переложить, а вот корпус чипа, кварцы и разъёмы на торцах — нет.
    """
    out = []
    for f in board.GetFootprints():
        ref = f.GetReference()
        if ref == "J601" or ref in movable:
            continue
        f.BuildCourtyardCaches()
        cy = f.GetCourtyard(pcbnew.F_CrtYd)
        if not cy.OutlineCount():
            continue
        bb = cy.BBox()
        out.append((pcbnew.ToMM(bb.GetLeft()) - OX, pcbnew.ToMM(bb.GetTop()) - OY,
                    pcbnew.ToMM(bb.GetRight()) - OX,
                    pcbnew.ToMM(bb.GetBottom()) - OY))
    return out


def detail(board, chip, geom, others, ring, x, y, deg):
    """Подробности лучшего варианта: габарит, кому уступить, порядок бит."""
    conn = placed(geom, x, y, deg)
    xs = [p[0] for p in conn.values()]
    ys = [p[1] for p in conn.values()]
    print(f"     габарит площадок x {min(xs):.1f}…{max(xs):.1f}, "
          f"y {min(ys):.1f}…{max(ys):.1f}")
    hit = []
    for f in board.GetFootprints():
        if f.GetReference() not in ring:
            continue
        f.BuildCourtyardCaches()
        cy = f.GetCourtyard(pcbnew.F_CrtYd)
        if not cy.OutlineCount():
            continue
        bb = cy.BBox()
        b = (pcbnew.ToMM(bb.GetLeft()) - OX, pcbnew.ToMM(bb.GetTop()) - OY,
             pcbnew.ToMM(bb.GetRight()) - OX, pcbnew.ToMM(bb.GetBottom()) - OY)
        if (min(xs) - 0.6 < b[2] and max(xs) + 0.6 > b[0]
                and min(ys) - 0.6 < b[3] and max(ys) + 0.6 > b[1]):
            hit.append(f.GetReference())
    print("     подвинуть конденсаторы:", " ".join(sorted(hit)) or "никого")

    src = [(n, chip[n]) for n in FFC_DATA if n in chip]
    dst = [(p, conn[str(p)]) for p in FFC_DATA.values() if str(p) in conn]
    cost = [[math.dist(a[1], b[1]) for b in dst] for a in src]
    r, c = linear_sum_assignment(cost)
    pairs = sorted((dst[j][0], src[i][0]) for i, j in zip(r, c))
    print("     бит данных ложится так (вывод шлейфа ← цепь):")
    print("      ", ", ".join(f"{p}←{n.replace('LCD-','')}" for p, n in pairs))


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    chip = chip_lcd(board)
    geom = conn_geometry(board)
    ring = {r for r in (f.GetReference() for f in board.GetFootprints())
            if r.startswith("C8")}
    others = busy(board, ring)

    u1 = board.FindFootprintByReference("U1")
    q = u1.GetPosition()
    ux, uy = pcbnew.ToMM(q.x) - OX, pcbnew.ToMM(q.y) - OY

    print(f"F133 в ({ux:.1f}, {uy:.1f}); линий дисплея {len(chip)}")
    print(f"панель x {PANEL[0]:.1f}…{PANEL[2]:.1f}, вылет шлейфа {REACH}")
    print(f"кольцо развязки ({len(ring)} шт) считаем подвижным\n")

    for name, lo, hi in (("шлейф слева, как сейчас",
                          PANEL[0], PANEL[0] + REACH),
                         ("панель развёрнута, шлейф справа",
                          PANEL[2] - REACH, PANEL[2])):
        best = []
        for deg in (0, 90, 180, 270):
            for x in [v / 2 for v in range(int(lo * 2), int(hi * 2) + 1, 3)]:
                for y in [v / 2 for v in range(24, 120, 3)]:
                    conn = placed(geom, x, y, deg)
                    xs = [p[0] for p in conn.values()]
                    ys = [p[1] for p in conn.values()]
                    if not (PANEL[0] < min(xs) and max(xs) < PANEL[2]
                            and PANEL[1] < min(ys) and max(ys) < PANEL[3]):
                        continue
                    x1, y1 = min(xs) - 0.6, min(ys) - 0.6
                    x2, y2 = max(xs) + 0.6, max(ys) + 0.6
                    if any(x1 < bx2 and x2 > bx1 and y1 < by2 and y2 > by1
                           for bx1, by1, bx2, by2 in others):
                        continue                      # налезает на детали
                    length, cr = score(chip, conn)
                    best.append((cr, length, x, y, deg))
        best.sort()
        print(f"— {name}")
        for cr, length, x, y, deg in best[:4]:
            print(f"   ({x:5.1f}, {y:4.1f}) поворот {deg:3d}: "
                  f"шина {length:6.0f} мм, пересечений {cr}")
        detail(board, chip, geom, others, ring, *best[0][2:])
        print()


if __name__ == "__main__":
    main()
