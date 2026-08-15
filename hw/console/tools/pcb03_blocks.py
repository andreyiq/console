#!/usr/bin/env python3
"""Черновая раскладка блоков по зонам изнанки платы.

Это **не** финальное размещение. Скрипт делает одно: разбирает кучу, в которую
«Update PCB from Schematic» свалил 155 корпусов, и раскладывает каждый блок в
свою зону аккуратными рядами, не задевая уже посаженную механику. Дальше
размещение внутри блока правится руками — прижать конденсатор к выводу, увести
делитель от `LX` и так далее, по указаниям из документов блоков.

Зоны выбраны по правилу схемы: **блок стоит с той стороны корпуса, откуда
выходят его выводы** (README, «Как устроен лист»). У F133 нумерация идёт
1…32 слева сверху вниз, 33…64 снизу слева направо, 65…96 справа снизу вверх,
97…128 сверху справа налево, поэтому:

| выводы | сторона F133 | блок | зона на плате |
|---|---|---|---|
| 7…19 `PF`, `PC` | слева, верх | 4 хранение | левее и выше чипа |
| 20…30 такт | слева, низ | 7 такт и сброс | левее чипа |
| 31…45 `PE` | слева-низ | 9 отладка | нижний левый угол |
| 46…51 SYS/DRAM | низ, центр | 8 развязка | полосы над и под чипом |
| 52…76 `PD` | низ-право | 6 дисплей | правее чипа |
| 87…100 аудио | право-верх | 5 звук | правее и выше чипа |
| 101…117 питание | верх | 2 питание | верхняя полоса |

Принадлежность детали к блоку берётся из самой схемы — по тому, в какую рамку
попал символ, а не по номеру ссылки: у блока 2 ссылки без сотен (`C1`, `R7`),
и по номеру их от блока 7 не отличить.

Запуск после pcb02_place.py:  python3 hw/console/tools/pcb03_blocks.py
"""
import itertools
import re
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"
SCH = ROOT / "console.kicad_sch"

OX, OY = 50.0, 40.0
GAP = 0.7                      # просвет между courtyard соседей

# Рамки блоков на листе схемы — те же числа, что в tools/scaffold.py
FRAMES = [(1, 20, 20, 218, 130), (2, 226, 20, 448, 130), (3, 456, 20, 574, 130),
          (4, 20, 140, 214, 266), (5, 378, 140, 574, 226), (6, 378, 234, 574, 344),
          (7, 20, 274, 218, 344), (8, 226, 278, 372, 348), (9, 20, 352, 368, 400)]

# Три бака со своей обвязкой — их держим вместе и отдельно от зарядника:
# `CIN`, `L`, делитель и `FB` должны сидеть вплотную к своему SY8089
# (02-power.md §6.1, «Расположение»).
BUCKS = ("U2 L1 C1 C4 C7 R1 R2 R16 TP1 U3 L2 C2 C5 C8 R3 R4 R17 TP2 "
         "U4 L3 C3 C6 C9 R5 R6 R18 TP3 C10").split()

# Секвенсор: две ступени `EN` и подтяжка вниз на узле движка. Держим у самого
# движка — через его контакты идут микроамперы, но узел `PWR_EN` висит в
# воздухе при выключении, и длинную антенну из него делать незачем
# (02-power.md §6.2).
SEQ = "R7 R8 R9".split()

# Зона: (имя, блок, отбор, x1, y1, x2, y2).
#
# План изнанки построен от того, куда после переворота корпуса смотрит каждая
# банка выводов F133 — посчитано по координатам площадок, не по схеме:
#
#   влево  — хранение `PF`/`PC` (7…19) и такт (20…30)
#   вверх  — отладка `PE` (31…45) и половина шины дисплея
#   вправо — вторая половина `PD` и аудио (87…100)
#   вниз   — питание и USB (101…117), все семнадцать
#
# Отсюда: питание и розетка USB внизу, хранение и кварцы слева, звук справа,
# отладка сверху. Середину занимает F133 с кольцом развязки (`pcb04_fine.py`,
# примерно X 43…77, Y 20…54) — в зоны она не входит.
ZONES = [
    ("отладка",     9, None,                      36.0,  1.0,  74.0, 14.0),
    ("дисплей",     6, None,                      79.0, 15.0,  92.0, 40.0),
    ("звук",        5, None,                      79.0, 42.0,  92.0, 54.0),
    ("хранение",    4, None,                       3.0, 20.0,  40.0, 38.0),
    ("такт",        7, None,                       3.0, 46.0,  20.0, 54.0),
    # Секвенсор держим у самого движка: узел `PWR_EN` при выключении висит в
    # воздухе, длинную антенну из него делать незачем (02-power.md §6.2).
    ("секвенсор",   2, lambda r: r in SEQ,         3.0,  7.5,  12.0, 14.5),
    # Баки и зарядник расставляет pcb04_fine.py — тут только то, что осталось.
    ("зарядник",    2, lambda r: r not in BUCKS and r not in SEQ,
                                                 108.0, 55.0, 136.0, 72.0),
    ("USB",         3, None,                      86.0, 55.0, 106.0, 63.0),
    # Остаток блока 8: два 0 Ω аналога, 240 Ω на выводе 47 и контрольные
    # точки — им конкретное соседство не нужно, лишь бы рядом с корпусом.
    ("развязка",    8, None,                      79.0,  2.0,  92.0, 13.0),
]

# Адресных посадок тут больше нет: кварцы и всё, что требует конкретного
# соседства, ставит pcb04_fine.py.
PINNED = []


def mm(v):
    return pcbnew.FromMM(v)


def block_of():
    """ref -> номер блока, по попаданию символа в рамку блока на схеме."""
    s = SCH.read_text()
    out, depth, start, i, blocks = {}, 0, None, 0, []
    while i < len(s):
        if s.startswith("(symbol", i) and depth == 0:
            start, depth = i, 1
            i += 7
            continue
        if depth:
            if s[i] == "(":
                depth += 1
            elif s[i] == ")":
                depth -= 1
                if depth == 0:
                    blocks.append(s[start:i + 1])
        i += 1
    for blk in blocks:
        at = re.search(r"\(at ([\d.-]+) ([\d.-]+) \d+\)", blk)
        rf = re.search(r'\(property "Reference" "([^"]+)"', blk)
        if not at or not rf or rf.group(1).startswith("#"):
            continue
        x, y = float(at.group(1)), float(at.group(2))
        for n, x1, y1, x2, y2 in FRAMES:
            if x1 <= x <= x2 and y1 <= y <= y2:
                out[rf.group(1)] = n
                break
    return out


def size(fp):
    """Габарит courtyard в мм; если его нет — по габаритной рамке.

    Кэш пересчитываем явно: после `SetPosition` KiCad помечает его
    недействительным, но заново не строит, и `GetCourtyard()` отдаёт пустой
    полигон — деталь тогда молча выпадает из проверки на пересечения.
    """
    fp.BuildCourtyardCaches()
    cy = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd)
    bb = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False, False)
    return pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())


def to_back(fp):
    if not fp.IsFlipped():
        fp.Flip(fp.GetPosition(), False)
    fp.SetOrientationDegrees(0)


def pack(board, refs, x1, y1, x2, y2):
    """Разложить рядами слева направо, перенося строку по краю зоны.

    Возвращает список тех, кто не влез, — чтобы это было видно, а не молча
    оказалось друг на друге.
    """
    # Полочная упаковка: сначала высокие, потом мелочь. Иначе первый же ряд
    # задаёт высоту по самой рослой детали и половина зоны уходит в воздух.
    items = []
    for ref in refs:
        fp = board.FindFootprintByReference(ref)
        if fp is None:
            continue
        to_back(fp)
        items.append((ref, fp) + size(fp))
    items.sort(key=lambda t: (-t[3], t[0]))

    cx, cy, row_h, left = x1, y1, 0.0, [r for r in refs
                                        if board.FindFootprintByReference(r) is None]
    for ref, fp, w, h in items:
        if cx + w > x2:                       # перенос строки
            cx, cy, row_h = x1, cy + row_h + GAP, 0.0
        if cy + h > y2:
            left.append(ref)
            continue
        pos = fp.GetPosition()
        bb = (fp.GetCourtyard(pcbnew.B_CrtYd).BBox()
              if fp.GetCourtyard(pcbnew.B_CrtYd).OutlineCount()
              else fp.GetBoundingBox(False, False))
        # сдвигаем так, чтобы левый верхний угол courtyard попал в (cx, cy)
        dx = mm(OX + cx) - bb.GetLeft()
        dy = mm(OY + cy) - bb.GetTop()
        fp.SetPosition(pcbnew.VECTOR2I(pos.x + dx, pos.y + dy))
        cx += w + GAP
        row_h = max(row_h, h)
    return left


def overlaps(board):
    for f in board.GetFootprints():
        f.BuildCourtyardCaches()
    boxes = []
    for f in board.GetFootprints():
        for lay in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
            cy = f.GetCourtyard(lay)
            if cy.OutlineCount():
                boxes.append((f.GetReference(), lay, cy.BBox()))
    out = []
    for (r1, l1, b1), (r2, l2, b2) in itertools.combinations(boxes, 2):
        if l1 == l2 and b1.Intersects(b2):
            out.append(f"{r1}~{r2}")
    return out


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    blk = block_of()
    placed = {f.GetReference() for f in board.GetFootprints() if f.IsLocked()}
    placed |= {r for r, _, _, _ in PINNED}

    for ref, x, y, rot in PINNED:
        fp = board.FindFootprintByReference(ref)
        if fp is None:
            continue
        to_back(fp)
        fp.SetPosition(pcbnew.VECTOR2I(mm(OX + x), mm(OY + y)))
        fp.SetOrientationDegrees(rot)

    left_over, done = [], 0
    used = set()
    for name, n, sel, x1, y1, x2, y2 in ZONES:
        refs = sorted(r for r, b in blk.items()
                      if b == n and r not in placed and r not in used
                      and (sel is None or sel(r)))
        used |= set(refs)
        miss = pack(board, refs, x1, y1, x2, y2)
        left_over += [(name, m) for m in miss]
        done += len(refs) - len(miss)
        print("  %-12s %2d деталей в (%.0f,%.0f)…(%.0f,%.0f)%s"
              % (name, len(refs), x1, y1, x2, y2,
                 "  НЕ ВЛЕЗЛИ: " + " ".join(miss) if miss else ""))

    # Всё, что не влезло в свою зону, паркуем ЗА пределами платы. Раньше
    # парковка стояла в правом нижнем углу — и четыре резистора звука тихо
    # улеглись в зону банки, где деталей быть не должно. Снаружи платы их
    # видно и глазом, и по DRC, а «тихо и правдоподобно» — худший из исходов.
    if left_over:
        pack(board, [r for _, r in left_over], 162.0, 5.0, 200.0, 45.0)

    board.Save(str(BOARD))
    print(f"разложено: {done}")
    rest = sorted(r for r in blk if r not in placed and r not in used)
    if rest:
        print("без зоны остались:", " ".join(rest))
    bad = overlaps(board)
    print("пересечений courtyard:", len(bad), (" ".join(bad[:12]) if bad else ""))


if __name__ == "__main__":
    main()
