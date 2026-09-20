#!/usr/bin/env python3
"""Кто держит несошедшиеся связи: сколько ЧУЖИХ дорожек надо выдрать.

Это разведка перед постройкой rip-up, а не сам rip-up. Вопрос один: если
разрешить проходить сквозь чужую медь за штраф, скольких соседей придётся
подвинуть? Одного-двух — выдирать стоит; десяток — приём не окупится, и
строить его незачем.

Считаем по УЖЕ РАЗВЕДЁННОЙ плате: для каждой пары, которая не сошлась, ищем
путь в мягком режиме и смотрим, чью медь он пересекает.
"""
import collections
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# `pcb10_route` читает `sys.argv` при импорте и принял бы наши аргументы за
# имена цепей — чистим до импорта.
sys.argv = [sys.argv[0]]

import pcbnew
import pcb10_route as R

SOFT = 40.0        # цена шага сквозь чужую медь, в клетках пути


def soft_route(g, starts, goals, net, margin):
    """A* как в `route`, но сквозь чужую медь — за штраф `SOFT`."""
    import heapq
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
                continue                     # спорная клетка — стена всегда
            extra = 0.0 if o in (0, net) else SOFT
            if di and dj and not g.free_diag(ni, nj, L, net):
                if extra == 0.0:
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


def main():
    board = pcbnew.LoadBoard(str(R.BOARD))
    name_of = {}
    for n in range(board.GetNetCount()):
        ni = board.FindNet(n)
        if ni:
            name_of[n] = ni.GetNetname()

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
        if isinstance(t, pcbnew.PCB_VIA):
            q = t.GetPosition()
            vx, vy = pcbnew.ToMM(q.x) - R.OX, pcbnew.ToMM(q.y) - R.OY
            vias.append((t.GetNetCode(), vx, vy))
            edges.setdefault(t.GetNetCode(), []).append(
                (R.to_cell(vx, vy) + (0,), R.to_cell(vx, vy) + (1,)))
            continue
        a, b = t.GetStart(), t.GetEnd()
        ax, ay = pcbnew.ToMM(a.x) - R.OX, pcbnew.ToMM(a.y) - R.OY
        bx, by = pcbnew.ToMM(b.x) - R.OX, pcbnew.ToMM(b.y) - R.OY
        L = 0 if t.GetLayer() == pcbnew.F_Cu else 1
        wires.append((t.GetNetCode(), ax, ay, bx, by, L,
                      pcbnew.ToMM(t.GetWidth())))
        edges.setdefault(t.GetNetCode(), []).append(
            (R.to_cell(ax, ay) + (L,), R.to_cell(bx, by) + (L,)))

    g = R.build(board, pads, vias, [], wires)

    total, stuck, hist = 0, 0, collections.Counter()
    who = collections.Counter()
    for code, pts in sorted(by_net.items()):
        if len(pts) < 2:
            continue
        # `GND` трассировщик не ведёт — она идёт заливкой, и её сто тридцать
        # четыре несошедшихся куска не имеют отношения к делу. Первый прогон
        # этого не учёл, и вся гистограмма получилась про землю.
        if name_of.get(code) == "GND":
            continue
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
        if len(groups) < 2:
            continue
        keys = sorted(groups)
        for ia, a in enumerate(keys):
            for b in keys[ia + 1:]:
                total += 1
                own = groups[a][0]
                goals = [cells[i] for i in groups[b][1]]
                path = soft_route(g, sorted(own), goals, code, R.MARGIN)
                if path is None:
                    stuck += 1
                    continue
                blk = collections.Counter()
                for i, j, L in path:
                    o = g.own[L][g.idx(i, j)]
                    if o > 0 and o != code:
                        blk[o] += 1
                hist[len(blk)] += 1
                for o in blk:
                    who[name_of.get(o, o)] += 1
    print(f"несошедшихся пар осмотрено: {total}, "
          f"не прошло даже сквозь чужую медь: {stuck}")
    print("сколько ЧУЖИХ цепей держит связь:")
    for k in sorted(hist):
        print(f"  {k:2} цепей — {hist[k]} связей")
    print("кто держит чаще всех:",
          ", ".join(f"{n}×{c}" for n, c in who.most_common(8)))


if __name__ == "__main__":
    main()
