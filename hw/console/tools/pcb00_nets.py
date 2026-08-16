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

    changed, missing = [], set()
    for f in board.GetFootprints():
        ref = f.GetReference()
        for p in f.Pads():
            name = want.get((ref, p.GetPadName()))
            if name is None or name == p.GetNetname():
                continue
            net = known.get(name)
            if net is None:
                missing.add(name)
                continue
            changed.append(f"{ref}-{p.GetPadName()}: "
                           f"{p.GetNetname() or '—'} → {name}")
            p.SetNet(net)

    if changed:
        board.Save(str(BOARD))
    print(f"цепей на площадках исправлено: {len(changed)}")
    for line in changed[:12]:
        print("   ", line)
    if len(changed) > 12:
        print(f"    … и ещё {len(changed) - 12}")
    if missing:
        print("НЕТ на плате (нужен F8):", ", ".join(sorted(missing)[:10]))


if __name__ == "__main__":
    main()
