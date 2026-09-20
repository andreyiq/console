#!/usr/bin/env python3
"""Rip-up: выдрать мешающую дорожку и переложить её вместе со своей.

Зачем. Все перестановки очереди и все правки размещения дают ±2 связи — плата
стоит на 191 независимо ни от чего (замеры в `pcb02_place.py`, `pcb04_fine.py`,
`block08_decoupling.py`). Так и должно быть, когда мешает ОДНОВРЕМЕННАЯ
теснота: кто бы ни лёг первым, он займёт коридор, а второму его не хватит.
Порядком это не лечится, лечится только тем, что первого просят подвинуться.

Разведка перед постройкой (`scratchpad/blockers.py`): если разрешить проходить
сквозь чужую медь за штраф, путь находится у ВСЕХ 103 несошедшихся связей, и у
53 из них мешают всего одна-две чужие цепи. То есть выдирать почти всегда
придётся мало.

Как устроено. Ни одной строчки трассировщика не переписано: выдираем медь
прямо с платы и зовём `pcb10_route.py` на список цепей. Он умеет класть поверх
уже лежащей меди — своя помечена `locked`, и он её не трогает, а считает
готовыми кусками цепи.

Приёмка — по числу, а не по надежде: недостающие связи считаются до и после, и
если стало не лучше, плата возвращается из копии. Поэтому шаг безопасен: он не
может ухудшить результат, худшее — потратит время.

Померено на живой плате, кругами подряд:

| круг | не хватает связей | попыток | помогло | время |
|---|---|---|---|---|
| до rip-up | 74 | — | — | — |
| первый (одним вызовом) | 66 -> 58 | 31 | 4 | 12 мин |
| второй (своя цепь первой) | 58 -> 49 | 40 | 7 | 18 мин |

Для сравнения: добор (`pcb11_more.py`) за 10 минут убирает 4…5 связей и
дальше упирается, а все правки размещения и очереди дают ±2 (замеры в
`pcb02_place.py`, `pcb04_fine.py`, `block08_decoupling.py`). Rip-up —
единственное, что сдвинуло плату по-настоящему, и это ожидаемо: мешает
ОДНОВРЕМЕННАЯ теснота, а её порядком не лечат.

Гонять кругами, пока помогает.

Запуск:  python3 hw/console/tools/pcb13_rip.py [сколько попыток]
"""
import collections
import heapq
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# Свои аргументы забираем ДО того, как чистим `sys.argv`: `pcb10_route` читает
# его при импорте и принял бы наше число за имя цепи. Пока читали после, число
# попыток молча всегда было равно значению по умолчанию.
ARGS = sys.argv[1:]
sys.argv = [sys.argv[0]]

import pcbnew                                          # noqa: E402
import pcb10_route as R                                # noqa: E402

SOFT = 40.0          # цена шага сквозь чужую медь, в клетках пути

# Сколько чужих цепей выдираем за раз. Чем больше, тем больше кандидатов, но
# тем меньше шанс, что все выдранные лягут обратно: каждая следующая ищет себе
# место в плате, где уже нет её прежней дорожки.
#
# Порог 2 исчерпывается за три круга (кандидатов 45 -> 40 -> 17), а разбор
# `pcb13_blockers.py` говорит, что ещё 26 связей держат ровно три цепи.
MAX_BLOCK = int(os.environ.get("PCB_RIP_BLOCK", 2))
TRIES = int(ARGS[0]) if ARGS else 40
DEADLINE = float(os.environ.get("PCB_RIP_DEADLINE", 2400))


# ------------------------------------------------------------ чтение платы

def scan(board):
    """Площадки, медь и связность — в том виде, в каком их ждёт `pcb10_route`."""
    name_of = {n: board.FindNet(n).GetNetname()
               for n in range(board.GetNetCount()) if board.FindNet(n)}
    pads, vias, wires, edges = [], [], [], {}
    by_net = collections.defaultdict(list)
    for f in board.GetFootprints():
        for p in f.Pads():
            c = p.GetNetCode()
            pads.append((c, R.pad_box(p)))
            if name_of.get(c):
                q = p.GetPosition()
                by_net[c].append((pcbnew.ToMM(q.x) - R.OX,
                                  pcbnew.ToMM(q.y) - R.OY))
    for t in board.GetTracks():
        c = t.GetNetCode()
        if isinstance(t, pcbnew.PCB_VIA):
            q = t.GetPosition()
            vx, vy = pcbnew.ToMM(q.x) - R.OX, pcbnew.ToMM(q.y) - R.OY
            vias.append((c, vx, vy))
            edges.setdefault(c, []).append(
                (R.to_cell(vx, vy) + (0,), R.to_cell(vx, vy) + (1,)))
            continue
        a, b = t.GetStart(), t.GetEnd()
        ax, ay = pcbnew.ToMM(a.x) - R.OX, pcbnew.ToMM(a.y) - R.OY
        bx, by = pcbnew.ToMM(b.x) - R.OX, pcbnew.ToMM(b.y) - R.OY
        L = 0 if t.GetLayer() == pcbnew.F_Cu else 1
        wires.append((c, ax, ay, bx, by, L, pcbnew.ToMM(t.GetWidth())))
        edges.setdefault(c, []).append(
            (R.to_cell(ax, ay) + (L,), R.to_cell(bx, by) + (L,)))
    return name_of, pads, vias, wires, edges, by_net


def pieces_of(code, pts, edges):
    """Куски цепи: {номер -> (клетки, набор индексов площадок)}."""
    cells = [R.to_cell(*q) for q in pts]
    of_cell, of_root = R.components(edges.get(code, ()))

    def piece(cell):
        r = of_cell.get(cell + (0,)) or of_cell.get(cell + (1,))
        return set(of_root[r]) if r else {cell + (0,)}

    groups, gid = {}, 0
    for idx in range(len(pts)):
        p = piece(cells[idx])
        hit = None
        for k, (gc, _) in groups.items():
            if (gc & p) or any(cells[idx] + (L,) in gc for L in (0, 1)):
                hit = k
                break
        if hit is None:
            groups[gid] = (set(p), {idx})
            gid += 1
        else:
            groups[hit][0].update(p)
            groups[hit][1].add(idx)
    return cells, groups


def missing(board):
    """Сколько связей НЕ хватает на плате. Земля не в счёт — она идёт заливкой."""
    name_of, _, _, _, edges, by_net = scan(board)
    n = 0
    for code, pts in by_net.items():
        if len(pts) < 2 or name_of.get(code) == "GND":
            continue
        _, groups = pieces_of(code, pts, edges)
        n += len(groups) - 1
    return n


# ----------------------------------------------------------- мягкий поиск

def soft_route(g, starts, goals, net, margin):
    """A* как в `pcb10_route.route`, но сквозь чужую медь — за штраф `SOFT`.

    Нужен не чтобы проложить, а чтобы СПРОСИТЬ: чья медь мешает. Поэтому
    спорные клетки (−1) остаются стеной — за ними не видно, кого двигать.
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
        best[(i, j, L)] = 0.0
        heapq.heappush(heap, (h(i, j), 0.0, (i, j, L), None))
    budget = 400000
    while heap:
        budget -= 1
        if budget < 0:
            return None
        _, cost, cur, parent = heapq.heappop(heap)
        if cur in seen:
            continue
        seen[cur] = parent
        i, j, L = cur
        if (i, j) in goal and L == 0:
            path, key = [], cur
            while key is not None:
                path.append(key)
                key = seen[key]
            return path[::-1]
        for di, dj, w in R.DIRS:
            ni, nj = i + di, j + dj
            if not (lo_i <= ni <= hi_i and lo_j <= nj <= hi_j):
                continue
            if not (0 <= ni < R.NX and 0 <= nj < R.NY):
                continue
            o = g.own[L][g.idx(ni, nj)]
            if o == -1:
                continue
            extra = 0.0 if o in (0, net) else SOFT
            if di and dj and extra == 0.0 and not g.free_diag(ni, nj, L, net):
                continue
            nc = cost + w * (R.BACK_COST if L else 1.0) + extra
            key = (ni, nj, L)
            if key in best and best[key] <= nc:
                continue
            best[key] = nc
            heapq.heappush(heap, (nc + h(ni, nj), nc, key, cur))
        if g.can_via(i, j, net):
            nc = cost + R.VIA_COST
            key = (i, j, 1 - L)
            if not (key in best and best[key] <= nc):
                best[key] = nc
                heapq.heappush(heap, (nc + h(i, j), nc, key, cur))
    return None


# ---------------------------------------------------------------- выдирание

def drop_tracks(board, codes):
    """Снять с платы всю НЕЗАКРЕПЛЁННУЮ и закреплённую медь этих цепей.

    Лучи веера тоже закреплены, и их снимать нельзя — иначе цепь останется без
    выхода из-под корпуса. Отличаем по длине: луч короче 2.5 мм и начинается на
    площадке; проще и надёжнее — по тому, что `pcb07_fanout.py` кладёт лучи
    ТОЛЬКО на лице и только одним отрезком от центра площадки.
    """
    pad_at = set()
    for f in board.GetFootprints():
        for p in f.Pads():
            if p.GetNetCode() in codes:
                q = p.GetPosition()
                pad_at.add((round(pcbnew.ToMM(q.x), 2),
                            round(pcbnew.ToMM(q.y), 2)))
    gone = 0
    for t in list(board.GetTracks()):
        if t.GetNetCode() not in codes:
            continue
        if isinstance(t, pcbnew.PCB_VIA):
            board.RemoveNative(t)
            gone += 1
            continue
        a, b = t.GetStart(), t.GetEnd()
        ends = {(round(pcbnew.ToMM(a.x), 2), round(pcbnew.ToMM(a.y), 2)),
                (round(pcbnew.ToMM(b.x), 2), round(pcbnew.ToMM(b.y), 2))}
        if ends & pad_at:
            continue                     # луч веера — не наш
        board.RemoveNative(t)
        gone += 1
    return gone


def reroute(mine, blockers):
    """Переложить: СПЕРВА свою цепь, потом выдранные.

    Двумя вызовами, а не одним, и это не мелочь. `pcb10_route.py` внутри
    вызова сортирует цепи по длине связи, поэтому в одном вызове выдранные
    успевали лечь раньше моей и снова занимали тот самый коридор — ровно то,
    ради чего их и выдирали. Замер: одним вызовом помогало 4 попытки из 31.
    """
    for names in ([mine], blockers):
        r = subprocess.run(
            [sys.executable, str(HERE / "pcb10_route.py")] + names,
            capture_output=True, text=True)
        if r.returncode != 0:
            return False
    return True


# --------------------------------------------------------------------- круг

def main():
    board_path = R.BOARD
    backup = board_path.with_suffix(".rip-bak")
    t0 = time.monotonic()

    base = missing(pcbnew.LoadBoard(str(board_path)))
    print(f"до rip-up не хватает связей: {base}", flush=True)
    rounds = 0
    while True:
        rounds += 1
        before = base
        base = one_round(board_path, backup, base, t0, rounds)
        if base >= before:
            print(f"rip-up исчерпан: кругов {rounds}, "
                  f"не хватает связей {base}")
            return
        if time.monotonic() - t0 > DEADLINE:
            print(f"rip-up остановлен ВРЕМЕНЕМ, а не предметом: "
                  f"кругов {rounds}, не хватает связей {base}")
            return


def one_round(board_path, backup, base, t0, rounds):
    """Один проход по кандидатам. Возвращает, сколько связей не хватает после.

    Круги нужны потому, что после каждой удачной подвижки кандидаты другие:
    медь легла иначе, и держат уже не те. А вот КРУГ, не давший ничего, значит
    исчерпание — приём детерминированный, и повторять его незачем. Померено:
    круги 5…8 дали по 18 кандидатов и ноль успехов каждый, то есть двадцать
    четыре минуты впустую. Поэтому останов здесь, а не у того, кто зовёт.
    """
    board = pcbnew.LoadBoard(str(board_path))
    name_of, pads, vias, wires, edges, by_net = scan(board)

    g = R.build(board, pads, vias, [], wires)

    # Кандидаты: несошедшиеся пары и кто их держит.
    cand = []
    for code, pts in sorted(by_net.items()):
        if len(pts) < 2 or name_of.get(code) == "GND":
            continue
        cells, groups = pieces_of(code, pts, edges)
        if len(groups) < 2:
            continue
        keys = sorted(groups)
        for ia, a in enumerate(keys):
            for b in keys[ia + 1:]:
                path = soft_route(g, sorted(groups[a][0]),
                                  [cells[i] for i in groups[b][1]],
                                  code, R.MARGIN)
                if path is None:
                    continue
                blk = {g.own[L][g.idx(i, j)] for i, j, L in path}
                blk = {o for o in blk if o > 0 and o != code}
                if 0 < len(blk) <= MAX_BLOCK:
                    cand.append((len(blk), name_of[code],
                                 sorted(name_of[o] for o in blk)))
                break
    cand.sort()
    print(f"кандидатов на выдирание: {len(cand)} "
          f"(держат не больше {MAX_BLOCK} чужих цепей)", flush=True)

    won, lost, n = 0, 0, 0
    for k, mine, blockers in cand:
        if n >= TRIES:
            print(f"  упёрлись в потолок {TRIES} попыток")
            break
        if time.monotonic() - t0 > DEADLINE:
            print(f"  упёрлись во ВРЕМЯ ({DEADLINE / 60:.0f} мин), "
                  f"а не в предмет")
            break
        n += 1
        shutil.copy2(board_path, backup)
        b = pcbnew.LoadBoard(str(board_path))
        codes = {c for c, nm in name_of.items() if nm in blockers}
        gone = drop_tracks(b, codes)
        b.Save(str(board_path))
        ok = reroute(mine, blockers)
        now = missing(pcbnew.LoadBoard(str(board_path))) if ok else base + 1
        if ok and now < base:
            print(f"  {n:3}. {mine:14} ← подвинули {', '.join(blockers):24} "
                  f"снято {gone:3} отрезков: не хватает {base} -> {now}",
                  flush=True)
            base = now
            won += 1
        else:
            shutil.copy2(backup, board_path)
            lost += 1
    backup.unlink(missing_ok=True)
    print(f"  круг {rounds}: попыток {n}, помогло {won}, откачено {lost}; "
          f"не хватает связей {base}, прошло "
          f"{(time.monotonic() - t0) / 60:.0f} мин", flush=True)
    return base


if __name__ == "__main__":
    main()
