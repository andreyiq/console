#!/usr/bin/env python3
"""Подтянуть корпуса на плате под то, что записано в схеме.

Зачем отдельный скрипт: «Update PCB from Schematic» меняет корпус, только если
в диалоге взведена галка «Replace footprints with those specified in the
schematic», а она по умолчанию снята. Плюс pcbnew после `Remove`/`Add` ломает
свой контейнер корпусов — поиск по ссылке начинает отдавать сырой
`SwigPyObject`, и это не лечится даже повторным `LoadBoard` в том же процессе.
Поэтому подмена живёт в своём процессе и делается до размещения.

Запуск:  python3 hw/console/tools/pcb00_sync.py
"""
import re
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"


SYS_FP = Path("/usr/share/kicad/footprints")


def want_footprints():
    """ref -> "библиотека:корпус", как записано в схеме."""
    s = (ROOT / "console.kicad_sch").read_text()
    out, depth, start, i, blocks = {}, 0, None, 0, []
    while i < len(s):
        if s.startswith("(symbol", i) and depth == 0:
            start, depth = i, 1
            i += 7
            continue
        if depth:
            if s[i] == "(":
                depth += 1
            elif s[i] == ")":
                depth -= 1
                if depth == 0:
                    blocks.append(s[start:i + 1])
        i += 1
    for blk in blocks:
        rf = re.search(r'\(property "Reference" "([^"]+)"', blk)
        fp = re.search(r'\(property "Footprint" "([^"]*)"', blk)
        # Отбираем только реальные детали листа. В `lib_symbols` лежат
        # определения библиотечных символов — у них ссылка это префикс без
        # номера (`U`, `R`, `C`), и без этой проверки они попадают в сверку
        # состава как несуществующие детали.
        if rf and fp and fp.group(1) and re.fullmatch(r"[A-Z]+\d+", rf.group(1)):
            out[rf.group(1)] = fp.group(1)
    return out


def lib_dir(nick):
    local = ROOT / "lib" / f"{nick}.pretty"
    return local if local.exists() else SYS_FP / f"{nick}.pretty"


def sync(board):
    """Подтянуть корпуса под то, что записано в схеме.

    «Update PCB from Schematic» меняет корпус, только если в диалоге взведена
    галка «Replace footprints with those specified in the schematic», а она по
    умолчанию снята. Делаем это сами, чтобы посадка не зависела от того, что
    нажали в GUI.

    Цепи переносятся **по номеру площадки**, и это единственно верный способ:
    схема говорит «вывод N разъёма несёт цепь X», номер вывода и есть связь.
    Именно поэтому подмена штатного FH12 на наш `..._ContactsReversed`
    работает без единой правки цепей — площадка `N` просто оказывается в
    другом месте корпуса (06-display.md §7.2.1).

    Порядок вызовов важен: новый корпус сначала добавляется на плату и только
    потом переворачивается и получает цепи. Если сделать наоборот, pcbnew
    падает сегфолтом.
    """
    want = want_footprints()
    changed = []
    for fp in list(board.GetFootprints()):
        ref = fp.GetReference()
        target = want.get(ref)
        if not target or ":" not in target:
            continue                       # крепёж `H*` в схеме не значится
        if fp.GetFPIDAsString() == target:
            continue
        nick, name = target.split(":", 1)
        new = pcbnew.FootprintLoad(str(lib_dir(nick)), name)
        if new is None:
            changed.append(f"{ref}: НЕ НАЙДЕН {target}")
            continue
        nets = {p.GetNumber(): p.GetNet() for p in fp.Pads()}
        new.SetReference(ref)
        new.SetValue(fp.GetValue())
        new.SetPosition(fp.GetPosition())
        new.SetOrientation(fp.GetOrientation())
        board.Add(new)
        if fp.IsFlipped():
            new.Flip(new.GetPosition(), False)
        for pad in new.Pads():
            if pad.GetNumber() in nets:
                pad.SetNet(nets[pad.GetNumber()])
        new.SetFPID(pcbnew.LIB_ID(nick, name))
        new.SetLocked(fp.IsLocked())
        board.Remove(fp)
        changed.append(f"{ref}: {fp.GetFPIDAsString().split(':')[-1]} -> {name}")
    return changed


def audit(board):
    """Сверить состав платы со схемой.

    Скрипт умеет подменять корпус у детали, но не умеет заводить и удалять
    детали: это работа «Update PCB from Schematic». Поэтому расхождение по
    составу он не чинит, а показывает — иначе плата молча живёт со старым
    набором. Крепёж `H*` в схеме не значится и в сверке не участвует.
    """
    want = set(want_footprints())
    have = {f.GetReference() for f in board.GetFootprints()
            if not f.GetReference().startswith("H")}
    return sorted(want - have), sorted(have - want)


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    changed = sync(board)
    for line in changed or ["корпуса уже совпадают со схемой"]:
        print(" ", line)
    if changed:
        board.Save(str(BOARD))

    missing, extra = audit(board)

    # Осиротевшие убираем сами. «Update PCB from Schematic» делает это только
    # при взведённой галке «Delete footprints with no symbols», и она тоже по
    # умолчанию снята: после переименования `SW2` -> `JP1` на плате остаются
    # оба. Крепёж `H*` под удаление не попадает — его в схеме нет и не должно
    # быть, он ставится скриптом контура.
    if extra:
        for fp in list(board.GetFootprints()):
            if fp.GetReference() in extra:
                board.Remove(fp)
        print("  убраны корпуса, которых нет в схеме:", " ".join(extra))
        board.Save(str(BOARD))

    if missing:
        print("  НЕТ НА ПЛАТЕ, нужен F8:", " ".join(missing))
    elif not extra:
        print("  состав платы совпадает со схемой")


if __name__ == "__main__":
    main()
