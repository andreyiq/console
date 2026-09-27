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
import collections
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


#
# Заклёпки, которые ставятся не «одна на кусок», а у конкретного вывода.
# Горячая петля понижающего: даташит SY8089 (Layout Design, п. 2) — «CIN
# вплотную к IN и GND, площадь петли CIN–GND минимальна». У SOT-23-5 IN и LX
# на одной стороне корпуса, GND посередине другой, и землю ёмкости с землёй
# микросхемы на лице не свести: между ними всегда либо LX, либо EN. Обычный
# ответ двухслойной платы — заклёпка в плоскость у каждой из двух земель, и
# петля замыкается через изнанку под корпусом, а не вокруг ячейки.
REQUIRED = [("C1", "2"), ("U2", "2"), ("C2", "2"), ("U3", "2"),
            ("C3", "2"), ("U4", "2"),
            # Усилитель класса D (третья ревизия): его ток питания — те же
            # импульсы 250 кГц, и возврат от развязки `C504` должен идти в
            # плоскость у самой ёмкости. Добивка иначе ставит единственную
            # заклёпку куска посреди поля, в 14 мм от усилителя.
            ("C504", "1")]

# Под корпусами микросхем и кварцев заклёпок нет. Под SOIC и кварцем зазор
# до платы ~0.1 мм, и головка проволочки не даст корпусу лечь. Заклёпки
# термопадов F133 и TP4056 — другое дело: они задуманы, ставятся до посадки
# и расклёпываются заподлицо (`pcb06_planes.py`, 10-mech.md §7). Вторая
# ревизия: добивка поставила заклёпку земли под флешкой, между её рядами.
BODY_PREFIX = ("U", "Y")
BODIES = []          # courtyard корпусов, мм; заполняет `main`


def fed_pieces(board, code, front, pierce):
    """Номера кусков, у которых связь с изнанкой уже есть — свою или через
    соседей, сшитых с ними земляными дорожками.

    Прежде кусок считался связанным, только если заклёпка стоит в нём самом.
    Вторая ревизия: земля флешки выведена дорожкой к земле кварца, где
    заклёпка есть, DRC видит 0 несоединённых, а добивка твердила «кусок без
    связи» и искала место под корпусом. Касание дорожки проверяем по точкам
    вдоль неё: концы лежат на площадках, а площадка в полигон заливки не
    входит.
    """
    n = len(front)
    root = list(range(n))

    def find(a):
        while root[a] != a:
            root[a] = root[root[a]]
            a = root[a]
        return a

    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA) or t.GetNetCode() != code \
                or t.GetLayer() != pcbnew.F_Cu:
            continue
        a, b = t.GetStart(), t.GetEnd()
        steps = max(2, int(pcbnew.ToMM(t.GetLength()) / 0.2) + 1)
        hit = set()
        for k in range(steps + 1):
            p = pcbnew.VECTOR2I(int(a.x + (b.x - a.x) * k / steps),
                                int(a.y + (b.y - a.y) * k / steps))
            for i, o in enumerate(front):
                if o.Contains(p) or o.Collide(p, int(t.GetWidth() / 2)):
                    hit.add(i)
        hit = sorted(hit)
        for i in hit[1:]:
            ra, rb = find(hit[0]), find(i)
            if ra != rb:
                root[ra] = rb
    fed = {find(i) for i, o in enumerate(front)
           if any(o.Contains(pt(x, y)) for x, y in pierce)}
    return {i for i in range(n) if find(i) in fed}


def under_body(x, y):
    return any(x1 < x < x2 and y1 < y < y2 for x1, y1, x2, y2 in BODIES)
REACH = 2.0          # дальше — уже не «у вывода»


def required(board, gnd, front, back, gpads, pierce):
    """Заклёпка у каждого вывода из `REQUIRED`: ближайшее законное место."""
    added, miss = 0, []
    for ref, num in REQUIRED:
        f = board.FindFootprintByReference(ref)
        if f is None:
            continue
        p = [q for q in f.Pads() if q.GetNumber() == num][0]
        px = pcbnew.ToMM(p.GetPosition().x)
        py = pcbnew.ToMM(p.GetPosition().y)
        one = next((o for o in front if o.Collide(p.GetPosition(), 0)), None)
        if one is None:
            miss.append(f"{ref}.{num}: площадка не в заливке")
            continue
        near = [q for q in gpads if q.Collide(one, 0)]
        whole = pcbnew.SHAPE_POLY_SET(one)
        for q in near:
            whole.BooleanAdd(q)
        rd = VIA_PAD / 2 + 0.1
        best, best_d = None, None
        k = int(REACH / GRID)
        for i in range(-k, k + 1):
            for j in range(-k, k + 1):
                x, y = px + i * GRID, py + j * GRID
                d = (x - px) ** 2 + (y - py) ** 2
                if d > REACH * REACH or (best_d is not None and d >= best_d):
                    continue
                if not deep_inside(whole, x, y) or under_body(x, y):
                    continue
                if any(q.Contains(pt(x, y))
                       or any(q.Contains(pt(x + dx * rd, y + dy * rd))
                              for dx, dy in DIRS) for q in near):
                    continue
                if not any(deep_inside(b, x, y) for b in back):
                    continue
                if any((x - vx) ** 2 + (y - vy) ** 2 < HOLE * HOLE
                       for vx, vy in pierce):
                    continue
                best, best_d = (x, y), d
        if best is None:
            miss.append(f"{ref}.{num}: в {REACH} мм нет места")
            continue
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pt(*best))
        v.SetWidth(mm(VIA_PAD))
        v.SetDrill(mm(VIA_DRILL))
        v.SetNet(gnd)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        v.SetLocked(True)
        board.Add(v)
        pierce.append(best)
        added += 1
    return added, miss


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
    for f in board.GetFootprints():
        if f.GetReference().startswith(BODY_PREFIX):
            f.BuildCourtyardCaches()
            cy = f.GetCourtyard(pcbnew.F_CrtYd)
            if cy.OutlineCount():
                bb = cy.BBox()
                BODIES.append((pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()),
                               pcbnew.ToMM(bb.GetRight()),
                               pcbnew.ToMM(bb.GetBottom())))

    gpads = []
    for f in board.GetFootprints():
        for p in f.Pads():
            if p.GetNetCode() == code and p.IsOnLayer(pcbnew.F_Cu):
                gpads.append(pcbnew.SHAPE_POLY_SET(
                    p.GetEffectivePolygon(pcbnew.F_Cu)))

    # Заходов несколько, пока добавляется хоть что-то. Не для порядка: каждая
    # поставленная заклёпка меняет саму заливку — куски сливаются, границы
    # ползут, и там, где места не было, оно появляется. Померено на живой
    # плате: один заход оставляет три куска без связи, второй по тем же
    # правилам добивает два из трёх. Пока заход был один, эти два выглядели
    # «не влезает» — то есть отчёт называл невозможным то, что возможно на
    # следующем шаге.
    zones = list(board.Zones())
    n_req, miss = required(board, gnd, pieces(zones, pcbnew.F_Cu, code),
                           pieces(zones, pcbnew.B_Cu, code), gpads, pierce)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    print(f"заклёпок у горячих петель баков и усилителя: {n_req} из "
          f"{len(REQUIRED)}")
    for m in miss:
        print(f"    НЕ ПОСТАВЛЕНА: {m} — петля CIN–GND замкнётся вокруг ячейки")

    total, left = 0, []
    for round_no in range(6):
        zones = list(board.Zones())
        back = pieces(zones, pcbnew.B_Cu, code)
        front = pieces(zones, pcbnew.F_Cu, code)
        added, left = place(board, gnd, code, front, back, gpads, pierce)
        total += added
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        if added == 0:
            break
    # Кусок, которому заклёпка не влезла, ещё не приговорён: к нему можно
    # ПОДВЕСТИ ДОРОЖКУ от уже связанной земли. Это не роскошь — в одном из
    # таких кусков сидит земляной вывод усилителя `U501`, то есть без этого
    # у него земля висит в воздухе, а плата едет в производство.
    if left:
        bridged = bridge(board, code, left)
        if bridged:
            pcbnew.ZONE_FILLER(board).Fill(board.Zones())
            left = [x for x in left if x[2] not in bridged]

    zones = list(board.Zones())
    front = pieces(zones, pcbnew.F_Cu, code)
    back = pieces(zones, pcbnew.B_Cu, code)
    board.Save(str(BOARD))
    print(f"кусков заливки на лице: {len(front)}, изнанка одним куском: "
          f"{'да' if len(back) == 1 else f'нет, кусков {len(back)}'}")
    print(f"добито заклёпок по земле: {total} за заходов {round_no + 1}, "
          f"осталось без связи: {len(left)}")
    for area, why, (x, y) in sorted(left, reverse=True)[:8]:
        print(f"    кусок {area:7.2f} мм² в ({x}, {y}) — {why}")


def bridge(board, code, left):
    """Подвести дорожку от связанной земли к куску, куда не влезла заклёпка.

    Земля у нас не разводится вовсе — она идёт заливкой, и `pcb10_route.py`
    её нарочно пропускает. Но для ОСТАТКОВ это правило вредит: кусок заливки
    с выводом детали внутри и без связи с плоскостью — это деталь без земли,
    а не косметика.

    Считаем тем же волновым поиском и на той же сетке, что и обычная разводка:
    старт — площадка земли внутри осиротевшего куска, цель — ближайшая
    заклёпка земли, у которой связь с плоскостью есть. Ширину берём 0.4: это
    земля, и лишняя медь ей на пользу.
    """
    import pcb10_route as R10

    pads, vias, wires = [], [], []
    for f in board.GetFootprints():
        for p in f.Pads():
            pads.append((p.GetNetCode(), R10.pad_box(p)))
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            q = t.GetPosition()
            vias.append((t.GetNetCode(), pcbnew.ToMM(q.x) - R10.OX,
                         pcbnew.ToMM(q.y) - R10.OY))
            continue
        a, b = t.GetStart(), t.GetEnd()
        wires.append((t.GetNetCode(),
                      pcbnew.ToMM(a.x) - R10.OX, pcbnew.ToMM(a.y) - R10.OY,
                      pcbnew.ToMM(b.x) - R10.OX, pcbnew.ToMM(b.y) - R10.OY,
                      0 if t.GetLayer() == pcbnew.F_Cu else 1,
                      pcbnew.ToMM(t.GetWidth())))
    g = R10.build(board, pads, vias, [], wires)

    anchors = [R10.to_cell(pcbnew.ToMM(t.GetPosition().x) - R10.OX,
                           pcbnew.ToMM(t.GetPosition().y) - R10.OY)
               for t in board.GetTracks()
               if isinstance(t, pcbnew.PCB_VIA) and t.GetNetCode() == code]
    if not anchors:
        return set()

    gnd = board.FindNet("GND")
    done = set()
    for area, why, (cx, cy) in left:
        # Площадка земли внутри куска — от неё и ведём.
        start = None
        for f in board.GetFootprints():
            for p in f.Pads():
                if p.GetNetCode() != code:
                    continue
                x = pcbnew.ToMM(p.GetPosition().x)
                y = pcbnew.ToMM(p.GetPosition().y)
                if abs(x - cx) < 2.0 and abs(y - cy) < 2.0:
                    start = R10.to_cell(x - R10.OX, y - R10.OY)
                    break
            if start:
                break
        if start is None:
            continue
        near = sorted(anchors, key=lambda c: (c[0] - start[0]) ** 2
                      + (c[1] - start[1]) ** 2)[:12]
        path = R10.route(g, [start + (0,)], near, code,
                         margin=R10.MARGIN, toll=())
        if not path:
            continue
        # Ширина — та, под которую искался путь (`R10.TRACK`). Стояло 0.4 «на
        # пользу земле», а занятость сетки считается под 0.2: мост к куску у
        # `JP1` лёг в 0.1 мм от `RESET`, `REFCLK` и `+1V8` — шесть нарушений
        # зазора в DRC.
        R10.lay(board, path, gnd, g, width=R10.TRACK)
        done.add((cx, cy))
        print(f"    кусок {area:7.2f} мм² в ({cx}, {cy}) — подведена дорожка, "
              f"{len(path)} клеток")
    return done


def place(board, gnd, code, front, back, gpads, pierce):
    """Один заход: заклёпка в каждый кусок, который её примет."""
    added, left = 0, []
    fed = fed_pieces(board, code, front, pierce)
    for idx, one in enumerate(front):
        area = one.Area() / 1e12
        if idx in fed or any(one.Contains(pt(x, y)) for x, y in pierce):
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
        # Кто именно не пустил. Без этого отчёт говорит «не влезает» и на
        # куске 2 мм², и на куске 5 мм², хотя чинить их надо по-разному:
        # тесную медь — разводкой рядом, занятую изнанку — переносом дорожки.
        stop = collections.Counter()
        n = int((x2 - x1) / GRID) + 1
        m = int((y2 - y1) / GRID) + 1
        for i in range(n):
            for j in range(m):
                x, y = x1 + i * GRID, y1 + j * GRID
                if not deep_inside(whole, x, y):
                    stop["мало своей меди"] += 1
                    continue
                if under_body(x, y):
                    stop["под корпусом микросхемы"] += 1
                    continue
                # Сверло не должно ЗАДЕВАТЬ площадку детали — иначе у неё
                # выест середину и паять деталь будет нечем. Радиус ровно
                # сверлa, без запаса: полигон площадки и так огранён в запас, а
                # «на всякий случай» здесь уже дважды стоило связей (см.
                # `RING`). Кромка отверстия вплотную к кромке площадки законна:
                # площадка остаётся целой, цепь у них одна.
                #
                # Здесь, впрочем, это ничего не дало: на всех трёх оставшихся
                # кусках места лежат глубоко внутри площадок, и запас был не
                # при чём. Правило оставлено как верное, а не как полезное.
                #
                # Поправка: мерить надо ПЛОЩАДКОЙ заклёпки, а не сверлом.
                # «Кромка отверстия у кромки площадки» на ЛУТ-плате значит
                # головку проволочки на площадке детали — вывод на неё не лечь.
                # Так легли заклёпки в `C807` и `JP1`. Запас 0.1 — чтобы
                # огранка кругов не пропустила касание.
                rd = VIA_PAD / 2 + 0.1
                if any(q.Contains(pt(x, y))
                       or any(q.Contains(pt(x + dx * rd, y + dy * rd))
                              for dx, dy in DIRS) for q in near):
                    stop["заклёпка легла бы на площадку детали"] += 1
                    continue
                if not any(deep_inside(b, x, y) for b in back):
                    stop["на изнанке в этом месте не плоскость"] += 1
                    continue
                d = min(((x - px) ** 2 + (y - py) ** 2 for px, py in pierce),
                        default=1e9)
                if d < HOLE * HOLE:
                    stop["рядом уже стоит заклёпка"] += 1
                    continue        # свёрла столкнутся, медь тут не при чём
                if d > best_d:
                    best, best_d = (x, y), d
        if best is None:
            # Причина — самая частая среди тех, что не про «мало меди»: клеток
            # вне куска всегда большинство, и они ничего не объясняют.
            real = [(v, k) for k, v in stop.items() if k != "мало своей меди"]
            why = (f"{max(real)[1]} ({max(real)[0]} мест из "
                   f"{sum(v for v, _ in real)})" if real
                   else "кусок уже своей меди для заклёпки")
            left.append((area, why,
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
    return added, left


if __name__ == "__main__":
    main()
