#!/usr/bin/env python3
"""Добивка земли: заклёпка в каждый кусок заливки, отрезанный от плоскости.

Изнанка — сплошная плоскость `GND`, лицо — заливка того же `GND`, между ними
сшивка с шагом 8 мм (`pcb06_planes.py`). Но после трассировки дорожки режут
переднюю заливку на куски, и часть кусков остаётся без связи с плоскостью:
медь есть, связи нет. Такой кусок — не пустяк: это висящая в воздухе медь
площадью в десятки квадратных миллиметров.

Работаем от самих кусков заливки, а не от текста отчёта DRC, и не по
близости, а по принадлежности. Прежняя версия делала наоборот, и это была
тихая поломка: она искала свободное место кольцами до 2.4 мм от точки,
названной DRC, и ставила заклёпку в первое геометрически свободное — то есть
запросто в СОСЕДНИЙ кусок или в щель между кусками. Отчёт при этом честно
говорил «добито 8», хотя связал ли кто-нибудь из этих восьми хоть что-то, он
не знал. Замер: восемь заклёпок, разрывов по земле по-прежнему 18.

Кусок считается связанным, если внутри него стоит переходная `GND` или
сквозная площадка `GND` — только они пробивают на изнанку.

Место под заклёпку проверяется с двух сторон:
  * на лице — все восемь точек на радиусе (полплощадки + зазор) лежат внутри
    ЭТОГО куска;
  * на изнанке — те же восемь точек лежат внутри плоскости.
Заливка уже соблюдает зазоры до всего чужого, поэтому «внутри своей меди с
запасом» и означает «зазоры соблюдены» — отдельного перебора чужих площадок
не нужно, а прежний перебор ошибался: он смотрел только на площадки, но не на
дорожки.

Из подходящих мест берём то, что дальше от уже стоящих заклёпок: их паять
руками, и две рядом хуже двух вразброс.

Запускать после `pcb08_ses.py` / `pcb10_route.py` и `pcb06_planes.py`.
Идемпотентно: свои заклёпки помечены `locked`, и `pcb06_planes.py` снимает их
перед новой заливкой.
"""
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"

VIA_PAD, VIA_DRILL = 0.7, 0.4   # как в разводке, 10-mech.md §7
CLEAR = 0.2                     # зазор, как на всей плате
# Между центрами двух отверстий: сверло плюс 0.2495 между кромками
# (console.kicad_dru). Правило про сверло, а не про медь, и заливка его не
# видит — поэтому здесь оно проверяется отдельно.
HOLE = VIA_DRILL + 0.2495
# Шаг перебора мест внутри куска. Мельче не помогает, и это проверено, а не
# предположено: на куске у вывода 2 `U501` шаг 0.1 и 0.05 дают ноль годных мест
# так же, как 0.2. Там их нет вовсе — все места, где умещается площадка
# заклёпки, приходятся на саму площадку детали, а сверлить сквозь неё нельзя.
# Этот кусок лечится не добивкой, а разводкой: см. отчёт скрипта.
GRID = 0.2
# На этом радиусе вокруг места нужна СВОЯ медь — ровно полплощадки заклёпки, и
# ни сотой больше. Зазор сюда не входит, и это не поблажка: зазор нужен до
# ЧУЖОЙ меди, а вокруг заклёпки лежит своя земля. Заливка сама держит 0.25 до
# чужого, поэтому площадка, целиком лежащая внутри куска заливки, зазоры
# соблюдает по построению — и полигон заливки уже огранён в запас, добавлять
# «на огранку» нечего.
#
# Числа на куске 2.25 мм² у вывода 2 `U501`, перебор с шагом 0.02: радиус 0.40
# подходит в 6 точках, 0.35 — в 317. Стояло `+ CLEAR` (0.55) — куску отказывали
# вовсе; поставил 0.40 «с запасом» — отказывали снова, потому что те 6 точек
# отсекала проверка сверла. Запас наугад стоил ровно одной связи.
RING = VIA_PAD / 2

# Восемь направлений вместо круга: круг здесь не нужен, а восемь точек ловят и
# узкий перешеек, и близкий край. Диагонали через 0.7071, чтобы радиус был тот
# же, а не корень из двух больше.
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1),
        (0.7071, 0.7071), (0.7071, -0.7071),
        (-0.7071, 0.7071), (-0.7071, -0.7071))


def mm(v):
    return pcbnew.FromMM(v)


def pt(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def deep_inside(poly, x, y, r=RING):
    """Точка лежит внутри `poly` вместе с площадкой заклёпки и зазором."""
    return all(poly.Contains(pt(x + dx * r, y + dy * r)) for dx, dy in DIRS)


def pieces(zones, layer, net):
    """Куски залитой меди цепи `net` на слое `layer`, каждый со своими дырами."""
    out = []
    for z in zones:
        if z.GetNetCode() != net or not z.IsOnLayer(layer):
            continue
        polys = z.GetFilledPolysList(layer)
        for k in range(polys.OutlineCount()):
            one = pcbnew.SHAPE_POLY_SET()
            one.AddOutline(polys.Outline(k))
            for h in range(polys.HoleCount(k)):
                one.AddHole(polys.Hole(k, h), 0)
            out.append(one)
    return out


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    gnd = board.FindNet("GND")
    if gnd is None:
        raise SystemExit("цепь GND на плате не найдена — сначала pcb00_nets.py")
    code = gnd.GetNetCode()

    # Заливка должна быть свежей: куски считаем по ней, а не по тому, что было
    # залито до последней трассировки.
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())

    # Что уже пробивает лицо на изнанку.
    pierce = [(pcbnew.ToMM(t.GetPosition().x), pcbnew.ToMM(t.GetPosition().y))
              for t in board.GetTracks()
              if isinstance(t, pcbnew.PCB_VIA) and t.GetNetCode() == code]
    for f in board.GetFootprints():
        for p in f.Pads():
            if p.GetNetCode() == code and p.GetAttribute() in (
                    pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                pierce.append((pcbnew.ToMM(p.GetPosition().x),
                               pcbnew.ToMM(p.GetPosition().y)))

    # Площадки самой земли на лице. Они тоже своя медь, и заклёпке позволено
    # заходить на них ПЛОЩАДКОЙ — цепь одна, замыкать нечего. Нельзя другое:
    # сверлить сквозь площадку детали, иначе её нечем будет припаять.
    #
    # Тот кусок, из-за которого это писалось, оказался не таким: заливка у
    # вывода 2 `U501` идёт цельным пятном, площадка лежит ВНУТРИ него, и
    # объединение ничего не меняет (площадь 2.2459 до и после). Дело было в
    # запасе на радиусе, см. `RING`. Объединение оставлено потому, что кольцо
    # вокруг площадки с термозазором — случай реальный, но записано честно:
    # здесь оно не помогло.
    gpads = []
    for f in board.GetFootprints():
        for p in f.Pads():
            if p.GetNetCode() == code and p.IsOnLayer(pcbnew.F_Cu):
                gpads.append(pcbnew.SHAPE_POLY_SET(
                    p.GetEffectivePolygon(pcbnew.F_Cu)))

    zones = list(board.Zones())
    back = pieces(zones, pcbnew.B_Cu, code)
    front = pieces(zones, pcbnew.F_Cu, code)

    added, left = 0, []
    for one in front:
        area = one.Area() / 1e12
        if any(one.Contains(pt(x, y)) for x, y in pierce):
            continue
        bb = one.BBox()
        x1, y1 = pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop())
        x2, y2 = pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom())
        # Кусок вместе со своими площадками — по ним заклёпке ходить можно.
        near = [q for q in gpads if q.Collide(one, 0)]
        whole = pcbnew.SHAPE_POLY_SET(one)
        for q in near:
            whole.BooleanAdd(q)
        best, best_d = None, -1.0
        n = int((x2 - x1) / GRID) + 1
        m = int((y2 - y1) / GRID) + 1
        for i in range(n):
            for j in range(m):
                x, y = x1 + i * GRID, y1 + j * GRID
                if not deep_inside(whole, x, y):
                    continue
                # Сверло — только по заливке, не по площадке детали.
                rd = VIA_DRILL / 2 + 0.05
                if any(q.Contains(pt(x, y))
                       or any(q.Contains(pt(x + dx * rd, y + dy * rd))
                              for dx, dy in DIRS) for q in near):
                    continue
                if not any(deep_inside(b, x, y) for b in back):
                    continue
                d = min(((x - px) ** 2 + (y - py) ** 2 for px, py in pierce),
                        default=1e9)
                if d < HOLE * HOLE:
                    continue        # свёрла столкнутся, медь тут не при чём
                if d > best_d:
                    best, best_d = (x, y), d
        if best is None:
            left.append((area, "заклёпка не влезает",
                         (round((x1 + x2) / 2, 1), round((y1 + y2) / 2, 1))))
            continue
        x, y = best
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pt(x, y))
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
        pierce.append((x, y))
        added += 1

    # Кусок, в который заклёпка не влезла, остаётся висеть в воздухе: медь без
    # связи, у нас это два-три квадратных миллиметра. Для ЛУТ это не пустяк —
    # лишний островок под утюгом и лишний шанс замкнуть соседа перемычкой
    # тонера. Кто не смог получить заклёпку, тот убирается.
    #
    # Убирает сама заливка, режимом «снимать островки», и делать это надо
    # ПОСЛЕ добивки, а не до: до неё островками считаются все пятнадцать
    # кусков, которым заклёпка как раз полагается.
    for z in zones:
        if z.GetNetCode() == code and z.IsOnLayer(pcbnew.F_Cu):
            z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(str(BOARD))
    print(f"кусков заливки на лице: {len(front)}, изнанка одним куском: "
          f"{'да' if len(back) == 1 else f'нет, кусков {len(back)}'}")
    print(f"добито заклёпок по земле: {added}, осталось без связи: {len(left)}")
    for area, why, (x, y) in sorted(left, reverse=True)[:8]:
        print(f"    кусок {area:7.2f} мм² в ({x}, {y}) — {why}")


if __name__ == "__main__":
    main()
