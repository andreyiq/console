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
import math
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

    # Сначала ЗАГРУЖАЕМ ВСЁ, потом трогаем плату. `FootprintLoad` после
    # первого же `board.Remove` падает с «SwigPyObject has no attribute
    # FootprintLoad»: pcbnew портит свой объект плагина ровно так же, как
    # портит контейнер корпусов. Пока загрузка стояла внутри цикла, скрипт
    # менял первый корпус и умирал на втором — то есть половину работы делал
    # и оставлял плату в промежуточном состоянии.
    todo = []
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
        todo.append((fp, new, nick, name))

    for fp, new, nick, name in todo:
        ref = fp.GetReference()
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


def audit(board, have):
    """Сверить состав платы со схемой.

    Скрипт умеет подменять корпус у детали, но не умеет заводить и удалять
    детали: это работа «Update PCB from Schematic». Поэтому расхождение по
    составу он не чинит, а показывает — иначе плата молча живёт со старым
    набором. Крепёж `H*` в схеме не значится и в сверке не участвует.
    """
    want = set(want_footprints())
    return sorted(want - have), sorted(have - want)


def preload(want, libs):
    """Заранее прочитать из библиотеки всё, с чем будем сверяться.

    Читать надо ДО первой правки платы: `FootprintLoad` после `board.Remove`
    падает с «SwigPyObject has no attribute FootprintLoad» — pcbnew портит
    объект плагина ровно так же, как портит контейнер корпусов.
    """
    out = {}
    for ref, fpid in want.items():
        if ":" not in fpid:
            continue
        lib, name = fpid.split(":", 1)
        if lib not in libs:
            continue
        fresh = pcbnew.FootprintLoad(str(libs[lib]), name)
        if fresh is not None:
            out[ref] = fresh
    return out


def relabel(board, want, fresh_of):
    """Сверить нумерацию площадок с библиотекой и поправить.

    Сверять одно имя корпуса мало. На плате у `J601` лежала копия со ШТАТНОЙ
    нумерацией, хотя имя стояло наше, `..._ContactsReversed`: копия попала
    туда раньше, чем библиотека была перенумерована, и с тех пор молча жила.
    А в ней вся суть — заворот шлейфа на 180° переставляет контакты, и без
    обратной нумерации шина дисплея разведена задом наперёд. DRC об этом
    говорит, но одной строчкой `lib_footprint_mismatch` среди сотни разрывов.

    Сверяем по МЕСТАМ: где в библиотеке стоит вывод N, там же он должен стоять
    и на плате. Поворот снимаем, чтобы сравнивать в осях самого корпуса.
    """
    fixed, checked = [], []
    for ref, fpid in want.items():
        fp = board.FindFootprintByReference(ref)
        fresh = fresh_of.get(ref)
        if fp is None or fresh is None:
            continue
        # Знак поворота: у KiCad ось Y смотрит вниз, и снимается поворот тем
        # же знаком, каким он задан. С обратным знаком не совпадает ни одна
        # площадка — и это выглядит как «нумерация на плате перевёрнута».
        # Я на этом попался и чуть не «починил» верную плату.
        rot = math.radians(fp.GetOrientationDegrees())
        c, s = math.cos(rot), math.sin(rot)
        here = {}
        for p in fp.Pads():
            dx = pcbnew.ToMM(p.GetX() - fp.GetX())
            dy = pcbnew.ToMM(p.GetY() - fp.GetY())
            here[(round(dx * c - dy * s, 2), round(dx * s + dy * c, 2))] = p
        there = {(round(pcbnew.ToMM(p.GetX()), 2),
                  round(pcbnew.ToMM(p.GetY()), 2)): p.GetPadName()
                 for p in fresh.Pads()}
        if set(here) != set(there):
            continue                      # геометрия другая — это не наш случай
        n = 0
        for pos, pad in here.items():
            if pad.GetPadName() != there[pos]:
                pad.SetPadName(there[pos])
                n += 1
        # Описание тоже с библиотеки. Не украшение: пока оно расходилось, DRC
        # держал `lib_footprint_mismatch` на `J601` — и эта строчка ничем не
        # отличается от той, которой он сообщал о перевёрнутой нумерации.
        # Одинаково выглядящее предупреждение про мелочь учит не смотреть на
        # предупреждение про важное.
        if fp.GetLibDescription() != fresh.GetLibDescription():
            fp.SetLibDescription(fresh.GetLibDescription())
            n += 1
        if n:
            fixed.append(f"{ref}: приведено к библиотеке, полей {n}")
        else:
            checked.append(ref)
    return fixed, checked


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    # Состав снимаем ДО подмены корпусов: подмена делает `Remove`, после
    # которого контейнер корпусов отдаёт сырой SwigPyObject (10-mech.md §8.2).
    have = {f.GetReference() for f in board.GetFootprints()
            if not f.GetReference().startswith("H")}
    want = want_footprints()
    fresh_of = preload(want, {"console": ROOT / "lib" / "console.pretty"})
    # Сверка нумерации — ДО подмены корпусов, а не после. После первого
    # `board.Remove` контейнер корпусов отдаёт сырой SwigPyObject, и
    # `FindFootprintByReference` возвращает объект без методов. Порядок при
    # этом ничего не портит: корпус, который подменяет `sync`, берётся прямо
    # из библиотеки и приходит с правильной нумерацией по построению.
    fixed, checked = relabel(board, want, fresh_of)
    changed = sync(board)
    print(f"  нумерация площадок сверена с библиотекой: {len(checked)}, "
          f"поправлено {len(fixed)}")
    for line in fixed:
        print("   ", line)
        changed = changed or [""]
    for line in changed or ["корпуса уже совпадают со схемой"]:
        print(" ", line)
    if changed:
        board.Save(str(BOARD))

    missing, extra = audit(board, have)

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
