#!/usr/bin/env python3
"""Вывод сигналов из-под мелкого шага — короткие лучи от площадок наружу.

Эту часть трассировщик не делает вовсе. Freerouting 2.1.0 роняет свой поиск
пути исключением `MazeSearchAlgo.expand_to_target_doors` каждый раз, когда
целью оказывается площадка с шагом 0.4 мм: в логе десятки `NullPointerException`
за проход, а на плате — **ни одной дорожки, подошедшей к чипу**, сколько бы
проходов он ни сделал. Проверено счётом: из 128 площадок медь пришла к нулю.

Поэтому из-под корпуса выводим сами, и это ровно та часть работы, которую
машине отдавать и не стоило: геометрия здесь жёсткая и считается на бумаге.

Как считается. Площадки 0.23 при шаге 0.4, то есть между соседними 0.17 мм —
дорожке там не пройти никогда. Значит каждый сигнал идёт строго наружу, по
своей оси: до соседней площадки остаётся 0.185 (послабление записано в
`console.kicad_dru`), между соседними лучами — ровно 0.2, наш общий зазор.

Лучи кончаются **через один** на двух радиусах. Иначе их концы стоят тем же
частоколом 0.4 мм, и подойти к концу сбоку так же нельзя, как к самой
площадке; в шахматном порядке у каждого конца соседи по своему ряду в 0.8 мм.

Трёх радиусов не делаем, хотя расчёт за них: 1.2 мм между концами вместо 0.8
позволили бы поставить на конце каждого луча переходную (0.7 меди плюс 0.2
зазора с двух сторон — нужно 1.1). Померено: с тремя радиусами связей не
сходится 195 против 177 при двух. Лучи становятся длиннее и съедают то самое
место у корпуса, ради которого всё и затевалось, а переходные трассировщик
всё равно ставит там, где ему удобно, а не на их концах.

Если прямо наружу не влезает — луч пробует уйти наискось, отклоняя конец в
сторону. Прямая тяга сразу к площадке соседнего конденсатора тоже пробовалась
и отвергнута: выходит одна связь и два пересечения, потому что путь идёт через
чужие лучи.

Длину луч выбирает себе сам. Конденсаторы развязки стоят вплотную к выводам —
так и задумано (`08-decoupling.md`), — и упереться в чужую площадку луч может
уже на первом миллиметре. Поэтому он укорачивается, пока не разойдётся со всем
чужим; если не расходится и на минимуме, сигнал остаётся без луча и достаётся
трассировщику как есть.

Ставится до `pcb07_dsn.py`: тот выгружает лучи в задание неприкосновенной
медью, и freerouting подхватывает их концы.
"""
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"

# Кому нужен вывод. Мерка простая: дорожка 0.2 с зазорами занимает 0.6 мм, и
# при шаге площадок мельче этого между ними не пройти — площадка замурована
# соседями, и добраться до неё не может ни один трассировщик, ни наш, ни
# чужой. Под мерку попадают трое: F133 (шаг 0.4), разъём шлейфа дисплея
# (0.5) и розетка USB-C (0.5). У разъёмов это 36 площадок из 41.
CHIPS = ("U1", "J601", "J301")

# Куда выводить принудительно. У разъёма шлейфа площадки стоят справа от
# корпуса, к чипу (`pcb02_place.py`, поворот 270°), и лучи — вправо, прямо к
# чипу. (До исправления перед заказом площадки стояли слева и лучи шли под
# корпус; без принудительной стороны шина тогда сходилась на шесть связей из
# тридцати шести.)
FORCED = {"J601": (1, 0)}
WIDTH = 0.2
OUT = (1.3, 2.1)              # насколько луч выходит за край площадки, мм
LEAST = 0.4                   # короче — не имеет смысла, лучше отдать как есть
KEEP = 0.2                    # зазор до чужой меди, общее правило платы
CHIP_KEEP = 0.15              # у площадок F133 — послабление из .kicad_dru
SKEW = (0.0, 0.3, -0.3, 0.6, -0.6)    # отклонение конца вбок, мм


def mm(v):
    return pcbnew.FromMM(v)


def clear(board):
    """Снять всю медь — и дорожки, и переходные. Это первый шаг разводки.

    Переходные тоже, и это важнее, чем кажется. Сшивка земли от прошлого круга
    остаётся на плате помехой, а ставится она по свободному полю, то есть
    каждый раз по-разному. Пока она переживала пересборку, один и тот же
    прогон давал то 177 несошедшихся связей, то 206 — и разница была не в том,
    что мы правили, а в том, где вчера легли заклёпки. Возвращает их
    `pcb06_planes.py` после разводки.
    """
    doomed = list(board.GetTracks())
    for t in doomed:
        board.RemoveNative(t)
    return len(doomed)


def obstacles(board, chips):
    """Площадки платы: прямоугольник, цепь и мерка зазора до неё.

    Мерок две. Всем — общий зазор платы. Площадкам самого F133 — послабление
    из `console.kicad_dru`: у этого корпуса разрешено 0.15, иначе луч не выйдет
    и по своей оси. Без них в списке луч наискось садился на соседний вывод
    чипа, а проверка молчала, потому что чип был исключён целиком.

    Сравнение по ссылке, а не по объекту: pcbnew отдаёт на каждой итерации
    новую обёртку, и `f is chip` не совпадает никогда.
    """
    out = []
    for f in board.GetFootprints():
        own = f.GetReference() in chips
        gap = CHIP_KEEP if own else KEEP
        for p in f.Pads():
            bb = p.GetBoundingBox()
            out.append((bb.GetLeft(), bb.GetTop(), bb.GetRight(),
                        bb.GetBottom(), p.GetNetCode(), mm(WIDTH / 2 + gap)))
    return out


def crosses(x1, y1, x2, y2, segs):
    """Задевает ли отрезок уже проложенные лучи.

    Мерка — расстояние между осями: `WIDTH + KEEP`, то есть 0.4 мм. Ровно
    столько между соседними лучами по построению, поэтому берём волосок
    меньше, иначе каждый луч считает соседа помехой и веер не строится вовсе.
    """
    dx, dy = x2 - x1, y2 - y1
    steps = max(1, int((dx * dx + dy * dy) ** 0.5 / mm(0.1)))
    lim = (mm(WIDTH + KEEP) - mm(0.005)) ** 2
    for i in range(steps + 1):
        px, py = x1 + dx * i // steps, y1 + dy * i // steps
        for ax, ay, bx, by in segs:
            ux, uy = bx - ax, by - ay
            n = ux * ux + uy * uy
            s = 0.0 if n == 0 else max(0.0, min(1.0, ((px - ax) * ux
                                                      + (py - ay) * uy) / n))
            qx, qy = ax + s * ux, ay + s * uy
            if (px - qx) ** 2 + (py - qy) ** 2 < lim:
                return True
    return False


def clashes(x1, y1, x2, y2, net, boxes):
    """Мешает ли отрезку чужая площадка.

    Идём по отрезку шагом в десятую миллиметра и смотрим каждую точку. Взять
    габаритный прямоугольник всего отрезка было бы вдесятеро проще и вдесятеро
    грубее: у косого отрезка он захватывает всё вокруг, и прямая связь до
    соседнего конденсатора не проходила ни разу.
    """
    dx, dy = x2 - x1, y2 - y1
    steps = max(1, int((dx * dx + dy * dy) ** 0.5 / mm(0.1)))
    for i in range(steps + 1):
        px, py = x1 + dx * i // steps, y1 + dy * i // steps
        for bx1, by1, bx2, by2, code, half_w in boxes:
            if code == net:
                continue
            if (bx1 - half_w < px < bx2 + half_w
                    and by1 - half_w < py < by2 + half_w):
                return True
    return False


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    parts = [board.FindFootprintByReference(r) for r in CHIPS]
    parts = [f for f in parts if f is not None]

    pads = []
    for chip in parts:
        cx, cy = chip.GetPosition().x, chip.GetPosition().y
        # Куда «наружу». У корпуса с четырьмя сторонами — от середины, у
        # однорядного разъёма — поперёк ряда. Разница не косметическая: у
        # крайних площадок ряда «от середины» указывает ВДОЛЬ него, прямо в
        # соседа, луч упирается и не ставится вовсе. Так у разъёма шлейфа без
        # вывода осталось восемнадцать площадок — вся шина дисплея.
        xs = [p.GetPosition().x for p in chip.Pads()]
        ys = [p.GetPosition().y for p in chip.Pads()]
        span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
        row = None
        if span_x > 3 * span_y:
            row = "x"                      # ряд идёт по X, наружу — по Y
        elif span_y > 3 * span_x:
            row = "y"
        for p in chip.Pads():
            net = p.GetNetname()
            bb = p.GetBoundingBox()
            if bb.GetWidth() > mm(2.0) and bb.GetHeight() > mm(2.0):
                continue                  # термопад, ему лучи не нужны
            if not net or net == "GND" or net.startswith("unconnected-"):
                continue
            pads.append((p, cx, cy, row))

    boxes = obstacles(board, set(CHIPS))
    dropped = clear(board)

    laid = short = twins = 0
    laid_segs = []
    blocked = []
    done_at = set()          # где луч уже стоит: (x, y, цепь)
    pads.sort(key=lambda q: (q[0].GetPosition().x, q[0].GetPosition().y))
    for i, (p, cx, cy, row) in enumerate(pads):
        pos = p.GetPosition()
        dx, dy = pos.x - cx, pos.y - cy
        bb = p.GetBoundingBox()
        # Сторону берём по расстоянию, а не по размеру площадки: `GetSize`
        # отдаёт размер до поворота, и у боковых рядов луч от него уходил
        # поперёк, ложась на соседние площадки.
        forced = FORCED.get(p.GetParentFootprint().GetReference())
        if forced:
            step = forced
            half = (bb.GetWidth() if forced[0] else bb.GetHeight()) / 2
        elif row == "x" or (row is None and abs(dy) >= abs(dx)):
            step = (0, 1 if dy > 0 else -1)
            half = bb.GetHeight() / 2
        else:
            step = (1 if dx > 0 else -1, 0)
            half = bb.GetWidth() / 2
        side = (step[1], step[0])          # поперёк луча
        found = None
        for skew in SKEW:
            want = OUT[i % len(OUT)]
            while want >= LEAST:
                ex = int(pos.x + step[0] * (half + mm(want))
                         + side[0] * mm(skew))
                ey = int(pos.y + step[1] * (half + mm(want))
                         + side[1] * mm(skew))
                if not (clashes(pos.x, pos.y, ex, ey, p.GetNetCode(), boxes)
                        or crosses(pos.x, pos.y, ex, ey, laid_segs)):
                    found = (ex, ey)
                    break
                want -= 0.1
            if found:
                break
        if not found and (pos.x, pos.y, p.GetNetCode()) in done_at:
            # Не «не поместился», а «уже выведен». У реверсивной розетки USB-C
            # площадки `A4`/`B9` и `A9`/`B4` стоят в одной и той же точке: две
            # ориентации кабеля, один контакт на плате. Луч там уже лежит, и
            # второй раз он и не нужен — прежний счёт «129 из 131» был просто
            # двойным учётом двух точек.
            twins += 1
            continue

        if not found:
            # Кто именно не пустил — вопрос не праздный. Помеха от деталей
            # («некуда выйти в принципе») и помеха от уже положенного соседа
            # («место разобрали») лечатся в разных местах: первое — свойство
            # корпуса и должно быть записано в самой детали, второе —
            # размещение. Проверяем самым щедрым вариантом: короткий луч,
            # прямо, без сдвига вбок.
            ex = int(pos.x + step[0] * (half + mm(LEAST)))
            ey = int(pos.y + step[1] * (half + mm(LEAST)))
            why = ("корпус" if clashes(pos.x, pos.y, ex, ey,
                                       p.GetNetCode(), boxes) else "сосед")
            blocked.append((p.GetParentFootprint().GetReference(),
                            p.GetPadName(), p.GetNetname(), why))
            short += 1
            continue
        ex, ey = found

        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pos)
        t.SetEnd(pcbnew.VECTOR2I(ex, ey))
        t.SetWidth(mm(WIDTH))
        t.SetLayer(pcbnew.F_Cu)
        t.SetNet(p.GetNet())
        t.SetLocked(True)                 # метка «луч наш», по ней и снимаем
        board.Add(t)
        laid_segs.append((pos.x, pos.y, ex, ey))
        done_at.add((pos.x, pos.y, p.GetNetCode()))
        laid += 1

    board.Save(str(BOARD))
    print(f"снято прежней меди: {dropped}; лучей выведено: {laid}, "
          f"не поместилось: {short}, площадок в одной точке: {twins}")
    for ref, pin, net, why in blocked:
        print(f"    {ref}-{pin} ({net or '—'}): помешал {why}")


if __name__ == "__main__":
    main()
