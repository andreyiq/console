#!/usr/bin/env python3
"""Точное размещение там, где документы блоков требуют конкретного соседства.

`pcb03_blocks.py` раскладывает блок ровными рядами в зоне — этого достаточно,
чтобы разобрать кучу, но недостаточно там, где место детали задано физикой:
конденсатор развязки принадлежит **конкретному выводу**, `CIN` обязан стоять
между `IN` и `GND` своего бака, делитель `FB` — подальше от `LX`.

Что делает этот скрипт:

| | откуда требование |
|---|---|
| 27 конденсаторов развязки кольцом вокруг F133, каждый у своего вывода | 08-decoupling.md §6.2, §6.3, §6.4 |
| три бака: `CIN` у `IN`, дроссель у `LX`, `COUT` за дросселем, делитель на стороне `FB` | даташит SY8089 §2.4 Layout Design пп. 1–4, через 02-power.md §6.1 |
| кварцы вплотную к своим выводам, нагрузочные конденсаторы у кварца | 07-clock-reset.md §6.1 |
| `CC`-резисторы и `USBLC6` у самой розетки | 03-usb.md §6.2 |

Скрипт двигает только перечисленное поимённо и блокирует поставленное, чтобы
`pcb03` его больше не трогал. Запускать после `pcb03_blocks.py`.
"""
import itertools
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"

OX, OY = 50.0, 40.0
GAP = 0.6                      # просвет между courtyard соседей
MAX_ROWS = 2                   # сколько рядов допускаем в кольце развязки

# ------------------------------------------------------- развязка по выводам
#
# Соответствие «конденсатор — вывод F133» восстановлено из block08_decoupling.py:
# там `rails()` идёт по таблице RAILS и нумерует C801… подряд, по одному
# 0.1 мкФ на вывод плюс объёмный на рельсу.
DECOUP = {
    # +3V3: объём и по выводам 34, 66, 83, 128
    "C801": "83", "C802": "34", "C803": "66", "C804": "83", "C805": "128",
    # +1V8: объём и выводы 20, 26, 48, 49, 50, 65
    "C806": "50", "C807": "20", "C808": "26", "C809": "48", "C810": "49",
    "C811": "50", "C812": "65",
    # +0V9: объём и выводы 46, 51, 81, 116, 117
    "C813": "116", "C814": "46", "C815": "51", "C816": "81", "C817": "116",
    "C818": "117",
    "C819": "29",                    # 2.2 мкФ на входе внутренних LDO
    "C820": "77",                    # VCC-TVOUT
    "C821": "28", "C822": "30",      # выходы LDOA / LDOB
    "C823": "89",                    # объём аналога
    "C824": "89", "C825": "97",      # AVCC / HPVCC
    "C826": "92", "C827": "90",      # опоры VRA1 / VRA2
}
# Объёмные ставим во внешний ряд: они не про высокочастотную развязку, а про
# запас заряда, и место у самого вывода дороже отдать керамике 0.1 мкФ.
BULK = {"C801", "C806", "C813", "C823"}

# ------------------------------------------------------------------- баки
#
# Смещения от центра SY8089, в миллиметрах. `IN` и `FB` у SOT-23-5 на одной
# стороне корпуса, `LX` и `GND` на другой — отсюда и раскладка: вход справа,
# силовой выход влево, обратная связь вниз-вправо, подальше от `LX`.
BUCKS = [
    # U,    L,    CIN,  COUT, C0.1, Rверх, Rниз, перемычка, TP,   C22p
    ("U2", "L1", "C1", "C4", "C7", "R1", "R2", "R16", "TP1", None),
    ("U3", "L2", "C2", "C5", "C8", "R3", "R4", "R17", "TP2", None),
    ("U4", "L3", "C3", "C6", "C9", "R5", "R6", "R18", "TP3", "C10"),
]
# Три канала в нижней полосе, под корпусом: выводы питания F133 после
# переворота смотрят вниз, и рельсы приходят к ним снизу, никого не огибая.
#
# Порядок слева направо — 3.3 / 1.8 / 0.9 В, и он не случаен. Выход бака стоит
# в кластере слева (за дросселем), поэтому чем правее сам бак, тем короче путь
# его рельсы до корпуса. Ближе всех ставим 0.9 В: у него самый большой ток
# (0.6 А, 02-power.md §7.1) и самый тесный допуск — просадка 20 мВ на трёх
# вольтах это ничто, а на девяти десятых уже заметно.
BUCK_AT = [(20.0, 62.0), (41.0, 62.0), (62.0, 62.0)]
# Смещения от центра SY8089. У SOT-23-5 `EN`, `GND` и `LX` на одной стороне
# корпуса, `IN` и `FB` на другой — отсюда раскладка: вход и обратная связь
# справа, силовой выход влево. Делитель уходит вниз-вправо, то есть в сторону
# `FB` и подальше от `LX`, как требует даташит §2.4 п. 4.
BUCK_OFF = {
    "CIN":  (4.5, 0.0, 90),      # вплотную к IN
    "L":    (-4.5, 0.0, 0),      # у LX
    "COUT": (-9.0, 0.0, 90),
    "C01":  (-9.0, 4.4, 90),
    "RT":   (4.5, 4.4, 90),      # делитель — на стороне FB
    "RB":   (4.5, 9.0, 90),
    "JMP":  (8.5, 0.0, 90),      # 0 Ω перемычка на выходе рельсы
    "TP":   (-4.5, 5.0, 0),
    "C22":  (8.5, 4.4, 90),
}


def mm(v):
    return pcbnew.FromMM(v)


def xy(fp):
    p = fp.GetPosition()
    return pcbnew.ToMM(p.x) - OX, pcbnew.ToMM(p.y) - OY


def size(fp):
    """Габарит courtyard **при нулевом повороте**.

    Мерить как есть нельзя: скрипт сам поворачивает детали, и на втором прогоне
    у повёрнутого конденсатора ширина с высотой меняются местами. Кольцо от
    этого разъезжается и детали налезают друг на друга — так и было, пока не
    стали снимать габарит на нуле и возвращать поворот обратно.
    """
    # Кэш courtyard пересчитываем явно: после `SetPosition` KiCad помечает
    # его недействительным, но не строит заново, и `GetCourtyard()` отдаёт
    # пустой полигон. Проверка тогда молча пропускает деталь и рапортует
    # ноль — ровно так подсунутая внахлёст пара конденсаторов не нашлась.
    fp.BuildCourtyardCaches()
    was = fp.GetOrientation()
    fp.SetOrientationDegrees(0)
    lay = pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd
    cy = fp.GetCourtyard(lay)
    bb = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False, False)
    w, h = pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())
    fp.SetOrientation(was)
    return w, h


def put(board, ref, x, y, rot, lock=True):
    fp = board.FindFootprintByReference(ref)
    if fp is None:
        return None
    if not fp.IsFlipped():
        fp.Flip(fp.GetPosition(), False)
    fp.SetOrientationDegrees(rot)
    fp.SetPosition(pcbnew.VECTOR2I(mm(OX + x), mm(OY + y)))
    fp.SetLocked(lock)
    return fp


# ------------------------------------------------------------------ кольцо

def ring(board):
    """Конденсаторы развязки — каждый напротив своего вывода.

    Выводов на стороне много, а конденсатор шире шага выводов, поэтому кольцо
    получается в несколько рядов: деталь садится в первый ряд, где её интервал
    вдоль стороны свободен, иначе уходит дальше от корпуса. Так конденсатор
    остаётся против своего вывода, а не уезжает к середине стороны.
    """
    u1 = board.FindFootprintByReference("U1")
    cx, cy = xy(u1)
    pads = {p.GetNumber(): (pcbnew.ToMM(p.GetPosition().x) - OX,
                            pcbnew.ToMM(p.GetPosition().y) - OY)
            for p in u1.Pads()}

    # сторона вывода и оси: n — наружу, t — вдоль стороны
    def side(pin):
        px, py = pads[pin]
        if px < cx - 7:
            return "L"
        if px > cx + 7:
            return "R"
        return "T" if py < cy else "B"

    AXIS = {"L": (-1, 0), "R": (1, 0), "T": (0, -1), "B": (0, 1)}
    groups = {"L": [], "R": [], "T": [], "B": []}
    for ref, pin in DECOUP.items():
        if pin in pads:
            groups[side(pin)].append((ref, pin))

    half = 8.25 + 0.4          # край площадок корпуса плюс зазор
    placed = 0
    for s, items in groups.items():
        nx, ny = AXIS[s]
        vertical = s in ("L", "R")            # вдоль стороны идёт Y
        # объёмные — в конец очереди, они уедут в дальний ряд
        items.sort(key=lambda it: (it[0] in BULK,
                                   pads[it[1]][1] if vertical else pads[it[1]][0]))
        rows = []                              # правый край занятого в каждом ряду
        for ref, pin in items:
            fp = board.FindFootprintByReference(ref)
            if fp is None:
                continue
            w, h = size(fp)
            # длинной стороной наружу: так деталь занимает вдоль стороны
            # минимум и влезает больше штук в один ряд
            rot = 90 if not vertical else 0
            along, radial = (h, w) if True else (w, h)
            t = pads[pin][1] if vertical else pads[pin][0]
            lo = t - along / 2
            # Рядов не больше двух. Третий ряд уводит конденсатор на 4.5 мм
            # дальше от вывода, чем второй, — а смысл развязки именно в том,
            # чтобы петля тока была короткой. Если в двух рядах на уровне
            # своего вывода места нет, лучше сдвинуть деталь вдоль стороны,
            # чем отодвинуть от корпуса.
            for k in range(MAX_ROWS):
                if k == len(rows):
                    rows.append(-1e9)
                if rows[k] + GAP <= lo:
                    break
            else:
                k = min(range(len(rows)), key=lambda i: rows[i])
                lo = rows[k] + GAP
            t = lo + along / 2
            rows[k] = lo + along
            r = half + radial / 2 + k * (radial + GAP)
            px = cx + nx * r if not vertical else cx + nx * r
            py = cy + ny * r if not vertical else t
            if vertical:
                px, py = cx + nx * r, t
            else:
                px, py = t, cy + ny * r
            put(board, ref, px, py, rot)
            placed += 1
    return placed


# -------------------------------------------------------------------- баки

def bucks(board):
    """Три канала: CIN у IN, дроссель у LX, делитель на стороне FB."""
    n = 0
    for (u, l, cin, cout, c01, rt, rb, jmp, tp, c22), (bx, by) in zip(BUCKS, BUCK_AT):
        put(board, u, bx, by, 0)
        for ref, key in ((cin, "CIN"), (l, "L"), (cout, "COUT"), (c01, "C01"),
                         (rt, "RT"), (rb, "RB"), (jmp, "JMP"), (tp, "TP"),
                         (c22, "C22")):
            if ref is None:
                continue
            dx, dy, rot = BUCK_OFF[key]
            if put(board, ref, bx + dx, by + dy, rot):
                n += 1
        n += 1
    return n


# ------------------------------------------------------- кварцы и USB

def crystals(board):
    """Кварц вплотную к своим выводам, нагрузочные конденсаторы — у кварца.

    Выводы такта (20…30) после переворота корпуса выходят влево, на x = 50.4,
    поэтому кварцы стоят слева от кольца развязки, а не где придётся.
    """
    n = 0
    for ref, x, y, rot in (("Y1", 30.0, 32.0, 0), ("Y2", 30.0, 42.0, 0),
                           ("C15", 24.0, 30.0, 0), ("C16", 24.0, 34.0, 0),
                           ("C17", 24.0, 40.0, 0), ("C18", 24.0, 44.0, 0)):
        if put(board, ref, x, y, rot):
            n += 1
    return n


def usb(board):
    """CC-резисторы и защита — вплотную к розетке (03-usb.md §6.2)."""
    n = 0
    for ref, x, y, rot in (("R301", 81.0, 57.0, 0), ("R302", 81.0, 60.0, 0),
                           ("D301", 96.0, 58.5, 0)):
        if put(board, ref, x, y, rot):
            n += 1
    return n


def overlaps(board):
    for f in board.GetFootprints():
        f.BuildCourtyardCaches()
    boxes = []
    for f in board.GetFootprints():
        for lay in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
            c = f.GetCourtyard(lay)
            if c.OutlineCount():
                boxes.append((f.GetReference(), lay, c.BBox()))
    return [f"{a}~{b}" for (a, l1, x), (b, l2, y) in itertools.combinations(boxes, 2)
            if l1 == l2 and x.Intersects(y)]


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    print(f"  развязка кольцом вокруг F133: {ring(board)}")
    print(f"  баки с обвязкой:              {bucks(board)}")
    print(f"  кварцы и их конденсаторы:     {crystals(board)}")
    print(f"  обвязка USB у розетки:        {usb(board)}")
    board.Save(str(BOARD))
    bad = overlaps(board)
    print("пересечений courtyard:", len(bad), " ".join(bad[:14]))


if __name__ == "__main__":
    main()
