#!/usr/bin/env python3
"""Вывод сигналов из-под F133 — короткие лучи от каждой площадки наружу.

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

Лучи кончаются **через один** на двух разных радиусах. Иначе их концы стоят
тем же частоколом 0.4 мм, и подойти к концу сбоку так же нельзя, как к самой
площадке; в шахматном порядке у каждого конца соседи по своему ряду в 0.8 мм,
и дальше трассировщик работает уже в открытом поле.

Двум десяткам выводов луча не достаётся вовсе: у них конденсатор развязки
стоит ближе, чем самый короткий луч. Тянуть к его площадке напрямую мы
пробовали — выходит одна связь и два пересечения, потому что путь идёт через
чужие лучи. Оставляем их трассировщику как есть.

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

CHIP = "U1"
WIDTH = 0.2
NEAR, FAR = 1.3, 2.1          # насколько луч выходит за край площадки, мм
LEAST = 0.4                   # короче — не имеет смысла, лучше отдать как есть
KEEP = 0.2                    # зазор до чужой меди, общее правило платы


def mm(v):
    return pcbnew.FromMM(v)


def clear(board):
    """Снять прежние лучи — они наши, помечены блокировкой."""
    doomed = [t for t in board.GetTracks()
              if not isinstance(t, pcbnew.PCB_VIA) and t.IsLocked()]
    for t in doomed:
        board.RemoveNative(t)
    return len(doomed)


def foreign(board, chip):
    """Чужие площадки: прямоугольник и цепь. Свои, той же цепи, не мешают.

    Сравнение по ссылке, а не по объекту: pcbnew отдаёт на каждой итерации
    новую обёртку, и `f is chip` не совпадает никогда — чип попадал в список
    чужих сам себе, и все лучи упирались в соседние площадки.
    """
    out = []
    for f in board.GetFootprints():
        if f.GetReference() == chip.GetReference():
            continue
        for p in f.Pads():
            bb = p.GetBoundingBox()
            out.append((bb.GetLeft(), bb.GetTop(), bb.GetRight(),
                        bb.GetBottom(), p.GetNetCode()))
    return out


def clashes(x1, y1, x2, y2, net, boxes, half_w):
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
        for bx1, by1, bx2, by2, code in boxes:
            if code == net:
                continue
            if (bx1 - half_w < px < bx2 + half_w
                    and by1 - half_w < py < by2 + half_w):
                return True
    return False


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    chip = board.FindFootprintByReference(CHIP)
    cx, cy = chip.GetPosition().x, chip.GetPosition().y

    pads = []
    for p in chip.Pads():
        net = p.GetNetname()
        bb = p.GetBoundingBox()
        if bb.GetWidth() > mm(2.0) and bb.GetHeight() > mm(2.0):
            continue                      # термопад, ему лучи не нужны
        if not net or net == "GND" or net.startswith("unconnected-"):
            continue
        pads.append(p)

    boxes = foreign(board, chip)
    dropped = clear(board)

    laid = short = 0
    for i, p in enumerate(sorted(pads, key=lambda q: (q.GetPosition().x,
                                                      q.GetPosition().y))):
        pos = p.GetPosition()
        dx, dy = pos.x - cx, pos.y - cy
        bb = p.GetBoundingBox()
        # Наружу — от середины корпуса. Сторону берём по расстоянию, а не по
        # размеру площадки: `GetSize` отдаёт размер до поворота, и у боковых
        # рядов луч от него уходил поперёк, ложась на соседние площадки.
        if abs(dy) >= abs(dx):
            step = (0, 1 if dy > 0 else -1)
            half = bb.GetHeight() / 2
        else:
            step = (1 if dx > 0 else -1, 0)
            half = bb.GetWidth() / 2
        want = NEAR if i % 2 == 0 else FAR
        margin = mm(WIDTH / 2 + KEEP)
        while want >= LEAST:
            ex = int(pos.x + step[0] * (half + mm(want)))
            ey = int(pos.y + step[1] * (half + mm(want)))
            if not clashes(pos.x, pos.y, ex, ey, p.GetNetCode(), boxes, margin):
                break
            want -= 0.1
        if want < LEAST:
            short += 1
            continue

        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pos)
        t.SetEnd(pcbnew.VECTOR2I(ex, ey))
        t.SetWidth(mm(WIDTH))
        t.SetLayer(pcbnew.F_Cu)
        t.SetNet(p.GetNet())
        t.SetLocked(True)                 # метка «луч наш», по ней и снимаем
        board.Add(t)
        laid += 1

    board.Save(str(BOARD))
    print(f"снято прежних лучей: {dropped}; лучей выведено: {laid}, "
          f"не поместилось: {short}")


if __name__ == "__main__":
    main()
