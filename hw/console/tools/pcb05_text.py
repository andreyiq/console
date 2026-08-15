#!/usr/bin/env python3
"""Ссылки деталей — на слой `Fab`, а не на шелкографию.

Плату травим дома: слоя шелкографии у неё физически нет, его никто не печатает.
Но текст ссылок штатно лежит именно на нём, и на плотной плате из 161 корпуса
он даёт около сотни нарушений DRC — надпись налезает на площадку соседа или на
его контур. За этой сотней не видно настоящих находок, а «починить» её,
раздвигая надписи, нельзя: места нет и не будет.

Поэтому ссылки переезжают на `F.Fab` / `B.Fab`. Там они и нужны: при сборке
смотрят в CAD, а не на голый стеклотекстолит. Значения прячем — они есть в
netlist и в BOM, на чертеже от них только каша.

Если плату когда-нибудь закажут на заводе, шелкография понадобится — тогда
ссылки надо будет вернуть на `*.SilkS` и расставить руками. Это записано в
10-mech.md §10.

Скрипт идемпотентный. Запускать последним в конвейере.
"""
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    moved = hidden = 0
    for fp in board.GetFootprints():
        fab = pcbnew.B_Fab if fp.IsFlipped() else pcbnew.F_Fab
        ref = fp.Reference()
        if ref.GetLayer() != fab:
            ref.SetLayer(fab)
            moved += 1
        ref.SetVisible(True)

        val = fp.Value()
        if val.GetLayer() != fab:
            val.SetLayer(fab)
        if val.IsVisible():
            val.SetVisible(False)
            hidden += 1
    board.Save(str(BOARD))
    print(f"ссылок перенесено на Fab: {moved}, значений скрыто: {hidden}")


if __name__ == "__main__":
    main()
