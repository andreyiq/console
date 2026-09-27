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
import os
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"

OX, OY = 50.0, 40.0
BOARD_W, BOARD_H = 156.0, 74.0
EDGE = 0.5                      # отступ меди от реза, как в правилах платы
CORNER_R = 3.0

# Площадка крупная — сшивку паять руками с двух сторон (10-mech.md §7). Сверло
# то же, что у переходных разводки, 0.4: стояло 0.5, и на плате было два
# сверла под заклёпки — лишняя смена на станке ради 33 отверстий. Поясок от
# этого только шире: 0.25 на сторону против 0.2.
VIA_PAD, VIA_DRILL = 0.9, 0.4

# Сшивка: шаг сетки по свободной земле.
#
# Каждая заклёпка — это два сверления и пайка проволочки с обеих сторон, так
# что шаг здесь не абстракция, а прямая цена работы. Стояло 8 мм «с запасом»,
# и на плате из этого выходило 94 заклёпки — больше, чем вся разводка.
#
# Считаем по делу. Наружу у нас ничего быстрее шины дисплея не выходит (ядро
# 480 МГц и DDR заперты внутри корпуса), f_knee порядка 400 МГц, λ/20 ≈ 21 мм.
# TI SZZA009 и Отт для двухслойных плат дают сетку 12.7 мм — она и берётся.
STITCH_STEP = float(os.environ.get("PCB_STITCH", 12.7))

# Сетка — не по всей плате. Замер: без сетки вовсе каждый кусок лицевой
# заливки всё равно связан с изнанкой (добивка `pcb09_gnd.py`, +2 заклёпки),
# а сама сетка стоила 30 заклёпок, и больше половины из них — под дисплеем и
# у кнопок, где быстрых сигналов нет: обратный ток лицевых дорожек и так идёт
# по сплошной изнанке прямо под ними.
#
# Оставлено два правила из A64 PCB Layout Guide (Allwinner, раздел EMC):
#   * «вдоль края платы заклёпки земли, шаг меньше 3 см» — кольцо `EDGE_*`;
#   * у быстрых сигналов (шина дисплея 50 МГц, USB, кварцы, сам чип) —
#     прежняя сетка 12.7 мм, в прямоугольнике `FAST` (координаты от угла
#     платы, как всё в этом файле).
# `PCB_FULL_GRID=1` возвращает прежнюю сетку по всей плате — для сравнения.
FULL_GRID = bool(os.environ.get("PCB_FULL_GRID"))
FAST = (65.0, 15.0, 122.0, 72.0)      # x 115…172, y 55…112 в осях платы
EDGE_STEP = 28.0                      # < 30 мм по руководству, с запасом
EDGE_IN = 3.5                         # отступ кольца от края: внутри VBUS
# насколько можно сдвинуть вдоль края. 6 не хватало внизу: между кнопками
# SW105/SW106 и розеткой USB точка не нашлась, и край остался без заклёпки
# на 55 мм.
EDGE_SLIDE = 12.0

# Термопад F133: девять переходных внутри 5.72 x 5.72. У Xassette их шесть в
# пределах ±4 мм от центра (08-decoupling.md §4), берём чуть плотнее — это
# единственная земля чипа и единственный его теплоотвод.
EPAD_GRID = [(-1.6, -1.6), (0.0, -1.6), (1.6, -1.6),
             (-1.6, 0.0), (0.0, 0.0), (1.6, 0.0),
             (-1.6, 1.6), (0.0, 1.6), (1.6, 1.6)]

# Термопад зарядника TP4056: 1 А × (5 − 3.7) В = 1.3 Вт на ESOP-8
# (02-power.md §2.5: «под термопад нужен полигон меди»). Лицевая медь у
# термопада — островок внутри петли `VBUS`, тепло уходит только в изнанку,
# а к ней была одна заклёпка в 4 мм. Четыре — под самим термопадом
# 3.3 × 2.4 мм, по углам; смещения — в осях платы, от центра термопада.
THERMAL = {"U5": [(-1.0, -0.6), (1.0, -0.6), (-1.0, 0.6), (1.0, 0.6)]}


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
    # Снимаем ВСЕ переходные земли, а не только помеченные замком. Замок был
    # меткой «эта заклёпка наша», и на ней скрипт спотыкался: `pcb09_gnd.py`
    # метку не ставил, его добивки не снимались никогда и копились от прогона
    # к прогону — пять штук осталось стоять снаружи контура платы, за резом.
    # Метка не нужна вовсе: `pcb10_route.py` цепь `GND` не разводит (она идёт
    # плоскостью), значит на плате нет ни одной переходной земли, которую
    # поставили бы не мы.
    stitch = [t for t in board.GetTracks()
              if isinstance(t, pcbnew.PCB_VIA) and t.GetNetname() == "GND"]
    for item in zones + stitch:
        board.RemoveNative(item)
    return gnd


def plane(board, layer, net, inset):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNet(net)
    z.SetIsFilled(False)
    z.SetLocalClearance(mm(0.25))
    # Минимальная толщина заливки — 0.2, не меньше. Стояло 0.15, и это было
    # число ниоткуда: процесс проверен на 0.2/0.2 на живой плате
    # (`10-mech.md §7`), а на 0.15 у нас нет ни одного травления. У дорожки
    # такую вольность хотя бы видно на чертеже; перемычка заливки возникает
    # сама, в случайном месте, и узнать, что земля всей платы держится на
    # ней одной, можно только после травления.
    z.SetMinThickness(mm(0.2))
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
# Перемерено после того, как в конвейере починили сшивку (она ставилась через
# раз) и модель зазоров. Вывод не изменился, числа стали чище: семь островков
# на `+3V3 +1V8 +0V9` убрали питанию 15 разрывов из 29 и добавили земле 38 —
# с трёх до сорока одного. Всего разрывов на плате 132 против 155.
#
# Видно и почему: добивка земли после островков не может поставить 75 заклёпок
# из 84 — «пропущено (занято)». Островок режет заливку на куски, а свободного
# места рядом с куском уже нет.
#
# Причина в том, что у нас заливка земли НЕСУЩАЯ: все детали на лице, и
# земляной вывод каждой из них держится только ею. Островок питания режет её
# на куски, а к куску надо ставить заклёпку — вручную, с двух сторон. Размен
# получается не в нашу пользу.
#
# Оставить пустым, чтобы вернуться к этому осознанно. Проверить, не изменилось
# ли что-нибудь, стоит одной командой:
#     PCB_RAILS="+3V3 +1V8 +0V9" python3 pcb06_planes.py && python3 pcb09_gnd.py
RAILS = tuple(os.environ.get("PCB_RAILS", "").split())
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
    z.SetMinThickness(mm(0.2))                        # почему 0.2 — см. `plane`
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
    """ЧУЖИЕ переходные — их ставит разводка, и в них тоже нельзя попадать.

    Сшивка смотрела на детали и на дорожки, а на переходные нет, и заклёпки
    садились ровно в них: три пары совпавших отверстий на плату.

    Своя прошлая сшивка сюда не входит, и это не мелочь. Плату мы смотрим до
    первой правки (`Remove` портит контейнеры pcbnew), а `wipe` снимает старую
    сшивку уже после. Пока свои заклёпки попадали в список, каждая занимала
    свою же точку сетки — и второй прогон подряд ставил ноль вместо двадцати
    пяти. Скрипт «работал» через раз, и по выводу это выглядело как теснота.
    """
    out = []
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            if t.GetNetname() == "GND":
                continue          # наша прошлая сшивка, её снимет `wipe`
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
    # центр термопада, а не корпуса: у U5 они совпадают, но мерить надо то,
    # во что сверлим
    thermal = {}
    for ref in THERMAL:
        f = board.FindFootprintByReference(ref)
        ep = max(f.Pads(), key=lambda p: p.GetSize().x * p.GetSize().y)
        thermal[ref] = (pcbnew.ToMM(ep.GetPosition().x) - OX,
                        pcbnew.ToMM(ep.GetPosition().y) - OY)

    gnd = wipe(board)
    if gnd is None:
        raise SystemExit("цепь GND на плате не найдена")

    # Сплошное подключение — у ВСЕХ деталей, а не только по умолчанию зоны:
    # библиотечная паяльная перемычка (`JP1`, Jumper:SolderJumper-2) несёт
    # свой режим «термобарьер», и её земляная площадка держалась на одной
    # спице — DRC `starved_thermal`. Паяем феном, барьеры не нужны.
    for f in board.GetFootprints():
        if f.GetLocalZoneConnection() == pcbnew.ZONE_CONNECTION_THERMAL:
            f.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_INHERITED)

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
    for ref, grid in THERMAL.items():
        for dx, dy in grid:
            via(board, gnd, thermal[ref][0] + dx, thermal[ref][1] + dy)
            n_epad += 1

    def fits(x, y):
        return (EDGE + 1.0 < x < BOARD_W - EDGE - 1.0
                and EDGE + 1.0 < y < BOARD_H - EDGE - 1.0
                and not any(bx1 < x < bx2 and by1 < y < by2
                            for bx1, by1, bx2, by2 in busy)
                and not near_wire(x, y, segs)
                and not any((x - vx) ** 2 + (y - vy) ** 2 < r * r
                            for vx, vy, r in holes))

    # сшивка по полю — только там, где быстрые сигналы (`FAST`)
    n_grid = 0
    fx1, fy1, fx2, fy2 = FAST
    y = STITCH_STEP
    while y < BOARD_H:
        x = STITCH_STEP
        while x < BOARD_W:
            if (FULL_GRID or (fx1 <= x <= fx2 and fy1 <= y <= fy2)) \
                    and fits(x, y):
                via(board, gnd, x, y)
                holes.append((x, y, VIA_PAD + 0.25))
                n_grid += 1
            x += STITCH_STEP
        y += STITCH_STEP

    # кольцо по краю: шаг не больше `EDGE_STEP`, место ищется вдоль края
    n_edge, n_edge_miss = 0, 0
    if not FULL_GRID:
        a, b = EDGE_IN, (BOARD_W - EDGE_IN, BOARD_H - EDGE_IN)
        sides = [((a, a), (b[0], a)), ((b[0], a), (b[0], b[1])),
                 ((b[0], b[1]), (a, b[1])), ((a, b[1]), (a, a))]
        for (x1, y1), (x2, y2) in sides:
            length = math.hypot(x2 - x1, y2 - y1)
            n = max(1, math.ceil(length / EDGE_STEP))
            ux, uy = (x2 - x1) / length, (y2 - y1) / length
            nx, ny = -uy, ux                      # внутрь платы
            for k in range(n):
                t0 = (k + 0.5) * length / n
                spot = None
                # вдоль края ±EDGE_SLIDE, вглубь до 2 мм
                for s in [0] + [d * sgn for d in
                                [q * 0.4 for q in range(1, int(EDGE_SLIDE / 0.4) + 1)]
                                for sgn in (1, -1)]:
                    for dep in (0.0, 0.8, 1.6, 2.4, 3.2):
                        x = x1 + ux * (t0 + s) + nx * dep
                        y = y1 + uy * (t0 + s) + ny * dep
                        if fits(x, y):
                            spot = (x, y)
                            break
                    if spot:
                        break
                if spot is None:
                    n_edge_miss += 1
                    continue
                via(board, gnd, *spot)
                holes.append(spot + (VIA_PAD + 0.25,))
                n_edge += 1

    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    board.Save(str(BOARD))
    print(f"  полигон GND: изнанка сплошная, лицо заливкой")
    print(f"  переходных под термопадом: {n_epad}")
    print(f"  переходных сшивки по полю: {n_grid}"
          + ("" if FULL_GRID else " (только зона быстрых сигналов)"))
    if not FULL_GRID:
        print(f"  переходных по краю платы: {n_edge}, шаг ≤ {EDGE_STEP:.0f} мм"
              + (f"; НЕ НАШЛОСЬ МЕСТА: {n_edge_miss}" if n_edge_miss else ""))
    print(f"  островков питания: {n_isl}")


if __name__ == "__main__":
    main()
