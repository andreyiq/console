#!/usr/bin/env python3
"""Перенести цепи со схемы на плату — то, что делает F8, но из скрипта.

Менять КАКОЙ вывод в какой цепи — обычное проектное решение, и у нас оно
случается: порядок бит на шлейфе дисплея выбран по разводке
(`block06_display.py`), и он ещё не раз поедет, если поедет размещение. А
«Update PCB from Schematic» живёт только в окне KiCad, командной строки у него
нет — то есть каждая такая правка требовала бы ручного шага и молча ломалась
бы, если про него забыть.

Здесь делается ровно одна часть его работы, зато честно: нетлист берётся у
`kicad-cli` из самой схемы, и каждой площадке платы ставится та цепь, которая
за ней записана. Ни деталей, ни корпусов скрипт не заводит и не удаляет —
это по-прежнему F8 (`pcb00_sync.py` показывает расхождение состава).

Запускать после правки схемы, до размещения и разводки.
"""
import re
import subprocess
import tempfile
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"
SCH = ROOT / "console.kicad_sch"


def netlist():
    """{(деталь, вывод): цепь} из схемы, через `kicad-cli`."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "net.net"
        r = subprocess.run(["kicad-cli", "sch", "export", "netlist",
                            "--format", "kicadsexpr", "-o", str(out), str(SCH)],
                           capture_output=True, text=True)
        if not out.exists():
            raise SystemExit("нетлист не собрался: " + r.stderr.strip())
        text = out.read_text()

    # Режем по началу цепи, а не по её концу: закрывающие скобки в этом
    # формате стоят на той же строке, что и последний вывод, и шаблон «конец
    # блока» молча не находил ничего.
    nets = {}
    for chunk in text.split("(net (code ")[1:]:
        m = re.match(r'"[^"]*"\) \(name "([^"]*)"\)', chunk)
        if not m:
            continue
        name = m.group(1)
        for ref, pin in re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)',
                                   chunk):
            nets[(ref, pin)] = name
    return nets


def main():
    want = netlist()
    board = pcbnew.LoadBoard(str(BOARD))
    known = {n.GetNetname(): n for n in board.GetNetsByName().values()}

    changed, missing, added = [], set(), set()
    for f in board.GetFootprints():
        ref = f.GetReference()
        for p in f.Pads():
            name = want.get((ref, p.GetPadName()))
            if name is None or name == p.GetNetname():
                continue
            net = known.get(name)
            if net is None:
                # Цепи, которой на плате ещё нет, заводим здесь же. Раньше
                # скрипт умел только раздавать уже заведённые и честно писал
                # «нужен F8» — а F8 живёт в окне KiCad, то есть оставался
                # ручной шаг ровно там, где правка схемы и требуется: новое имя
                # цепи появляется при КАЖДОМ переименовании сигнала. На снятии
                # параллельной шины дисплея так застряли четыре цепи из четырёх
                # новых (`LCD-DC`, `LCD-SCL`, `LCD-SDA`, `LCD-SDO`).
                net = pcbnew.NETINFO_ITEM(board, name)
                board.Add(net)
                known[name] = net
                added.add(name)
            changed.append(f"{ref}-{p.GetPadName()}: "
                           f"{p.GetNetname() or '—'} → {name}")
            p.SetNet(net)

    # Цепи, которых больше нет в схеме, но которые ещё держат медь на плате.
    # Сами по себе они безвредны (пустая цепь ничего не значит), а вот ДОРОЖКИ
    # на них — вредны: это медь, которой в схеме соответствия нет. Разводка её
    # снимает своим `pcb07_fanout.py`, но сказать о ней надо здесь, пока видно.
    live = set(want.values())
    stale = {}
    for t in board.GetTracks():
        n = t.GetNetname()
        if n and n not in live:
            stale[n] = stale.get(n, 0) + 1

    if changed or added:
        board.Save(str(BOARD))
    print(f"цепей на площадках исправлено: {len(changed)}, "
          f"заведено новых: {len(added)}")
    for line in changed[:12]:
        print("   ", line)
    if len(changed) > 12:
        print(f"    … и ещё {len(changed) - 12}")
    if added:
        print("    новые:", ", ".join(sorted(added)[:10]))
    if missing:
        print("НЕ УДАЛОСЬ завести:", ", ".join(sorted(missing)[:10]))
    if stale:
        top = sorted(stale.items(), key=lambda kv: -kv[1])[:6]
        print(f"  медь на цепях, которых в схеме нет: {sum(stale.values())} "
              f"отрезков по {len(stale)} цепям — "
              + ", ".join(f"{k}×{v}" for k, v in top))
        print("  снимется при следующем pcb07_fanout.py")


if __name__ == "__main__":
    main()
