#!/usr/bin/env python3
"""Чего не хватает после разводки: разрывы по цепям, деталям и расстояниям.

Не шаг конвейера, а отчёт. Сам по себе счёт «столько-то не разведено» ничего
не подсказывает: важно, **какие** связи не сошлись. Разрыв на пятьдесят
миллиметров говорит о размещении — деталь стоит не там, где ей место; десяток
разрывов в одной точке говорит о тесноте.

Запуск: `python3 gaps.py`, читает плату и спрашивает DRC.
"""
import math
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"

ITEM = re.compile(r"@\((?P<x>[\d,\.]+) mm, (?P<y>[\d,\.]+) mm\): (?P<what>[^\n]+)")
REF = re.compile(r"\b([A-Z]+\d+)\b")
NET = re.compile(r"\[([^\]]+)\]")


def num(s):
    return float(s.replace(",", "."))


def report():
    with tempfile.TemporaryDirectory() as d:
        rpt = Path(d) / "drc.rpt"
        subprocess.run(["kicad-cli", "pcb", "drc", "--severity-error",
                        "-o", str(rpt), str(BOARD)], capture_output=True)
        return rpt.read_text()


def main():
    text = report()
    gaps = []
    for blk in re.split(r"\n(?=\[)", text):
        if not blk.startswith("[unconnected_items]"):
            continue
        items = list(ITEM.finditer(blk))
        if len(items) != 2:
            continue
        a, b = items
        net = NET.search(a.group("what")) or NET.search(b.group("what"))
        refs = REF.findall(a.group("what")) + REF.findall(b.group("what"))
        dist = math.hypot(num(a.group("x")) - num(b.group("x")),
                          num(a.group("y")) - num(b.group("y")))
        gaps.append((net.group(1) if net else "?", dist, refs,
                     (num(a.group("x")), num(a.group("y")))))

    if not gaps:
        print("разрывов нет")
        return

    print(f"разрывов: {len(gaps)}")

    by_net = Counter(g[0] for g in gaps)
    print("\nпо цепям (первая десятка):")
    for net, n in by_net.most_common(10):
        far = max(g[1] for g in gaps if g[0] == net)
        print(f"  {net:<16} {n:>3}   самый длинный {far:.0f} мм")

    by_ref = Counter(r for g in gaps for r in g[2])
    print("\nпо деталям (первая десятка):")
    for ref, n in by_ref.most_common(10):
        print(f"  {ref:<8} {n:>3}")

    print("\nпо длине:")
    for lo, hi in ((0, 5), (5, 15), (15, 30), (30, 60), (60, 999)):
        n = sum(1 for g in gaps if lo <= g[1] < hi)
        if n:
            print(f"  {lo:>3}–{hi:<3} мм  {n:>3}")

    cells = defaultdict(int)
    for _, _, _, (x, y) in gaps:
        cells[(int(x // 20) * 20, int(y // 20) * 20)] += 1
    print("\nгде гуще (квадраты 20 мм):")
    for (x, y), n in sorted(cells.items(), key=lambda kv: -kv[1])[:6]:
        print(f"  x {x}–{x + 20}, y {y}–{y + 20}: {n}")


if __name__ == "__main__":
    main()
