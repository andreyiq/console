#!/usr/bin/env python3
"""Нетлистер для схемы Xassette-Asterisk (legacy KiCad 5 .sch).

`kicad-cli` этот формат не открывает, а сверяться с референсом надо на каждом
блоке — отсюда свой разбор. Цепи собираются union-find'ом по концам проводов,
junction'ам, меткам и power-символам; одинаковые имена склеиваются в одну цепь.

    python3 hw/console/tools/xassette_net.py U1 U3 U4 U5   # выводы компонентов
    python3 hw/console/tools/xassette_net.py --net +1V8 +3V3   # состав цепей

Ограничение: `Text Label` в KiCad 5 локальная для листа, но схема Xassette
одностраничная, так что склейка по имени здесь корректна.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

REF = Path(__file__).resolve().parents[2] / "ref" / "Xassette-Asterisk" / "hw"
SCH = REF / "XassetteAsterisk.sch"
LIBS = [REF / "XassetteAsterisk-cache.lib", REF / "lib" / "F133.lib"]


def load_libs():
    """{имя символа: [(номер, имя вывода, x, y)]}"""
    out = {}
    for path in LIBS:
        cur, pins = None, []
        for ln in path.read_text(encoding="utf-8", errors="replace").split("\n"):
            if ln.startswith("DEF "):
                cur, pins = ln.split()[1].lstrip("~"), []
            elif ln.startswith("X ") and cur:
                f = ln.split()
                pins.append((f[2], f[1], int(f[3]), int(f[4])))
            elif ln.startswith("ENDDEF") and cur:
                out.setdefault(cur, pins)
                # в cache.lib имя склеено с именем библиотеки через "_"
                out.setdefault(cur.split("_")[-1], pins)
                cur = None
    return out


def parse_sch():
    """(компоненты, провода, junction'ы, метки, значения)"""
    text = SCH.read_text(encoding="utf-8", errors="replace").split("\n")
    comps, wires, juncs, labels, vals = [], [], set(), [], {}
    i = 0
    while i < len(text):
        ln = text[i]
        if ln.startswith("$Comp"):
            name = ref = pos = mat = val = None
            i += 1
            while not text[i].startswith("$EndComp"):
                t = text[i]
                if t.startswith("L "):
                    name = t.split()[1].split(":")[-1].lstrip("~")
                    ref = t.split()[2]
                elif t.startswith("P "):
                    pos = (int(t.split()[1]), int(t.split()[2]))
                elif t.startswith("F 1 "):
                    val = t.split('"')[1]
                elif re.fullmatch(r"\s+-?\d+\s+-?\d+\s+-?\d+\s+-?\d+\s*", t):
                    mat = [int(v) for v in t.split()]
                i += 1
            comps.append((ref, name, pos, mat or [1, 0, 0, -1]))
            vals[ref] = val
        elif ln.startswith("Wire Wire Line"):
            f = [int(v) for v in text[i + 1].split()]
            wires.append(((f[0], f[1]), (f[2], f[3])))
            i += 1
        elif ln.startswith("Connection ~"):
            f = ln.split()
            juncs.add((int(f[2]), int(f[3])))
        elif ln.startswith(("Text Label", "Text GLabel", "Text HLabel")):
            f = ln.split()
            labels.append(((int(f[2]), int(f[3])), text[i + 1].strip()))
            i += 1
        i += 1
    return comps, wires, juncs, labels, vals


class UF(dict):
    def find(self, a):
        self.setdefault(a, a)
        while self[a] != a:
            self[a] = self[self[a]]
            a = self[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self[ra] = rb


def build():
    """(цепи {корень: [(ref, номер, имя вывода)]}, имена, выводы, значения)"""
    libs = load_libs()
    comps, wires, juncs, labels, vals = parse_sch()

    uf = UF()
    for a, b in wires:
        uf.union(a, b)

    def attach(pt):
        """Точка на теле провода — тоже соединение (вывод, метка, junction)."""
        for a, b in wires:
            if a[0] == b[0] == pt[0] and min(a[1], b[1]) <= pt[1] <= max(a[1], b[1]):
                uf.union(pt, a)
            elif a[1] == b[1] == pt[1] and min(a[0], b[0]) <= pt[0] <= max(a[0], b[0]):
                uf.union(pt, a)

    # пересечение проводов соединяется только через явный junction, а вот вывод
    # и метка цепляются к проводу в любой его точке
    for j in juncs:
        attach(j)
    for pt, _ in labels:
        attach(pt)

    pins = []   # (точка, ref, номер, имя вывода)
    for ref, name, pos, m in comps:
        if name not in libs or pos is None:
            continue
        for num, pname, px, py in libs[name]:
            pt = (pos[0] + m[0] * px + m[1] * py,
                  pos[1] + m[2] * px + m[3] * py)
            pins.append((pt, ref, num, pname))
            attach(pt)
            # выводы одного компонента между собой НЕ соединяются

    # одинаковые метки и power-символы — одна цепь
    byname = defaultdict(list)
    for pt, txt in labels:
        byname[txt].append(pt)
    for pt, ref, _, pname in pins:
        if ref.startswith("#PWR"):
            byname[pname].append(pt)
    for pts in byname.values():
        for p in pts[1:]:
            uf.union(pts[0], p)

    nets = defaultdict(list)
    for pt, ref, num, pname in pins:
        nets[uf.find(pt)].append((ref, num, pname))
    names = {uf.find(pts[0]): nm for nm, pts in byname.items()}
    return nets, names, pins, vals, uf


def main():
    args = sys.argv[1:]
    nets, names, pins, vals, uf = build()

    if args and args[0] == "--net":
        for want in args[1:]:
            root = next((r for r, nm in names.items() if nm == want), None)
            print(f"=== цепь {want}" + ("" if root else " — не найдена"))
            for ref, num, pname in sorted(set(nets.get(root, []))):
                if ref.startswith(("#PWR", "#FLG")):
                    continue
                print(f"  {ref:<6} {str(vals.get(ref)):<14} вывод {num} {pname}")
        return 0

    for want in args or ["U1"]:
        # безымянную цепь называем по её корню в union-find, а не по координате
        # вывода — иначе один и тот же узел печатается разными именами
        rows = [(int(n) if n.isdigit() else 999, n, pn,
                 names.get(uf.find(pt), "N%d,%d" % uf.find(pt)))
                for pt, ref, n, pn in pins if ref == want]
        print(f"=== {want}" + ("" if rows else " — не найден"))
        for _, num, pname, net in sorted(rows):
            print(f"  {num:>4}  {pname:<14} -> {net}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
