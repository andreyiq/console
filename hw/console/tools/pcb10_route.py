#!/usr/bin/env python3
"""Трассировка волновым алгоритмом по сетке — добить остатки за freerouting.

Написан был как основной трассировщик (в KiCad своего нет), но эту работу
делает freerouting и делает лучше — см. `pcb07_dsn.py`. Здесь он остался для
другого: доложить руками те связи, которые freerouting не осилил. Ему давать
список цепей, всё прочее на плате он считает неприкосновенным.

Расклад в нашу пользу: изнанка отдана под сплошную землю (`pcb06_planes.py`),
детали все на лице. Значит сигналы идут **по одному слою**, а под ними всюду
опорная плоскость. Это классический лёгкий случай — но и жёсткий: пересечься
дорожкам негде, и всё, что не разошлось, честно остаётся неразведённым.

Как устроено:

* сетка 0.2 мм — это шаг, на котором наш процесс живёт (дорожка 0.2, зазор
  0.2, 10-mech.md §7);
* занято — площадки чужих цепей, уже проложенные дорожки чужих цепей и поле за
  контуром платы, всё раздутое на зазор;
* заливка `GND` на лице препятствием **не считается**: после трассировки зоны
  переливаются и сами расступаются перед дорожками;
* поиск — A* по восьми направлениям, диагональ дороже прямой, поворот
  штрафуется. Так дорожки выходят прямыми, а не лесенкой;
* порядок — от коротких к длинным: короткая связь почти всегда единственный
  разумный путь, и уступать его длинной незачем.

Скрипт идемпотентный: перед работой снимает все дорожки (переходные и зоны не
трогает) и кладёт заново.
"""
import heapq
import math
import os
import sys
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"

OX, OY = 50.0, 40.0
BOARD_W, BOARD_H = 156.0, 74.0
STEP = 0.2                      # шаг сетки, мм
TRACK = 0.2                     # ширина дорожки
CLEAR = 0.2                     # зазор
EDGE = 0.5                      # отступ меди от реза
PAD = int(math.ceil((TRACK / 2 + CLEAR) / STEP))     # раздутие препятствий

NX = int(BOARD_W / STEP) + 1
NY = int(BOARD_H / STEP) + 1

DIRS = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
        (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142)]
TURN = 0.6                      # штраф за смену направления, в клетках

# Рельсы питания ведём первыми и шире сигналов. Первыми — потому что они
# длинные и разветвлённые, и если сигналы успеют занять коридоры, питанию
# останется обходить через полплаты. Шире — потому что по ним течёт ток:
# 0.3 мм на 35 мкм меди держит около ампера, а у нас максимум 0.6 (§7.1).
# Ширину 0.3 выбрали не наугад: при ней половина дорожки плюс зазор (0.35)
# по-прежнему укладывается в раздутие PAD = 2 клетки, то есть модель занятости
# остаётся честной. Шире — и она начнёт врать.
POWER = {"+3V3", "+1V8", "+0V9", "VSYS", "VBUS", "AVCC", "AGND",
         "VCC-TVOUT", "LDOA-OUT", "LDOB-OUT"}
# Ширину рельсы держим такой же, как у сигнала. Пробовали 0.3: питание от
# этого выиграло (29 несошедшихся связей против 43), но сигналы проиграли
# сильнее — широкая рельса съедает коридор, и в сумме разведённых стало
# меньше. Порядок «питание первым» оставляем, он правильный; ширину добираем
# руками там, где посчитанный ток этого требует (02-power.md §7.1).
POWER_TRACK = 0.3

# Перескок на изнанку. Изнанка отдана под сплошную землю, и каждый перескок в
# ней прорезает щель — поэтому он дорог: переходная стоит как 25 клеток пути,
# а шаг по изнанке втрое дороже шага по лицу. Так маршрут уходит вниз только
# там, где иначе пути нет вовсе, и возвращается при первой возможности.
# Цена перехода на изнанку и цена шага по ней. Прежде стояло 25 и 3: изнанка
# задумывалась сплошной землёй, и уходить туда полагалось в крайнем случае. С
# такой ценой он не уходил туда никогда — шесть переходных на всю плату, — а
# земли на изнанке больше нет, там обычный слой. Теперь дорога, но не заказана.
VIA_COST = float(os.environ.get("PCB_VIA", 12.0))
BACK_COST = float(os.environ.get("PCB_BACK", 1.2))
VIA_PAD_CELLS = 4               # 0.9 мм площадка плюс зазор

# Критичные цепи ведём вне очереди — так делают в индустрии, и по той же
# причине: пара USB, кварцы и опора кодека чувствительны к длине и к соседям,
# и отдавать им остатки коридоров нельзя.
CRITICAL = {"USB0-DP", "USB0-DM", "DXIN", "DXOUT", "X32KIN", "X32KOUT",
            "RESET", "AVCC", "AGND", "VRA1", "VRA2"}

# Потолок раскрытых узлов на одну связь. Без него безнадёжная связь съедает
# минуты, перебирая всю окрестность; с ним она честно объявляется неразведённой
# и уступает время остальным.
NODE_BUDGET = int(os.environ.get("PCB_BUDGET", 60000))

# Бюджет поиска и число проходов — из окружения: на кусте питания нужен
# щедрый бюджет, а на сотне коротких сигналов дешевле быстро сдаться и отдать
# связь дальше, чем перебирать шестьдесят тысяч клеток на каждую.
PASSES = int(os.environ.get("PCB_PASSES", 3))


def mm(v):
    return pcbnew.FromMM(v)


def to_cell(x, y):
    return int(round(x / STEP)), int(round(y / STEP))


def to_mm(i, j):
    return i * STEP, j * STEP


class Grid:
    """Занятость клеток на двух слоях.

    `own[L][k]`: 0 свободно, >0 — клетка принадлежит одной цепи, −1 — на неё
    претендуют две и более, значит закрыта для всех. Слой 0 — лицо, 1 —
    изнанка.

    Тонкость, из-за которой первая версия провалила почти всё: раздутие на
    зазор нельзя считать глухой стеной. Зазор вокруг площадки — это место, куда
    **своей** цепи ходить можно, а чужой нельзя. Пока раздутие блокировало всё
    подряд, дорожка не могла выйти даже из собственной площадки.
    """

    def __init__(self):
        self.own = [[0] * (NX * NY), [0] * (NX * NY)]
        # Реальная медь каждой цепи — площадки и уже проложенные дорожки.
        # Ветка цепи должна стартовать от НЕЁ, а не от ближайшей площадки:
        # иначе питание с 38 площадками растёт змеёй через полплаты вместо
        # аккуратного дерева. Это как раз тот случай, когда одна дорожка,
        # проложенная не оттуда, перекрывает коридор десятку соседей.
        self.copper = {}

    def idx(self, i, j):
        return i * NY + j

    def wall(self, i, j, layer=None):
        for L in ((0, 1) if layer is None else (layer,)):
            self.own[L][self.idx(i, j)] = -1

    def fill_box(self, x1, y1, x2, y2, net, layer=None, pad=PAD):
        i1, j1 = to_cell(x1, y1)
        i2, j2 = to_cell(x2, y2)
        for L in ((0, 1) if layer is None else (layer,)):
            o = self.own[L]
            for i in range(max(0, i1 - pad), min(NX, i2 + pad + 1)):
                for j in range(max(0, j1 - pad), min(NY, j2 + pad + 1)):
                    k = self.idx(i, j)
                    if o[k] == 0:
                        o[k] = net
                    elif o[k] != net:
                        o[k] = -1

    def add_copper(self, i, j, L, net):
        self.copper.setdefault(net, set()).add((i, j, L))

    def free(self, i, j, L, net):
        if not (0 <= i < NX and 0 <= j < NY):
            return False
        o = self.own[L][self.idx(i, j)]
        return o == 0 or o == net

    def can_via(self, i, j, net):
        """Переходная занимает обе стороны и шире дорожки."""
        for L in (0, 1):
            o = self.own[L]
            for di in range(-VIA_PAD_CELLS, VIA_PAD_CELLS + 1):
                for dj in range(-VIA_PAD_CELLS, VIA_PAD_CELLS + 1):
                    ii, jj = i + di, j + dj
                    if not (0 <= ii < NX and 0 <= jj < NY):
                        return False
                    v = o[self.idx(ii, jj)]
                    if v != 0 and v != net:
                        return False
        return True


def pad_box(p):
    bb = p.GetBoundingBox()
    return (pcbnew.ToMM(bb.GetLeft()) - OX, pcbnew.ToMM(bb.GetTop()) - OY,
            pcbnew.ToMM(bb.GetRight()) - OX, pcbnew.ToMM(bb.GetBottom()) - OY)


def build(board, pads, vias, keepouts, wires=()):
    g = Grid()
    # поле за контуром платы
    for i in range(NX):
        for j in range(NY):
            x, y = to_mm(i, j)
            if not (EDGE < x < BOARD_W - EDGE and EDGE < y < BOARD_H - EDGE):
                g.wall(i, j)
    for code, box in pads:
        if code:
            i1, j1 = to_cell(box[0], box[1])
            i2, j2 = to_cell(box[2], box[3])
            for i in range(max(0, i1), min(NX, i2 + 1)):
                for j in range(max(0, j1), min(NY, j2 + 1)):
                    g.add_copper(i, j, 0, code)
        if code == 0:
            # Площадка без цепи — это механика: неметаллизированные отверстия
            # движка, крепёж, установочные штыри. Ноль в модели означает
            # «свободно», поэтому такие площадки надо ставить стеной явно,
            # иначе дорожка проходит прямо сквозь отверстие.
            i1, j1 = to_cell(box[0], box[1])
            i2, j2 = to_cell(box[2], box[3])
            for i in range(max(0, i1 - PAD), min(NX, i2 + PAD + 1)):
                for j in range(max(0, j1 - PAD), min(NY, j2 + PAD + 1)):
                    g.wall(i, j)
        else:
            g.fill_box(*box, code)
    for x1, y1, x2, y2 in keepouts:
        # Зоны запрета живут внутри футпринтов — у `J401` это «No conductive
        # traces» из каталога Hirose, стр. 3, под механикой лотка. Закрываем
        # наглухо: там нельзя вести медь никакой цепи.
        i1, j1 = to_cell(x1, y1)
        i2, j2 = to_cell(x2, y2)
        for i in range(max(0, i1 - PAD), min(NX, i2 + PAD + 1)):
            for j in range(max(0, j1 - PAD), min(NY, j2 + PAD + 1)):
                g.wall(i, j)
    for code, x, y in vias:
        r = 0.45          # переходная 0.9, см. pcb06_planes.py
        g.fill_box(x - r, y - r, x + r, y + r, code)
    for code, x1, y1, x2, y2, L, w in wires:
        # Чужая медь, уже лежащая на плате: лучи из-под F133 и то, что развёл
        # freerouting. Идём по отрезку с шагом в клетку — габаритный
        # прямоугольник у косой дорожки захватывает вчетверо больше места.
        n = max(1, int(math.dist((x1, y1), (x2, y2)) / STEP))
        for k in range(n + 1):
            x = x1 + (x2 - x1) * k / n
            y = y1 + (y2 - y1) * k / n
            g.fill_box(x - w / 2, y - w / 2, x + w / 2, y + w / 2, code, layer=L)
            g.add_copper(*to_cell(x, y), L, code)
    return g


def route(g, starts, goals, net, margin=60):
    """A* от множества стартов к множеству целей. Возвращает путь `(i, j, слой)`.

    Поиск ограничен прямоугольником вокруг концов, раздутым на `margin` клеток.
    Без ограничения волна расходится по всей плате, и одна длинная связь
    считается дольше, чем вся остальная разводка вместе взятая. А обход в
    двенадцать миллиметров — это уже не «обошёл препятствие», это «пути нет».
    """
    goal = set(goals)
    if not starts or not goal:
        return None
    xs = [c[0] for c in list(starts) + list(goals)]
    ys = [c[1] for c in list(starts) + list(goals)]
    lo_i, hi_i = min(xs) - margin, max(xs) + margin
    lo_j, hi_j = min(ys) - margin, max(ys) + margin
    gx = sum(i for i, _ in goals) / len(goals)
    gy = sum(j for _, j in goals) / len(goals)

    def h(i, j):
        dx, dy = abs(i - gx), abs(j - gy)
        return (dx + dy) + (1.4142 - 2) * min(dx, dy)

    best, heap, seen = {}, [], {}
    for c in starts:
        i, j, L = c if len(c) == 3 else (c[0], c[1], 0)
        best[(i, j, L, -1)] = 0.0
        heapq.heappush(heap, (h(i, j), 0.0, (i, j, L), -1, None))
    budget = NODE_BUDGET
    while heap:
        budget -= 1
        if budget < 0:
            return None
        _, cost, cur, d, parent = heapq.heappop(heap)
        if (cur, d) in seen:
            continue
        seen[(cur, d)] = parent
        i, j, L = cur
        if (i, j) in goal and L == 0:
            path, key = [], (cur, d)
            while key is not None:
                path.append(key[0])
                key = seen[key]
            return path[::-1]
        for k, (di, dj, w) in enumerate(DIRS):
            ni, nj = i + di, j + dj
            if not (lo_i <= ni <= hi_i and lo_j <= nj <= hi_j):
                continue
            if not g.free(ni, nj, L, net):
                continue
            nc = cost + w * (BACK_COST if L else 1.0) + (TURN if d != -1 and k != d else 0.0)
            key = ((ni, nj, L), k)
            if key in best and best[key] <= nc:
                continue
            best[key] = nc
            heapq.heappush(heap, (nc + h(ni, nj), nc, (ni, nj, L), k, (cur, d)))
        # перескок на другую сторону
        if g.can_via(i, j, net):
            nc = cost + VIA_COST
            key = ((i, j, 1 - L), -1)
            if not (key in best and best[key] <= nc):
                best[key] = nc
                heapq.heappush(heap, (nc + h(i, j), nc, (i, j, 1 - L), -1, (cur, d)))
    return None


def simplify(path):
    """Сжать путь до изломов и смен слоя — иначе дорожка станет тысячей отрезков."""
    if len(path) < 3:
        return path
    out = [path[0]]
    for a, b, c in zip(path, path[1:], path[2:]):
        if a[2] != b[2] or b[2] != c[2]:
            out.append(b)
        elif (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
            out.append(b)
    out.append(path[-1])
    return out


def lay_rec(out, path, width, code):
    """Записать путь в список (а не на плату): проходов несколько, кладём лучший.

    Номер цепи записывается здесь же. Пока его не было, укладка на плату
    молча не делала ничего — при том что счётчик рапортовал успех.
    """
    pts = simplify(path)
    vias = 0
    for a, b in zip(pts, pts[1:]):
        if a[2] != b[2]:
            out.append(("via", to_mm(a[0], a[1]), None, width, a[2], code))
            vias += 1
        else:
            out.append(("seg", to_mm(a[0], a[1]), to_mm(b[0], b[1]),
                        width, a[2], code))
    return vias


def lay(board, path, net, g, width=TRACK):
    """Положить путь: отрезки по слоям, переходная на каждой смене стороны."""
    pts = simplify(path)
    layer_of = (pcbnew.F_Cu, pcbnew.B_Cu)
    vias = 0
    for a, b in zip(pts, pts[1:]):
        if a[2] != b[2]:
            v = pcbnew.PCB_VIA(board)
            x, y = to_mm(a[0], a[1])
            v.SetPosition(pcbnew.VECTOR2I(mm(OX + x), mm(OY + y)))
            v.SetWidth(mm(0.7))
            v.SetDrill(mm(0.4))
            v.SetNet(net)
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            v.SetLocked(True)
            board.Add(v)
            g.fill_box(x, y, x, y, net.GetNetCode(), pad=VIA_PAD_CELLS)
            vias += 1
            continue
        tr = pcbnew.PCB_TRACK(board)
        x1, y1 = to_mm(a[0], a[1])
        x2, y2 = to_mm(b[0], b[1])
        tr.SetStart(pcbnew.VECTOR2I(mm(OX + x1), mm(OY + y1)))
        tr.SetEnd(pcbnew.VECTOR2I(mm(OX + x2), mm(OY + y2)))
        tr.SetWidth(mm(width))
        tr.SetLayer(layer_of[a[2]])
        tr.SetNet(net)
        board.Add(tr)
    code = net.GetNetCode()
    for i, j, L in path:
        x, y = to_mm(i, j)
        g.fill_box(x, y, x, y, code, layer=L)
        g.add_copper(i, j, L, code)
    return vias


def main():
    board = pcbnew.LoadBoard(str(BOARD))

    # Сначала СНИМАЕМ данные, потом трогаем плату. Повторный `LoadBoard` в том
    # же процессе pcbnew не переживает — отдаёт сырой SwigPyObject, — а
    # `Remove` ломает контейнеры. Поэтому один проход: собрали, почистили,
    # разложили.
    wanted = set(sys.argv[1:])
    pads, vias, by_net = [], [], {}
    for f in board.GetFootprints():
        for p in f.Pads():
            code = p.GetNetCode()
            pads.append((code, pad_box(p)))
            name = p.GetNetname()
            if not name or name == "GND" or name.startswith("unconnected"):
                continue
            pos = p.GetPosition()
            by_net.setdefault((code, name), []).append(
                (pcbnew.ToMM(pos.x) - OX, pcbnew.ToMM(pos.y) - OY))
    wires, mine, seen_copper = [], [], {}
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            pos = t.GetPosition()
            vias.append((t.GetNetCode(),
                         pcbnew.ToMM(pos.x) - OX, pcbnew.ToMM(pos.y) - OY))
            continue
        name = t.GetNetname()
        if wanted and name in wanted and not t.IsLocked():
            mine.append(t)          # своё прежнее — снимем и проложим заново
            continue
        a, b = t.GetStart(), t.GetEnd()
        wires.append((t.GetNetCode(),
                      pcbnew.ToMM(a.x) - OX, pcbnew.ToMM(a.y) - OY,
                      pcbnew.ToMM(b.x) - OX, pcbnew.ToMM(b.y) - OY,
                      0 if t.GetLayer() == pcbnew.F_Cu else 1,
                      pcbnew.ToMM(t.GetWidth())))
        # Своя медь прошлого захода — это СОЕДИНЁННОЕ, а не только помеха.
        # Пока она числилась одной помехой, каждый следующий заход тянул
        # вторую дорожку от той же площадки рядом с первой: меди вчетверо
        # больше, неподключённых столько же.
        seen_copper.setdefault(t.GetNetCode(), set()).update(
            (to_cell(pcbnew.ToMM(a.x) - OX, pcbnew.ToMM(a.y) - OY),
             to_cell(pcbnew.ToMM(b.x) - OX, pcbnew.ToMM(b.y) - OY)))

    keepouts = []
    for z in list(board.Zones()) + [z for f in board.GetFootprints()
                                    for z in f.Zones()]:
        if z.GetIsRuleArea():
            bb = z.GetBoundingBox()
            keepouts.append((pcbnew.ToMM(bb.GetLeft()) - OX,
                             pcbnew.ToMM(bb.GetTop()) - OY,
                             pcbnew.ToMM(bb.GetRight()) - OX,
                             pcbnew.ToMM(bb.GetBottom()) - OY))

    # Объекты цепей берём ДО любых правок платы. После `Remove` контейнеры
    # pcbnew портятся, и `FindNet` начинает отдавать сырой SwigPyObject —
    # прогон падал ровно на этом, уже проложив половину дорожек.
    netobj = {name: board.FindNet(name) for _, name in by_net}
    codeobj = {code: netobj[name] for code, name in by_net}

    for t in mine:
        board.RemoveNative(t)

    g = build(board, pads, vias, keepouts, wires)

    # Порядок — от коротких цепей к длинным: у короткой связи путь чаще всего
    # единственный разумный, и уступать его длинной незачем.
    tasks = []
    for (code, name), pts in by_net.items():
        if len(pts) < 2:
            continue
        if wanted and name not in wanted:
            continue
        span = max(math.dist(a, b) for a in pts for b in pts)
        # Порядок — строго по длине связи, без предпочтения питанию. Пробовали
        # вести рельсы первыми: питанию стало лучше (29 несошедшихся связей
        # против 43), но сигналам заметно хуже, и в сумме разведённых стало
        # меньше — 104 против 119. Длинная разветвлённая рельса, проложенная
        # первой, режет плату надвое и отбирает коридоры у всех остальных.
        tasks.append((span, code, name, pts))
    tasks.sort()

    # Проходы «неудачники идут первыми». Настоящий rip-up выдирает мешающие
    # дорожки и кладёт их иначе; здесь беднее, но по духу то же — цепь, которой
    # не хватило коридора, в следующем проходе выбирает первой.
    order_bonus = set()
    best_state = None
    for attempt in range(PASSES):
        g = build(board, pads, vias, keepouts, wires)
        laid = []
        done = fail = nvias = 0
        failed = []
        ordered = sorted(tasks, key=lambda tk: (
            0 if tk[2] in CRITICAL else (1 if tk[2] in order_bonus else 2), tk[0]))
        for span, code, name, pts in ordered:
            net = netobj[name]
            width = POWER_TRACK if name in POWER else TRACK
            cells = [to_cell(*q) for q in pts]
            g.copper.setdefault(code, set())
            rest = list(range(1, len(pts)))
            connected = {0}
            # Соединённое ведём отдельно от «меди вообще». В меди у цепи лежат
            # все её площадки сразу, и если стартовать от неё, цель оказывается
            # достигнутой в ноль шагов: путь пустой, счётчик растёт, на плату
            # не ложится ничего. Разводка «удавалась» полностью и не давала
            # ни одной дорожки.
            own = set(seen_copper.get(code, ())) | {cells[0]}
            while rest:
                rest.sort(key=lambda r: min(math.dist(pts[r], pts[c])
                                            for c in connected))
                r = rest.pop(0)
                path = route(g, sorted(own), [cells[r]], code, margin=60)
                if path:
                    nvias += lay_rec(laid, path, width, code)
                    for i, j, L in path:
                        x, y = to_mm(i, j)
                        g.fill_box(x, y, x, y, code, layer=L)
                        g.add_copper(i, j, L, code)
                        own.add((i, j))
                    own.add(cells[r])
                    done += 1
                else:
                    fail += 1
                    failed.append(name)
                connected.add(r)
        print(f"  проход {attempt + 1}: проложено {done}, не удалось {fail}, "
              f"переходных {nvias}")
        if best_state is None or done > best_state[0]:
            best_state = (done, fail, nvias, laid, list(failed))
        order_bonus = set(failed)
        if fail == 0:
            break

    done, fail, nvias, laid, failed = best_state
    for kind, a, b, width, layer, code in laid:
        net = codeobj[code]
        if kind == "via":
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(pcbnew.VECTOR2I(mm(OX + a[0]), mm(OY + a[1])))
            v.SetWidth(mm(0.7))
            v.SetDrill(mm(0.4))
            v.SetNet(net)
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            v.SetLocked(True)
            board.Add(v)
        else:
            tr = pcbnew.PCB_TRACK(board)
            tr.SetStart(pcbnew.VECTOR2I(mm(OX + a[0]), mm(OY + a[1])))
            tr.SetEnd(pcbnew.VECTOR2I(mm(OX + b[0]), mm(OY + b[1])))
            tr.SetWidth(mm(width))
            tr.SetLayer(pcbnew.F_Cu if layer == 0 else pcbnew.B_Cu)
            tr.SetNet(net)
            tr.SetLocked(True)     # наше — задание отдаёт это неприкосновенным
            board.Add(tr)

    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    board.Save(str(BOARD))
    print(f"проложено связей: {done}, не удалось: {fail}, переходных {nvias}; "
          f"отрезков на плату: {sum(1 for k, *_ in laid if k == 'seg')}")
    if failed:
        import collections
        top = collections.Counter(failed).most_common(10)
        print("  не разошлись:", ", ".join(f"{k}×{v}" for k, v in top))


if __name__ == "__main__":
    sys.exit(main())
