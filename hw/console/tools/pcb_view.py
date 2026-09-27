#!/usr/bin/env python3
"""Картинка платы, на которую можно СМОТРЕТЬ.

Зачем свой рисовальщик, когда есть `kicad-cli pcb render` и экспорт в SVG.

* 3D-рендер показывает корпуса и лак, а не дорожки: по нему не видно, где
  разводка пошла криво.
* SVG-экспорт KiCad рисует медь одним путём с правилом evenodd, и после
  растеризации получается красное поле с белыми дырками — читать нельзя.
* KiCad MCP (`get_board_2d_view`) отдал картинку старой платы: крепёж на
  месте, разводка вся. Он смотрит не в тот файл, и область обрезки игнорирует.

Здесь рисуется ровно то, что нужно глазу: лицо, изнанка, переходные,
площадки, имена корпусов и **воздушные связи** — что ещё не разведено.
Воздушные считаются как в трассировщике: цепь разбивается на куски связной
меди, и куски соединяются остовом по ближайшим точкам. Линии между кусками и
есть то, что не сделано.

Запуск:
    python3 tools/pcb_view.py                       — вся плата
    python3 tools/pcb_view.py 105 55 162 98         — окно x0 y0 x1 y1
    python3 tools/pcb_view.py --net LCD-CS          — только эти цепи
    python3 tools/pcb_view.py -o /tmp/x.png 105 55 162 98
"""
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle
import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"

FRONT = "#d05a2a"       # лицо
BACK = "#3a7fd0"        # изнанка
VIA = "#f0d020"         # переходное
PAD = "#e8e8e8"         # площадка
AIR = "#ff3060"         # не разведено
EDGE = "#f0f0f0"        # контур платы

MM = pcbnew.ToMM


def layer_color(layer):
    return BACK if layer == pcbnew.B_Cu else FRONT


def pieces(items):
    """Разбить набор отрезков/площадок цепи на куски связной меди.

    `items` — список `(kind, data)`, где у каждого есть множество точек и
    слой. Два элемента в одном куске, если они касаются: общая точка на общем
    слое, либо один из них переходное (оно связывает слои).
    """
    parent = list(range(len(items)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if touch(items[i], items[j]):
                union(i, j)

    out = {}
    for i, it in enumerate(items):
        out.setdefault(find(i), []).append(it)
    return list(out.values())


def touch(a, b):
    """Касаются ли два куска меди — по меди, как считает сам KiCad.

    Каждый кусок — это осевая геометрия (точка площадки/заклёпки или отрезок
    дорожки) плюс то, насколько медь выходит за неё: у дорожки полширины, у
    площадки — её прямоугольник. Два куска соединены, если медь касается.

    Прежде касание проверялось по концам с допуском 0.06 мм, и это врало в
    обе стороны. Сперва площадка считалась точкой — дорожка, доведённая до
    КРАЯ площадки, выглядела разрывом (девять ложных тревог подряд). Потом
    трассировщик начал дорожку на сетке 0.2, а луч веера кончился на 85.35 —
    между осями 0.05 мм, медь перекрыта на 0.15, `kicad-cli` соединения видит,
    а смотрелка показывала разрыв пары USB. Ложная тревога опасна: она
    заставляет чинить то, что не сломано.
    """
    la, pa, ba, pada, ra = a
    lb, pb, bb, padb, rb = b
    if not (la & lb):
        return False
    r = ra + rb + 1e-6
    if ba[2] + r < bb[0] or bb[2] + r < ba[0]:
        return False
    if ba[3] + r < bb[1] or bb[3] + r < ba[1]:
        return False
    if pada and padb:
        return True                     # прямоугольники пересеклись выше
    if pada:
        return seg_rect(pb, ba) <= rb + 1e-6
    if padb:
        return seg_rect(pa, bb) <= ra + 1e-6
    return seg_seg(pa, pb) <= r


def pt_seg(p, s):
    """Расстояние от точки до отрезка (отрезок может быть точкой)."""
    (x1, y1), (x2, y2) = s[0], s[-1]
    dx, dy = x2 - x1, y2 - y1
    ln = dx * dx + dy * dy
    if ln < 1e-12:
        return math.dist(p, (x1, y1))
    t = max(0.0, min(1.0, ((p[0] - x1) * dx + (p[1] - y1) * dy) / ln))
    return math.dist(p, (x1 + t * dx, y1 + t * dy))


def cross(a, b):
    """Пересекаются ли два отрезка (строго, без касаний в общей точке)."""
    (x1, y1), (x2, y2) = a
    (x3, y3), (x4, y4) = b
    for p in (a[0], a[1]):
        for q in (b[0], b[1]):
            if abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6:
                return False

    def side(px, py, qx, qy, rx, ry):
        return (qx - px) * (ry - py) - (qy - py) * (rx - px)

    d1 = side(x1, y1, x2, y2, x3, y3)
    d2 = side(x1, y1, x2, y2, x4, y4)
    d3 = side(x3, y3, x4, y4, x1, y1)
    d4 = side(x3, y3, x4, y4, x2, y2)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def seg_seg(a, b):
    """Кратчайшее расстояние между двумя отрезками."""
    a = (a[0], a[-1])
    b = (b[0], b[-1])
    if cross(a, b):
        return 0.0
    return min(pt_seg(a[0], b), pt_seg(a[1], b),
               pt_seg(b[0], a), pt_seg(b[1], a))


def seg_rect(s, box):
    """Расстояние от отрезка до прямоугольника площадки (0 — если задевает)."""
    x0, y0, x1, y1 = box
    s = (s[0], s[-1])
    for x, y in s:
        if x0 <= x <= x1 and y0 <= y <= y1:
            return 0.0
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    edges = list(zip(corners, corners[1:] + corners[:1]))
    if any(cross(s, e) for e in edges):
        return 0.0
    return min([seg_seg(s, e) for e in edges])


def air_lines(board, nets):
    """Остов по кускам меди каждой цепи: что осталось соединить."""
    by_net = {}
    for f in board.GetFootprints():
        for p in f.Pads():
            n = p.GetNetname()
            if not n:
                continue
            c = p.GetPosition()
            x, y = MM(c.x), MM(c.y)
            layers = set()
            for L in (pcbnew.F_Cu, pcbnew.B_Cu):
                if p.IsOnLayer(L):
                    layers.add(L)
            # Прямоугольник площадки — габарит с учётом поворота корпуса:
            # у повёрнутой на 90° площадки ширина и высота меняются местами,
            # а `GetSize` отдаёт их неповёрнутыми.
            bb = p.GetBoundingBox()
            by_net.setdefault(n, []).append(
                (layers, [(x, y)],
                 (MM(bb.GetX()), MM(bb.GetY()),
                  MM(bb.GetRight()), MM(bb.GetBottom())), True, 0.0))
    for t in board.GetTracks():
        n = t.GetNetname()
        if not n:
            continue
        if isinstance(t, pcbnew.PCB_VIA):
            p = t.GetPosition()
            x, y = MM(p.x), MM(p.y)
            r = MM(t.GetWidth(pcbnew.F_Cu)) / 2
            by_net.setdefault(n, []).append(
                ({pcbnew.F_Cu, pcbnew.B_Cu}, [(x, y)],
                 (x - r, y - r, x + r, y + r), True, 0.0))
        else:
            s, e = t.GetStart(), t.GetEnd()
            x1, y1, x2, y2 = MM(s.x), MM(s.y), MM(e.x), MM(e.y)
            by_net.setdefault(n, []).append(
                ({t.GetLayer()}, [(x1, y1), (x2, y2)],
                 (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)), False,
                 MM(t.GetWidth()) / 2))

    out = []
    for n, items in by_net.items():
        if nets and n not in nets:
            continue
        if n == "GND":          # землю ведёт заливка, воздушных по ней тьма
            continue
        gr = pieces(items)
        if len(gr) < 2:
            continue
        # Остов: жадно присоединяем ближайший кусок к уже собранному.
        rest = list(range(1, len(gr)))
        have = [0]
        while rest:
            best = None
            for i in have:
                for j in rest:
                    for p1 in [p for it in gr[i] for p in it[1]]:
                        for p2 in [p for it in gr[j] for p in it[1]]:
                            d = (p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2
                            if best is None or d < best[0]:
                                best = (d, j, p1, p2)
            _, j, p1, p2 = best
            out.append((n, p1, p2))
            have.append(j)
            rest.remove(j)
    return out


def report_crossings(air):
    """Кто с кем пересекается по воздуху.

    Воздушная связь — это прямая «отсюда сюда». Если две такие прямые разных
    цепей пересекаются, то на одном слое их так не провести: кому-то придётся
    нырнуть на изнанку, то есть заплатить двумя переходными. Поэтому число
    пересечений — это нижняя оценка беды, и снимается она не трассировщиком, а
    руками: поменять выводы местами, повернуть корпус, переставить деталь.
    """
    pairs = {}
    for i in range(len(air)):
        for j in range(i + 1, len(air)):
            n1, p1, p2 = air[i]
            n2, q1, q2 = air[j]
            if n1 == n2:
                continue
            if cross((p1, p2), (q1, q2)):
                k = tuple(sorted((n1, n2)))
                pairs[k] = pairs.get(k, 0) + 1
    if not pairs:
        print("пересечений по воздуху нет — всё можно провести по лицу")
        return
    tally = {}
    for (n1, n2), c in pairs.items():
        tally[n1] = tally.get(n1, 0) + c
        tally[n2] = tally.get(n2, 0) + c
    print(f"пересечений по воздуху: {sum(pairs.values())} "
          f"(каждое — кандидат на две переходных)")
    for n in sorted(tally, key=lambda k: -tally[k])[:10]:
        who = sorted(b if a == n else a for a, b in pairs if n in (a, b))
        print(f"   {n:12} мешает {tally[n]:2}: {' '.join(who[:6])}")


def main():
    args = [a for a in sys.argv[1:]]
    out = ROOT / "view.png"
    nets = set()
    if "-o" in args:
        i = args.index("-o")
        out = Path(args[i + 1])
        del args[i:i + 2]
    while "--net" in args:
        i = args.index("--net")
        nets.add(args[i + 1])
        del args[i:i + 2]
    win = [float(a) for a in args] if len(args) == 4 else None

    board = pcbnew.LoadBoard(str(BOARD))
    if win is None:
        bb = board.GetBoardEdgesBoundingBox()
        win = [MM(bb.GetX()) - 2, MM(bb.GetY()) - 2,
               MM(bb.GetRight()) + 2, MM(bb.GetBottom()) + 2]
    x0, y0, x1, y1 = win

    # Маленькое окно рисуем крупнее: смотреть узел в 6 мм на картинке в 240
    # точек бессмысленно, а именно в такие узлы и упирается разводка.
    k = max(1.0, 50.0 / max(x1 - x0, y1 - y0))
    fig, ax = plt.subplots(figsize=((x1 - x0) * k / 6, (y1 - y0) * k / 6),
                           dpi=140)
    ax.set_facecolor("#101010")
    fig.patch.set_facecolor("#101010")

    for d in board.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts and hasattr(d, "GetStart"):
            s, e = d.GetStart(), d.GetEnd()
            ax.plot([MM(s.x), MM(e.x)], [MM(s.y), MM(e.y)],
                    color=EDGE, lw=0.8, zorder=1)

    for f in board.GetFootprints():
        p = f.GetPosition()
        fx, fy = MM(p.x), MM(p.y)
        # По габариту, а не по центру: у крупного корпуса центр бывает далеко
        # за окном, а его площадки — ровно в том узле, куда мы и смотрим.
        bb = f.GetBoundingBox(False, False)
        if (MM(bb.GetRight()) < x0 or MM(bb.GetX()) > x1
                or MM(bb.GetBottom()) < y0 or MM(bb.GetY()) > y1):
            continue
        for pad in f.Pads():
            c = pad.GetPosition()
            sz = pad.GetSize()
            ax.add_patch(Rectangle(
                (MM(c.x) - MM(sz.x) / 2, MM(c.y) - MM(sz.y) / 2),
                MM(sz.x), MM(sz.y), color=PAD, alpha=0.55, zorder=2))
        ax.text(fx, fy, f.GetReference(), color="#70e070", fontsize=4,
                ha="center", va="center", zorder=6)

    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            p = t.GetPosition()
            ax.add_patch(Circle((MM(p.x), MM(p.y)), MM(t.GetWidth()) / 2,
                                color=VIA, zorder=5))
            continue
        s, e = t.GetStart(), t.GetEnd()
        ax.plot([MM(s.x), MM(e.x)], [MM(s.y), MM(e.y)],
                color=layer_color(t.GetLayer()), lw=MM(t.GetWidth()) * 3.2,
                solid_capstyle="round", zorder=3 if t.GetLayer() else 4)

    # Воздушные рисуем только те, что хотя бы одним концом в окне. Иначе
    # картинку перечёркивают связи припаркованных деталей, которых тут нет.
    def in_win(p):
        return x0 <= p[0] <= x1 and y0 <= p[1] <= y1

    air = [a for a in air_lines(board, nets) if in_win(a[1]) or in_win(a[2])]
    for n, p1, p2 in air:
        ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=AIR, lw=0.7,
                ls=":", zorder=7)
        ax.text((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2, n, color=AIR,
                fontsize=3.5, ha="center", va="center", zorder=8)

    ax.set_xlim(x0, x1)
    ax.set_ylim(y1, y0)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_position([0, 0, 1, 1])
    fig.savefig(out, facecolor=fig.get_facecolor())
    print(f"картинка: {out}")
    print(f"окно: x {x0}..{x1}, y {y0}..{y1} мм")
    print(f"лицо оранжевое, изнанка синяя, переходные жёлтые, "
          f"не разведено — красный пунктир ({len(air)} шт.)")
    report_crossings(air)


if __name__ == "__main__":
    main()
