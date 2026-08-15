#!/usr/bin/env python3
"""Контур платы, крепёж и механическая разметка — hw/console/console.kicad_pcb.

Скрипт идемпотентный: при повторном запуске он выкидывает только то, что рисует
сам (Edge.Cuts, User.Drawings, крепёжные отверстия `H*`), и кладёт заново.
Всё остальное — импортированные из схемы корпуса, дорожки, полигоны — не
трогает. UUID детерминированные, считаются от содержимого объекта, поэтому
повторный прогон не даёт diff'а на пустом месте.

Откуда числа:

| | |
|---|---|
| плата 156 x 74 | blocks/10-mech.md §2 |
| панель 84.07 x 54.56, AA 73.44 x 48.96, поле 7.67 со стороны шлейфа | спека панели, `docs/display/ili9488/3.5寸TN_规格书_ILI9488_40PIN.pdf`, стр. 4 |
| кнопка PTS645 6 x 6, шаг в кресте 7.5, крест в 17.5 от края | плата v1, `~/dev/risc-v/wch/kicad/console/console/console.kicad_pcb` |
| банка 60 x 45 x 3 | живая деталь, обмер — blocks/10-mech.md §4 |

Запуск:  python3 hw/console/tools/pcb01_outline.py
"""
import math
import re
import tempfile
import uuid
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"
NS = uuid.UUID("6f1d2c14-0b4e-5a71-9c3d-1e2f3a4b5c6d")

# ---------------------------------------------------------------- геометрия

# Начало платы на листе. Левый верхний угол — сюда же ставится вспомогательный
# ноль, чтобы все координаты, которыми мы оперируем в документах, совпадали с
# тем, что покажет KiCad и что уедет в сверловку.
OX, OY = 50.0, 40.0

BOARD_W, BOARD_H = 156.0, 74.0
CORNER_R = 3.0

PANEL_W, PANEL_H = 84.07, 54.56
AA_W, AA_H = 73.44, 48.96
AA_MARGIN_TAIL = 7.67          # поле от торца со шлейфом до видимой области

# Панель по центру платы, шлейф выходит из её ЛЕВОГО торца (06-display.md §7.2)
PANEL_X = (BOARD_W - PANEL_W) / 2
PANEL_Y = (BOARD_H - PANEL_H) / 2
AA_X = PANEL_X + AA_MARGIN_TAIL
AA_Y = PANEL_Y + (PANEL_H - AA_H) / 2

# Кадр NES 256 x 240 в видимой области 480 x 320 — целочисленного увеличения
# нет, берём вписывание по меньшей стороне (src/console/src/nes/screen.rs)
NES_K = min(480 / 256, 320 / 240)
NES_W = 256 * NES_K / 480 * AA_W
NES_H = 240 * NES_K / 320 * AA_H

# Зона под банку на изнанке: 60 вдоль платы, 45 поперёк. Длинной стороной
# вдоль — иначе сверху и снизу остаётся по 7 мм и полосы под разводку
# становятся бесполезны (обсуждение 15.08.2026).
#
# Правая половина, а не середина: F133 обязан сидеть рядом с `J601`, то есть
# около x = 58, и центральная зона нужна ему с обвязкой. Банка уходит вправо и
# накрывает изнанку правого креста — кнопки на лице, ей это не мешает.
BATT_W, BATT_H = 60.0, 45.0
BATT_X, BATT_Y = 92.0, 15.0

# Крепёж M2: четыре по углам и два посередине длинных сторон — 156 мм с
# вырезом под панель посередине иначе играет.
# Нижний средний сдвинут с 78 на 102: на 78 он попадал ровно туда, где
# розетка USB-C должна пролезть между площадками SELECT и START на лице.
HOLES = [(5.0, 5.0), (78.0, 5.0), (151.0, 5.0),
         (5.0, 69.0), (102.0, 69.0), (151.0, 69.0)]
HOLE_FP = "MountingHole_2.2mm_M2"
HOLE_LIB = "/usr/share/kicad/footprints/MountingHole.pretty"

# Кнопки: кресты слева и справа, SELECT/START под панелью. Числа с платы v1,
# кроме y у SELECT/START — там пришлось опустить, см. заметку ниже.
ARM = 7.5
CLUSTERS = [(17.5, 37.0), (138.5, 37.0)]
EXTRA = [(66.0, 69.0), (90.0, 69.0)]


def mm(v):
    return pcbnew.FromMM(v)


def pt(x, y):
    return pcbnew.VECTOR2I(mm(OX + x), mm(OY + y))


# ---------------------------------------------------------------- рисование

def seg(board, layer, width, a, b):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetLayer(layer)
    s.SetStart(pt(*a))
    s.SetEnd(pt(*b))
    s.SetWidth(mm(width))
    board.Add(s)
    return s


def arc(board, layer, width, center, a, b):
    """Дуга через три точки: концы и середина, посчитанная от центра."""
    cx, cy = center
    ax, ay = a
    bx, by = b
    a0 = math.atan2(ay - cy, ax - cx)
    a1 = math.atan2(by - cy, bx - cx)
    # короткая дуга
    d = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
    r = math.hypot(ax - cx, ay - cy)
    am = a0 + d / 2
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_ARC)
    s.SetLayer(layer)
    s.SetArcGeometry(pt(ax, ay),
                     pt(cx + r * math.cos(am), cy + r * math.sin(am)),
                     pt(bx, by))
    s.SetWidth(mm(width))
    board.Add(s)
    return s


def rect(board, layer, width, x, y, w, h):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_RECT)
    s.SetLayer(layer)
    s.SetStart(pt(x, y))
    s.SetEnd(pt(x + w, y + h))
    s.SetWidth(mm(width))
    s.SetFilled(False)
    board.Add(s)
    return s


def circle(board, layer, width, x, y, r):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_CIRCLE)
    s.SetLayer(layer)
    s.SetCenter(pt(x, y))
    s.SetEnd(pt(x + r, y))
    s.SetWidth(mm(width))
    s.SetFilled(False)
    board.Add(s)
    return s


def text(board, layer, x, y, s, size=1.5):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s)
    t.SetLayer(layer)
    t.SetPosition(pt(x, y))
    t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
    t.SetTextThickness(mm(size / 7.5))
    board.Add(t)
    return t


def outline(board):
    """Контур со скруглением CORNER_R: четыре отрезка и четыре дуги."""
    w, h, r = BOARD_W, BOARD_H, CORNER_R
    lay, wd = pcbnew.Edge_Cuts, 0.05
    seg(board, lay, wd, (r, 0), (w - r, 0))
    seg(board, lay, wd, (w, r), (w, h - r))
    seg(board, lay, wd, (w - r, h), (r, h))
    seg(board, lay, wd, (0, h - r), (0, r))
    arc(board, lay, wd, (r, r), (0, r), (r, 0))
    arc(board, lay, wd, (w - r, r), (w - r, 0), (w, r))
    arc(board, lay, wd, (w - r, h - r), (w, h - r), (w - r, h))
    arc(board, lay, wd, (r, h - r), (r, h), (0, h - r))


def mechanics(board):
    """Разметка на User.Drawings — то, чего нет в меди, но что держит размещение."""
    lay, wd = pcbnew.Dwgs_User, 0.1

    rect(board, lay, wd, PANEL_X, PANEL_Y, PANEL_W, PANEL_H)
    text(board, lay, PANEL_X + 1.0, PANEL_Y - 1.6,
         "панель 84.07 x 54.56, стекло на 1.4 над платой", 1.2)

    rect(board, lay, wd, AA_X, AA_Y, AA_W, AA_H)
    text(board, lay, AA_X + 1.0, AA_Y + 2.4, "видимая область 73.44 x 48.96", 1.2)

    rect(board, lay, wd, AA_X + (AA_W - NES_W) / 2, AA_Y + (AA_H - NES_H) / 2,
         NES_W, NES_H)
    text(board, lay, AA_X + AA_W / 2 - 12.0, AA_Y + AA_H / 2,
         "кадр NES 256 x 240", 1.2)

    # шлейф выходит из левого торца панели и заворачивается под неё вправо
    seg(board, lay, wd, (PANEL_X, PANEL_Y + PANEL_H / 2 - 10.5),
        (PANEL_X, PANEL_Y + PANEL_H / 2 + 10.5))
    text(board, lay, PANEL_X + 2.0, PANEL_Y + PANEL_H / 2 - 12.0,
         "шлейф 21.05 сюда, контакт 1 сверху, окно разъёма влево", 1.2)

    rect(board, lay, wd, BATT_X, BATT_Y, BATT_W, BATT_H)
    text(board, lay, BATT_X + 1.0, BATT_Y + BATT_H - 1.6,
         "банка 60 x 45 x 3 на изнанке — деталей не ставить", 1.2)

    # кнопки: габарит корпуса и толкатель, чтобы видеть коллизии на глаз
    for cx, cy in CLUSTERS:
        for dx, dy in ((0, -ARM), (0, ARM), (-ARM, 0), (ARM, 0)):
            rect(board, lay, wd, cx + dx - 3.0, cy + dy - 3.0, 6.0, 6.0)
            circle(board, lay, wd, cx + dx, cy + dy, 1.75)
    for ex, ey in EXTRA:
        rect(board, lay, wd, ex - 3.0, ey - 3.0, 6.0, 6.0)
        circle(board, lay, wd, ex, ey, 1.75)


def holes(board):
    for i, (x, y) in enumerate(HOLES, 1):
        fp = pcbnew.FootprintLoad(HOLE_LIB, HOLE_FP)
        if fp is None:
            raise SystemExit(f"не найден футпринт {HOLE_LIB}/{HOLE_FP}")
        fp.SetPosition(pt(x, y))
        fp.SetReference(f"H{i}")
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
        board.Add(fp)


# ------------------------------------------------------------- служебное

# Плата всегда собирается на ЧИСТОЙ доске, а в существующий файл вливается
# текстом. Причина: `board.Remove()` на загруженной плате роняет pcbnew
# сегфолтом при повторном прогоне — SWIG отдаёт владение объектом питону, и
# дальше двойное освобождение. Текстовая склейка от этого свободна и заодно
# гарантирует, что импортированные из схемы корпуса мы не тронем.

TOP = re.compile(r"\n\t\((gr_line|gr_arc|gr_rect|gr_circle|gr_text|footprint)\b")


def blocks(text_):
    """Блоки верхнего уровня: (тип, начало, конец) по балансу скобок."""
    out, i = [], 0
    while True:
        m = TOP.search(text_, i)
        if not m:
            return out
        start = m.start() + 2
        depth, j = 0, start
        while True:
            if text_[j] == "(":
                depth += 1
            elif text_[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append((m.group(1), start, j + 1))
        i = j + 1


def is_ours(kind, block):
    """Наше — графика на Edge.Cuts и User.Drawings и крепёж `H*`."""
    if kind == "footprint":
        return bool(re.search(r'\(property "Reference" "H\d+"', block))
    return bool(re.search(r'\(layer "(Edge\.Cuts|Dwgs\.User)"\)', block))


UUID_RE = re.compile(r'\(uuid "[0-9a-f-]+"\)')


def our_blocks(fresh):
    """Наши блоки из свежесобранной платы: UUID детерминированы, порядок тоже.

    pcbnew выдаёт корпуса в порядке своего контейнера, а не вставки, поэтому
    сортируем сами — иначе повторный прогон переставляет `H1`…`H6` местами.
    """
    out, seen = [], {}
    for kind, a, b in blocks(fresh):
        block = fresh[a:b]
        if not is_ours(kind, block):
            continue
        key = UUID_RE.sub("", block)
        n = seen.get(key, 0)
        seen[key] = n + 1
        salt = f"{key}#{n}"
        parts, prev = [], 0
        for i, m in enumerate(UUID_RE.finditer(block)):
            parts.append(block[prev:m.start()])
            parts.append(f'(uuid "{uuid.uuid5(NS, f"{salt}:{i}")}")')
            prev = m.end()
        parts.append(block[prev:])
        out.append((key, "".join(parts)))
    return [b for _, b in sorted(out)]


def merge(old, fresh):
    """Выкинуть из old наши блоки и подставить взятые из fresh."""
    keep, prev = [], 0
    for kind, a, b in blocks(old):
        if is_ours(kind, old[a:b]):
            keep.append(old[prev:a - 2])       # вместе с «\n\t» перед блоком
            prev = b
    keep.append(old[prev:])
    tail = "".join(keep).rstrip()
    assert tail.endswith(")"), "файл платы не заканчивается скобкой"
    body = "".join("\n\t" + b for b in our_blocks(fresh))
    return tail[:-1].rstrip() + body + "\n)\n"


def settings(board):
    board.SetCopperLayerCount(2)
    d = board.GetDesignSettings()
    d.SetBoardThickness(mm(1.6))
    d.SetAuxOrigin(pcbnew.VECTOR2I(mm(OX), mm(OY)))
    # процесс ЛУТ + ЧПУ-сверловка, обсуждение 15.08.2026: дорожка 0.2,
    # зазор 0.2, заклёпка 0.5 в отверстии и 0.9 площадка
    nc = d.m_NetSettings.GetDefaultNetclass()
    nc.SetTrackWidth(mm(0.2))
    nc.SetClearance(mm(0.2))
    nc.SetViaDiameter(mm(0.9))
    nc.SetViaDrill(mm(0.5))


def main():
    board = pcbnew.CreateEmptyBoard()
    settings(board)
    outline(board)
    mechanics(board)
    holes(board)
    # Сохраняем во временный каталог ВНЕ проекта: pcbnew при сохранении платы
    # пишет рядом с ней и свой `.kicad_pro`, а наш почищен руками и терять его
    # нельзя. В /tmp этот побочный файл уезжает вместе с каталогом.
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / "fresh.kicad_pcb"
        board.Save(str(tmp))
        fresh = tmp.read_text()

    # шапку (слои, setup) берём из существующего файла, если он есть: там
    # могли поменять правила DRC руками, затирать это нельзя
    BOARD.write_text(merge(BOARD.read_text() if BOARD.exists() else fresh, fresh))
    print(f"контур {BOARD_W} x {BOARD_H}, скругление {CORNER_R}, "
          f"крепёж {len(HOLES)} x M2 -> {BOARD}")


if __name__ == "__main__":
    main()
