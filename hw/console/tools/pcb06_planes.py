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

# Сшивка: шаг сетки по свободной земле.
#
# Каждая заклёпка — это два сверления и пайка проволочки с обеих сторон, так
# что шаг здесь не абстракция, а прямая цена работы. Стояло 8 мм «с запасом»,
# и на плате из этого выходило 94 заклёпки — больше, чем вся разводка.
#
# Считаем по делу. Наружу у нас ничего быстрее шины дисплея не выходит (ядро
# 480 МГц и DDR заперты внутри корпуса), f_knee порядка 400 МГц, λ/20 ≈ 21 мм.
# TI SZZA009 и Отт для двухслойных плат дают сетку 12.7 мм — она и берётся.
STITCH_STEP = 12.7

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
    # списки собираем до первого удаления, а удаляем через `RemoveNative`:
    # обычный `Remove` отдаёт объект питону и ломает всё, что создаётся после —
    # `ZONE.Outline()` начинает возвращать сырой SwigPyObject (10-mech.md §8.2)
    # Снимаем ВСЕ свои медные заливки, а не перечисленные по имени: список
    # рельс менялся, и заливки от прошлой пробы оставались на плате молча —
    # плата показывала 170 разрывов вместо 140, и я искал причину в чём угодно,
    # кроме мусора от собственного прошлого прогона. Зоны правил (запреты под
    # лотком карты) не наши, их не трогаем.
    zones = [z for z in board.Zones() if not z.GetIsRuleArea()]
    stitch = [t for t in board.GetTracks()
              if isinstance(t, pcbnew.PCB_VIA) and t.GetNetname() == "GND"
              and t.IsLocked()]
    for item in zones + stitch:
        board.RemoveNative(item)
    return gnd


def plane(board, layer, net, inset):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNet(net)
    z.SetIsFilled(False)
    z.SetLocalClearance(mm(0.25))
    z.SetMinThickness(mm(0.15))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)   # без термобарьеров: паяем феном
    # Островки НЕ удаляем, хотя соблазн есть: обрезки заливки DRC считает
    # разрывами, и «удалять несоединённые» кажется чистой уборкой. Померено —
    # разрывов по земле становится 53 вместо шести: KiCad вырезает и те куски,
    # что держались на сшивочных заклёпках, а на них у нас держится земля всех
    # деталей лица.
    poly = z.Outline()
    poly.NewOutline()
    for x, y in board_outline(inset):
        poly.Append(mm(OX + x), mm(OY + y))
    board.Add(z)
    return z


# Островки питания ПРОБОВАЛИ И ОТКАЗАЛИСЬ. Мысль верная и обычная для
# двухслойки: питание не тянут дорожками, его заливают. Померено: девять
# островков дали питанию 18 связей (+3V3 с 11 разрывов до 4, +0V9 до нуля) — и
# стоили земле 46. Даже два островка на одной компактной рельсе стоили 56.
#
# Причина в том, что у нас заливка земли НЕСУЩАЯ: все детали на лице, и
# земляной вывод каждой из них держится только ею. Островок питания режет её
# на куски, а к куску надо ставить заклёпку — вручную, с двух сторон. Размен
# получается не в нашу пользу.
#
# Оставить пустым, чтобы вернуться к этому осознанно.
RAILS = ()
RAIL_NEAR = 11.0               # на каком расстоянии площадки считаем кучкой
RAIL_MIN = 3                   # кучка меньше трёх островка не стоит
RAIL_PAD = 1.2                 # насколько островок выходит за края кучки


def islands(board, net, refs):
    """Кучки площадок цепи: прямоугольники, которые стоит залить.

    На двухслойной плате питание не тянут дорожками — его заливают. У `+3V3`
    тридцать восемь площадок, у `+1V8` шестнадцать: провести к каждой отдельную
    дорожку значит занять полплаты, и как раз питание у нас и не сходилось —
    35 разрывов из 140. Островок соединяет всю кучку разом и ничего не стоит:
    меди на плате и так полно, вопрос только чьей она будет.

    Кучки собираем связыванием: две площадки в одной, если между ними меньше
    `RAIL_NEAR`. Одиночек не заливаем — им дешевле дорожка.
    """
    pts = []
    for f in board.GetFootprints():
        for p in f.Pads():
            if p.GetNetname() == net:
                q = p.GetPosition()
                pts.append((pcbnew.ToMM(q.x), pcbnew.ToMM(q.y)))
    root = list(range(len(pts)))

    def find(a):
        while root[a] != a:
            root[a] = root[root[a]]
            a = root[a]
        return a

    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            if ((pts[i][0] - pts[j][0]) ** 2
                    + (pts[i][1] - pts[j][1]) ** 2) ** 0.5 < RAIL_NEAR:
                ri, rj = find(i), find(j)
                if ri != rj:
                    root[ri] = rj
    groups = {}
    for i in range(len(pts)):
        groups.setdefault(find(i), []).append(pts[i])
    out = []
    for g in groups.values():
        if len(g) < RAIL_MIN:
            continue
        xs = [q[0] for q in g]
        ys = [q[1] for q in g]
        out.append((min(xs) - RAIL_PAD, min(ys) - RAIL_PAD,
                    max(xs) + RAIL_PAD, max(ys) + RAIL_PAD))
    return out


def island(board, net, box, layer=pcbnew.F_Cu):
    """Прямоугольная заливка рельсы. Приоритет выше земли — земля отступит."""
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNet(net)
    z.SetIsFilled(False)
    z.SetLocalClearance(mm(0.25))
    z.SetMinThickness(mm(0.15))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    z.SetAssignedPriority(10)
    poly = z.Outline()
    poly.NewOutline()
    x1, y1, x2, y2 = box
    for x, y in ((x1, y1), (x2, y1), (x2, y2), (x1, y2)):
        poly.Append(mm(x), mm(y))
    board.Add(z)
    return z


def via(board, net, x, y):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pt(x, y))
    v.SetWidth(mm(VIA_PAD))
    v.SetDrill(mm(VIA_DRILL))
    v.SetNet(net)
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetLocked(True)      # метка «это наша заклёпка», по ней же и снимаем
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


def other_vias(board):
    """Чужие переходные — их ставит разводка, и в них тоже нельзя попадать.

    Сшивка смотрела на детали и на дорожки, а на переходные нет, и заклёпки
    садились ровно в них: три пары совпавших отверстий на плату.
    """
    out = []
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            pos = t.GetPosition()
            out.append((pcbnew.ToMM(pos.x) - OX, pcbnew.ToMM(pos.y) - OY,
                        pcbnew.ToMM(t.GetWidth()) / 2 + VIA_PAD / 2 + 0.25))
    return out


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

    # всё, что надо посмотреть на плате, смотрим до первой правки
    busy = occupied(board)
    segs = wires(board)
    holes = other_vias(board)
    u1 = board.FindFootprintByReference("U1")
    ex = pcbnew.ToMM(u1.GetPosition().x) - OX
    ey = pcbnew.ToMM(u1.GetPosition().y) - OY

    gnd = wipe(board)
    if gnd is None:
        raise SystemExit("цепь GND на плате не найдена")

    plane(board, pcbnew.B_Cu, gnd, EDGE)
    plane(board, pcbnew.F_Cu, gnd, EDGE)

    n_isl = 0
    for name in RAILS:
        net = board.FindNet(name)
        if net is None:
            continue
        for box in islands(board, name, None):
            island(board, net, box)
            n_isl += 1

    # сшивка термопада — под корпусом, до посадки чипа (10-mech.md §7)
    n_epad = 0
    for dx, dy in EPAD_GRID:
        via(board, gnd, ex + dx, ey + dy)
        n_epad += 1

    # сшивка по свободному полю
    n_grid = 0
    y = STITCH_STEP
    while y < BOARD_H:
        x = STITCH_STEP
        while x < BOARD_W:
            if (EDGE + 1.0 < x < BOARD_W - EDGE - 1.0
                    and EDGE + 1.0 < y < BOARD_H - EDGE - 1.0
                    and not any(bx1 < x < bx2 and by1 < y < by2
                                for bx1, by1, bx2, by2 in busy)
                    and not near_wire(x, y, segs)
                    and not any((x - vx) ** 2 + (y - vy) ** 2 < r * r
                                for vx, vy, r in holes)):
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
    print(f"  островков питания: {n_isl}")


if __name__ == "__main__":
    main()
