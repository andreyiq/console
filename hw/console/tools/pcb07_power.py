#!/usr/bin/env python3
"""Раздача питания — дерево по кратчайшим связям, построенное нами.

Питание оказалось второй половиной того, что трассировщик не вытягивает, и по
той же причине, что и выход из-под корпуса: это не одна связь, а куст. У
`+3V3` тридцать восемь площадок на тридцати двух деталях, у `+1V8` шестнадцать,
у `+0V9` двенадцать. Развести такое перебором тяжело даже человеку, а машине
незачем: форма ответа известна заранее — дерево.

Строим его сами, по Приму: на каждом шаге берётся самая короткая связь из уже
собранного в ещё не собранное, и она проверяется на просвет — по чужим
площадкам, по лучам из-под F133 и по всему, что уже положено. Не прошла —
берётся следующая по длине. Совсем не прошло — эта площадка остаётся
трассировщику, дерево от этого не разваливается.

Связь пробуется не только прямой. Прямая между двумя конденсаторами почти
всегда задевает чужой вывод — у соседа земляная площадка стоит вплотную, —
и на одних прямых дерево не строится вовсе: из восьмидесяти связей проходит
десяток. Поэтому дальше идут углы и косые: сперва напрямую, потом через угол
в одну сторону, в другую, потом с косым коленом. Так ходит и человек.

Узлы у выводов чипа — **концы лучей**, а не сами площадки: из-под корпуса они
уже выведены (`pcb07_fanout.py`), и тянуть питание заново в частокол незачем.

Дорожка питания шире сигнальной: 0.3 против 0.2. Ток невелик (сотни
миллиампер на шину), дело в другом — падение на длинной раздаче и в том, что
шире дорожка при домашнем травлении держится вернее.

Ставится после `pcb07_fanout.py` и до `pcb07_dsn.py`.
"""
from pathlib import Path

import pcbnew

from pcb07_fanout import clashes, crosses, mm, obstacles

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"

CHIP = "U1"
WIDTH = 0.3                     # шире сигнальной: раздача длинная
RAILS = ("+3V3", "+1V8", "+0V9", "VSYS", "AVCC", "AGND")
REACH = 40.0                    # длиннее связи не тянем, это уже не дерево


def nodes_of(board, rail, stubs):
    """Точки цепи: площадки деталей, а у чипа — концы его лучей."""
    pts = []
    for f in board.GetFootprints():
        own = f.GetReference() == CHIP
        for p in f.Pads():
            if p.GetNetname() != rail:
                continue
            if own:
                continue                # чип входит в дерево концами лучей
            pos = p.GetPosition()
            pts.append((pos.x, pos.y))
    pts.extend(stubs.get(rail, ()))
    return pts


def stub_ends(board):
    """Концы лучей из-под F133 по цепям — их положил `pcb07_fanout.py`."""
    out = {}
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA) or not t.IsLocked():
            continue
        e = t.GetEnd()
        out.setdefault(t.GetNetname(), []).append((e.x, e.y))
    return out


def shapes(a, b):
    """Варианты пути от a к b: прямая, два угла, два косых колена."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    d = min(abs(dx), abs(dy))
    sx = d if dx > 0 else -d
    sy = d if dy > 0 else -d
    return (
        [a, b],
        [a, (bx, ay), b],
        [a, (ax, by), b],
        [a, (ax + sx, ay + sy), b],
        [a, (bx - sx, by - sy), b],
    )


def tree(pts, ok):
    """Прим по точкам: пути для связей, прошедших проверку `ok`."""
    if len(pts) < 2:
        return []
    done, rest, out = [0], set(range(1, len(pts))), []
    while rest:
        cand = sorted(((pts[i][0] - pts[j][0]) ** 2
                       + (pts[i][1] - pts[j][1]) ** 2, i, j)
                      for i in done for j in rest)
        for _, i, j in cand:
            path = ok(pts[i], pts[j])
            if path:
                out.append((j, path))
                done.append(j)
                rest.discard(j)
                break
        else:
            # ни одна связь не прошла — оставшиеся отдаём трассировщику
            break
    return out


def clear(board):
    """Снять свою прежнюю раздачу — она шире сигнальной, по ней и узнаём."""
    doomed = [t for t in board.GetTracks()
              if not isinstance(t, pcbnew.PCB_VIA) and t.IsLocked()
              and t.GetWidth() == mm(WIDTH)]
    for t in doomed:
        board.RemoveNative(t)
    return len(doomed)


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    dropped = clear(board)
    chip = board.FindFootprintByReference(CHIP)
    boxes = obstacles(board, chip)
    stubs = stub_ends(board)

    segs = []
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA) or not t.IsLocked():
            continue
        a, b = t.GetStart(), t.GetEnd()
        segs.append((a.x, a.y, b.x, b.y))

    laid = skipped = 0
    for rail in RAILS:
        net = board.FindNet(rail)
        if net is None:
            continue
        pts = nodes_of(board, rail, stubs)
        code = net.GetNetCode()
        reach = mm(REACH)

        def ok(a, bb, code=code):
            if ((a[0] - bb[0]) ** 2 + (a[1] - bb[1]) ** 2) ** 0.5 > reach:
                return None
            for path in shapes(a, bb):
                if all(not (clashes(u[0], u[1], v[0], v[1], code, boxes)
                            or crosses(u[0], u[1], v[0], v[1], segs))
                       for u, v in zip(path, path[1:])):
                    return path
            return None

        edges = tree(pts, ok)
        skipped += max(0, len(pts) - 1 - len(edges))
        for _, path in edges:
            for u, v in zip(path, path[1:]):
                if u == v:
                    continue
                t = pcbnew.PCB_TRACK(board)
                t.SetStart(pcbnew.VECTOR2I(*u))
                t.SetEnd(pcbnew.VECTOR2I(*v))
                t.SetWidth(mm(WIDTH))
                t.SetLayer(pcbnew.F_Cu)
                t.SetNet(net)
                t.SetLocked(True)
                board.Add(t)
                segs.append((u[0], u[1], v[0], v[1]))
            laid += 1

    board.Save(str(BOARD))
    print(f"снято прежней раздачи: {dropped}; связей положено: {laid}, "
          f"не прошло просвет: {skipped}")


if __name__ == "__main__":
    main()
