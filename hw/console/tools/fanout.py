#!/usr/bin/env python3
"""Куда смотрит каждый вывод F133 и куда ему на самом деле надо.

Не шаг конвейера, а отчёт — но самый важный из отчётов. Выход из-под корпуса
с шагом 0.4 мм идёт только веером, строго наружу: между дорожками веера
переходную (0.9 мм меди) не поставить, значит первые миллиметры сигнал не
может ни свернуть, ни нырнуть на изнанку.

Поэтому решает не длина дорожки, а **сторона**. Вывод, которому надо на другую
сторону корпуса, тянет свою дорожку вокруг всего чипа — и по дороге пересекает
чужие веера. Десяток таких, и разводка встаёт.

Скрипт считает для каждого вывода: с какой стороны он выходит и куда зовёт его
цепь (середина остальных площадок цепи). Дальше — сколько выводов согласны со
своей стороной, а сколько идут вокруг, и какие это цепи.

Запуск: `python3 fanout.py`.
"""
import math
from collections import Counter, defaultdict
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"

CHIP = "U1"
SIDES = ("влево", "вверх", "вправо", "вниз")


def side_of(dx, dy):
    """Сторона света по вектору: экран KiCad, y растёт вниз."""
    if abs(dx) >= abs(dy):
        return "вправо" if dx > 0 else "влево"
    return "вниз" if dy > 0 else "вверх"


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    chip = board.FindFootprintByReference(CHIP)
    cx = pcbnew.ToMM(chip.GetPosition().x)
    cy = pcbnew.ToMM(chip.GetPosition().y)

    # где лежат площадки каждой цепи, кроме самого чипа
    elsewhere = defaultdict(list)
    for f in board.GetFootprints():
        if f.GetReference() == CHIP:
            continue
        for p in f.Pads():
            net = p.GetNetname()
            if net:
                pos = p.GetPosition()
                elsewhere[net].append((pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y)))

    agree, around, lonely = 0, [], 0
    per_side = Counter()
    wrong_side = defaultdict(list)
    for p in chip.Pads():
        net = p.GetNetname()
        if not net or net == "GND":
            continue
        others = elsewhere.get(net)
        if not others:
            lonely += 1
            continue
        pos = p.GetPosition()
        px, py = pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y)
        out = side_of(px - cx, py - cy)
        per_side[out] += 1
        tx = sum(x for x, _ in others) / len(others)
        ty = sum(y for _, y in others) / len(others)
        want = side_of(tx - cx, ty - cy)
        if want == out:
            agree += 1
        else:
            around.append((net, out, want,
                           math.hypot(tx - px, ty - py)))
            wrong_side[(out, want)].append(net)

    total = agree + len(around)
    print(f"выводов с цепями: {total} (без земли), не подключено нигде: {lonely}")
    print(f"  выходят в свою сторону: {agree}")
    print(f"  идут вокруг корпуса:    {len(around)}")

    print("\nвыводов по сторонам корпуса:")
    for s in SIDES:
        print(f"  {s:<7} {per_side[s]:>3}")

    print("\nкуда просятся те, что идут вокруг:")
    for (out, want), nets in sorted(wrong_side.items(),
                                    key=lambda kv: -len(kv[1])):
        show = ", ".join(sorted(nets)[:6])
        more = f" и ещё {len(nets) - 6}" if len(nets) > 6 else ""
        print(f"  {out:<7} → {want:<7} {len(nets):>3}:  {show}{more}")

    print("\nсамые дальние:")
    for net, out, want, d in sorted(around, key=lambda a: -a[3])[:8]:
        print(f"  {net:<16} {out:<7} → {want:<7} {d:.0f} мм")


if __name__ == "__main__":
    main()
