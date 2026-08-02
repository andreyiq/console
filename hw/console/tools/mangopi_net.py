#!/usr/bin/env python3
"""Читает схему MangoPi (PDF) как схему, а не как картинку.

    python3 tools/mangopi_net.py --page 3 --at 520 710      # что в этой точке
    python3 tools/mangopi_net.py --page 3 --trace 520 710   # вся цепь оттуда
    python3 tools/mangopi_net.py --page 3 --label VCC-DRAM  # цепь по метке

Зачем. `docs/mangopi/mq_sch_v1.6.pdf` — вторая по важности опора после
даташита, и читать её глазами по кропам оказалось ненадёжно: дважды подряд
связь определялась «по соседству» и оба раза неверно. Но PDF векторный, и
`pdftocairo -svg` отдаёт точные концы каждого отрезка. Значит соединения можно
не разглядывать, а вычислять.

Как. Провода в этой схеме зелёные, точки соединения — залитые зелёные кружки
того же цвета. Отрезки объединяются в цепь union-find'ом по совпадению концов;
касание конца отрезка к середине другого считается соединением **только** если
там стоит точка. Это ровно то правило, по которому схема и рисовалась.

Текст в PDF настоящий (не кривые), поэтому метки цепей и номера выводов
берутся из `pdftotext -bbox` и привязываются к ближайшему проводу. Текст бывает
повёрнут на 90°, это видно по форме bbox и учитывается.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PDF = ROOT / "docs/mangopi/mq_sch_v1.6.pdf"
CACHE = Path("/tmp/mangopi_net")

WIRE = "55.293274%, 82.351685%, 44.7052%"     # зелёный: провода и точки
PIN = "76.861572%, 38.430786%, 41.960144%"    # красный: выводы и корпуса

TOL = 0.6        # мм-допуск на совпадение концов, в пунктах PDF
LABEL_GAP = 5.5  # насколько близко текст должен лежать к проводу, чтобы быть меткой
END_GAP = 28.0   # имя цепи ставится за концом провода, по его оси;
                 # у символов питания стебель нарисован не проводом, и текст
                 # отходит от зелёного конца на все 21 пункт


def svg(page):
    CACHE.mkdir(exist_ok=True)
    out = CACHE / f"p{page}.svg"
    if not out.exists():
        subprocess.run(["pdftocairo", "-svg", "-f", str(page), "-l", str(page),
                        str(PDF), str(out)], check=True)
    return out.read_text()


def words(page):
    """Слова страницы: (текст, x, y, вертикальный ли)."""
    CACHE.mkdir(exist_ok=True)
    out = CACHE / f"p{page}.bbox"
    if not out.exists():
        out.write_text(subprocess.run(
            ["pdftotext", "-bbox", "-f", str(page), "-l", str(page),
             str(PDF), "-"], capture_output=True, text=True, check=True).stdout)
    res = []
    for m in re.finditer(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" '
            r'yMax="([\d.]+)">([^<]*)</word>', out.read_text()):
        x0, y0, x1, y1 = (float(m.group(i)) for i in range(1, 5))
        res.append((m.group(5), (x0 + x1) / 2, (y0 + y1) / 2,
                    (y1 - y0) > (x1 - x0)))
    return res


def paths(text, colour):
    """Отрезки и точки нужного цвета. Возвращает (segments, dots)."""
    segs, dots = [], []
    for m in re.finditer(r"<path ([^>]*)/>", text):
        a = m.group(1)
        if colour not in a:
            continue
        d = re.search(r'd="([^"]*)"', a).group(1)
        pts = [(round(0.0072 * float(x), 3), round(842 - 0.0072 * float(y), 3))
               for x, y in re.findall(r"([-\d.]+) ([-\d.]+)", d)]
        if not pts:
            continue
        if 'fill="rgb(' + colour in a:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            dots.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
            continue
        for p, q in zip(pts, pts[1:]):
            if near(p, q):
                dots.append(p)      # отрезок нулевой длины — это тоже точка
            else:
                segs.append((p, q))
    return segs, dots


def near(a, b, tol=TOL):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def on_segment(p, seg, tol=TOL):
    """Лежит ли точка на отрезке (включая концы)."""
    (x1, y1), (x2, y2) = seg
    if abs(x1 - x2) <= tol:                       # вертикальный
        return abs(p[0] - x1) <= tol and min(y1, y2) - tol <= p[1] <= max(y1, y2) + tol
    if abs(y1 - y2) <= tol:                       # горизонтальный
        return abs(p[1] - y1) <= tol and min(x1, x2) - tol <= p[0] <= max(x1, x2) + tol
    dx, dy = x2 - x1, y2 - y1
    t = ((p[0] - x1) * dx + (p[1] - y1) * dy) / (dx * dx + dy * dy)
    if not -0.02 <= t <= 1.02:
        return False
    return near(p, (x1 + t * dx, y1 + t * dy), tol)


class Net:
    def __init__(self, page):
        self.page = page
        text = svg(page)
        self.segs, self.dots = paths(text, WIRE)
        self.pins, _ = paths(text, PIN)
        self.words = words(page)
        self.parent = list(range(len(self.segs)))
        self._build()

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i, j):
        a, b = self.find(i), self.find(j)
        if a != b:
            self.parent[a] = b

    def _build(self):
        # 1. общий конец — соединение без всяких точек
        ends = {}
        for i, (p, q) in enumerate(self.segs):
            for e in (p, q):
                ends.setdefault((round(e[0]), round(e[1])), []).append((i, e))
        for cell, lst in list(ends.items()):
            around = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    around += ends.get((cell[0] + dx, cell[1] + dy), [])
            for i, e in lst:
                for j, f in around:
                    if i != j and near(e, f):
                        self.union(i, j)
        # 2. точка соединения стягивает всё, что через неё проходит
        for d in self.dots:
            touch = [i for i, s in enumerate(self.segs) if on_segment(d, s)]
            for i in touch[1:]:
                self.union(touch[0], i)

    def net_of(self, pt):
        hit = [i for i, s in enumerate(self.segs) if on_segment(pt, s, 1.5)]
        if not hit:
            return None
        return self.find(hit[0])

    def members(self, root):
        return [i for i in range(len(self.segs)) if self.find(i) == root]

    def labels(self, idxs):
        """Слова, лежащие вплотную к отрезкам цепи."""
        res = []
        for w, x, y, vert in self.words:
            if not re.fullmatch(r"[A-Za-z0-9_+\-./]{1,20}", w) or w.isdigit():
                continue    # голое число — это номер вывода, а не имя цепи
            for i in idxs:
                if self._word_near(x, y, vert, self.segs[i]):
                    res.append(w)
                    break
        return sorted(set(res))

    def _word_near(self, x, y, vert, seg):
        """Метка стоит либо вдоль провода, либо сразу за его концом.

        Обе привязки взяты из самой схемы: подпись сидит на 2..5 пунктов
        выше горизонтального провода (левее вертикального) и никогда с
        другой стороны, а имя цепи в конце провода — на 11 пунктов дальше
        конца, по оси провода. Односторонний допуск важен: соседние ряды
        выводов отстоят на 7.2 пункта, симметричное окно захватывало бы
        подпись соседней цепи.
        """
        (x1, y1), (x2, y2) = seg
        if abs(x1 - x2) <= TOL:                    # вертикальный провод
            if vert and -LABEL_GAP <= x - x1 <= 0.5 \
                    and min(y1, y2) - LABEL_GAP <= y <= max(y1, y2) + LABEL_GAP:
                return True
            return abs(x - x1) <= 2.5 and (
                min(y1, y2) - END_GAP <= y <= min(y1, y2)
                or max(y1, y2) <= y <= max(y1, y2) + END_GAP)
        if abs(y1 - y2) <= TOL:                    # горизонтальный провод
            if not vert and -LABEL_GAP <= y - y1 <= 0.5 \
                    and min(x1, x2) - LABEL_GAP <= x <= max(x1, x2) + LABEL_GAP:
                return True
            return abs(y - y1) <= 2.5 and (
                min(x1, x2) - END_GAP <= x <= min(x1, x2)
                or max(x1, x2) <= x <= max(x1, x2) + END_GAP)
        return False

    def pins_touched(self, idxs):
        """Красные выводы, к которым цепь физически прикасается."""
        res = []
        for pp, pq in self.pins:
            for i in idxs:
                s = self.segs[i]
                if (on_segment(pp, s) or on_segment(pq, s)
                        or on_segment(s[0], (pp, pq)) or on_segment(s[1], (pp, pq))):
                    res.append((pp, pq))
                    break
        return res

    def near_words(self, x, y, r):
        return sorted(((round(wx, 1), round(wy, 1), w) for w, wx, wy, _ in self.words
                       if abs(wx - x) <= r and abs(wy - y) <= r),
                      key=lambda t: (t[1], t[0]))


def show_net(n, root, verbose):
    idxs = n.members(root)
    segs = [n.segs[i] for i in idxs]
    xs = [p[0] for s in segs for p in s]
    ys = [p[1] for s in segs for p in s]
    print(f"  отрезков {len(idxs)}, габарит x {min(xs):.0f}..{max(xs):.0f} "
          f"y {min(ys):.0f}..{max(ys):.0f}")
    print(f"  метки: {' '.join(n.labels(idxs)) or '—'}")
    pins = n.pins_touched(idxs)
    print(f"  касается выводов/корпусов: {len(pins)}")
    seen = set()
    for pp, pq in pins:
        mid = ((pp[0] + pq[0]) / 2, (pp[1] + pq[1]) / 2)
        for x, y, w in n.near_words(mid[0], mid[1], 12):
            if w not in seen and re.fullmatch(r"[A-Za-z0-9_+\-./]{1,18}", w):
                seen.add(w)
                print(f"    рядом с ({mid[0]:6.1f},{mid[1]:6.1f}): {w}")
    if verbose:
        for s in sorted(segs):
            print(f"    {s[0]} -> {s[1]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", type=int, default=3)
    ap.add_argument("--at", nargs=2, type=float, metavar=("X", "Y"))
    ap.add_argument("--trace", nargs=2, type=float, metavar=("X", "Y"))
    ap.add_argument("--label", nargs="+")
    ap.add_argument("--radius", type=float, default=10.0)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    n = Net(a.page)
    print(f"стр. {a.page}: проводов {len(n.segs)}, точек соединения {len(n.dots)}",
          file=sys.stderr)

    if a.at:
        x, y = a.at
        print(f"=== окрестность ({x}, {y}) r={a.radius}")
        for wx, wy, w in n.near_words(x, y, a.radius):
            print(f"  текст {w:20s} ({wx}, {wy})")
        for s in n.segs:
            if any(abs(p[0] - x) <= a.radius and abs(p[1] - y) <= a.radius for p in s):
                print(f"  провод {s[0]} -> {s[1]}")
        for d in n.dots:
            if abs(d[0] - x) <= a.radius and abs(d[1] - y) <= a.radius:
                print(f"  ТОЧКА  {d}")

    if a.trace:
        root = n.net_of(tuple(a.trace))
        print(f"=== цепь через ({a.trace[0]}, {a.trace[1]})")
        if root is None:
            print("  провода в этой точке нет")
        else:
            show_net(n, root, a.verbose)

    for lab in a.label or []:
        hits = [(x, y, vert) for w, x, y, vert in n.words if w == lab]
        print(f"=== метка {lab}: вхождений {len(hits)}")
        roots = {}
        for x, y, vert in hits:
            for dx, dy in ((0, 0), (0, 2), (0, -2), (3, 0), (-3, 0),
                           (0, 4), (0, -4), (6, 0), (-6, 0)):
                r = n.net_of((x + dx, y + dy))
                if r is not None:
                    roots.setdefault(r, []).append((round(x), round(y)))
                    break
        for r, where in roots.items():
            print(f"  -- фрагмент, метка стоит в {where}")
            show_net(n, r, a.verbose)


if __name__ == "__main__":
    main()
