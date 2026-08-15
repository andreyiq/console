#!/usr/bin/env python3
"""Уложить на плату медь из `.ses` — ответа freerouting.

Кладём заново каждый раз: скрипт сначала снимает с платы всю медь — и дорожки,
и переходные, — а потом кладёт то, что пришло. Так его можно гонять сколько
угодно раз подряд, и результат зависит только от входного `.ses`.

Сшивку по земле он тоже сносит, и это нарочно: землю трассировщик разводит
наравне с прочим, свои переходные ставит сам, и отличить их от заклёпок
`pcb06_planes.py` уже нельзя — при повторном приёме те бы просто копились.
Поэтому порядок такой: `pcb08_ses.py`, затем `pcb06_planes.py` — он вернёт
заливки и досыпет сшивку по оставшемуся свободному полю.

Единицы сверяем по самому файлу, и не от лени. KiCad пишет в `.dsn`
микрометры, а freerouting возвращает `.ses` в десятых долях микрометра — при
одном и том же заголовке `(resolution um 10)`. Разница ровно вдесятеро, и
дорожки от неё ложатся далеко за плату, но при этом выглядят складно: ошибка
видна только если посмотреть на числа.

Поэтому масштаб не угадывается, а измеряется: в `.ses` есть посадка деталей,
их места мы знаем точно, отсюда и множитель. Если детали дадут разные
множители — скрипт остановится, а не положит криво.

Ось Y в обоих файлах направлена вверх, в KiCad вниз, отсюда смена знака.

Ширина дорожки берётся из самого `.ses`: freerouting возвращает ту, которую мы
задали ему в правилах, и спорить с ней тут не о чем.

Запускать: `python3 pcb08_ses.py <файл.ses>`, потом `pcb06_planes.py` и
`pcb09_gnd.py` — вернуть заливки со сшивкой и добить землю там, где дорожки
отрезали куски заливки от остального.
"""
import re
import sys
from pathlib import Path

import pcbnew

import dsn

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"

LAYER = {"F.Cu": pcbnew.F_Cu, "B.Cu": pcbnew.B_Cu}
VIA_PAD, VIA_DRILL = 0.9, 0.5


def mm(v):
    return pcbnew.FromMM(v)


def scale_of(text, board):
    """Сколько миллиметров в единице `.ses` — по посадке деталей.

    Берём каждую деталь, чьё место знаем, и делим: миллиметры платы на
    единицы файла. Все ответы обязаны совпасть.
    """
    got = {}
    for ref, x, y in re.findall(r"\(place (\S+) (-?[\d.]+) (-?[\d.]+)", text):
        f = board.FindFootprintByReference(ref)
        if f is None or abs(float(x)) < 1.0:
            continue
        got[ref] = pcbnew.ToMM(f.GetPosition().x) / float(x)
    if not got:
        raise SystemExit("в .ses нет ни одной знакомой детали — масштаб не проверить")
    lo, hi = min(got.values()), max(got.values())
    if hi - lo > 1e-9:
        raise SystemExit(f"детали дают разный масштаб: от {lo} до {hi}")
    return lo


def xy(x, y, k):
    """Точка `.ses` (Y вверх) в координатах платы."""
    return pcbnew.VECTOR2I(mm(x * k), mm(-y * k))


def via_size(padstack):
    """Медь и сверло переходной из имени колодки, вида `Via[0-1]_800:400_um`.

    Размер задаём мы сами в правилах, но верить лучше тому, что вернулось:
    сверло 0,5 — это предел нашего набора (10-mech.md §7), и молча положить
    вместо него что-то мельче нельзя.
    """
    m = re.search(r"_(\d+):(\d+)_um", padstack or "")
    if not m:
        return VIA_PAD, VIA_DRILL
    return int(m.group(1)) / 1000.0, int(m.group(2)) / 1000.0


def clear(board):
    """Снять с платы всю медь трассировки — дорожки и переходные."""
    doomed = list(board.GetTracks())
    for t in doomed:
        board.RemoveNative(t)
    return len(doomed)


def main():
    if len(sys.argv) != 2:
        sys.exit("нужен путь к .ses")
    text = Path(sys.argv[1]).read_text()
    items = dsn.parse_ses(text)

    board = pcbnew.LoadBoard(str(BOARD))
    k = scale_of(text, board)
    nets = {n.GetNetname(): n for n in board.GetNetsByName().values()}
    dropped = clear(board)

    wires = vias = 0
    unknown = set()
    for kind, layer, width, pts, net in items:
        n = nets.get(net)
        if n is None:
            unknown.add(net)
            continue
        if kind == "via":
            pad, drill = via_size(layer)
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(xy(*pts[0], k))
            v.SetWidth(mm(pad))
            v.SetDrill(mm(drill))
            v.SetNet(n)
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            board.Add(v)
            vias += 1
            continue
        if layer not in LAYER:
            unknown.add(layer)
            continue
        for a, b in zip(pts, pts[1:]):
            if a == b:
                continue
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(xy(*a, k))
            t.SetEnd(xy(*b, k))
            t.SetWidth(mm(width * k))
            t.SetLayer(LAYER[layer])
            t.SetNet(n)
            board.Add(t)
            wires += 1

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(str(BOARD))
    print(f"масштаб .ses: 1 единица = {k} мм")
    print(f"снято прежней меди: {dropped}; положено отрезков: {wires}, "
          f"переходных: {vias}")
    if unknown:
        print("не понял (цепь или слой):", ", ".join(sorted(unknown)[:10]))


if __name__ == "__main__":
    main()
