#!/usr/bin/env python3
"""Подготовить задание трассировщику из того, что выгрузил KiCad.

KiCad пишет `console.dsn` (File → Export → Specctra DSN; из командной строки
этого нет). Отдавать его freerouting как есть нельзя — три поправки, и каждая
получена набитой шишкой, а не из общих соображений.

**Зазор для выхода из-под F133.** Общее правило платы — 0,2 мм, и по нему
выйти из-под корпуса с шагом 0,4 мм невозможно: дорожка 0,2 по оси площадки
оставляет до края соседней 0,185. Трассировщик честно отказывался и возвращал
«не разведено» даже там, где места хватало. Ставим 185 мкм для пары
дорожка-площадка, того же требует правило в `console.kicad_dru`.

**Одна переходная, наша.** KiCad предлагает на выбор свою 0,6/0,3 и нашу
0,9/0,5, а freerouting берёт первую. Сверло 0,3 — нижняя граница набора и
негодная для заклёпки из проволоки (10-mech.md §7), поэтому лишнюю убираем.

**Заливки — не в задание.** Это оказалось главным. Земля у нас на обеих
сторонах: сплошная плоскость на изнанке и заливка на лице; в выгрузке они —
`(plane GND (polygon …))` на всю плату. Для трассировщика это медь, занятая
чужой цепью, то есть двухсторонняя плата превращается в бесслойную, и он
упирается: сотня связей из 270 так и оставалась неразведённой.

Убираем обе. Земля тогда становится обычными связями, которые он разводит
наравне с прочими, и площади хватает: на том же первом проходе разведено 206
связей против 116. Заливки возвращает `pcb06_planes.py` после укладки — он
переливает их вокруг готовых дорожек, и дорожки земли в них просто тонут.

Это ровно тот порядок, что принят вообще: сначала разводка, потом заливка.

**Земля — не в задание вовсе.** Следующий шаг той же мысли. Заливку мы всё
равно вернём, и она соберёт землю сама; а трассировщику земля обходится дорого
— это треть всех связей, две сотни отрезков и половина занятого поля. Убираем
`GND` из списка цепей: площадки при этом никуда не деваются, он их видит и
обходит, но тянуть к ним ничего не обязан.

Разница решающая: 43 связи неразведёнными вместо 114, и проходы вместо
нескольких минут занимают секунды.

**Посадка деталей берётся с платы, а не из выгрузки.** Экспорт `.dsn` делается
руками из KiCad, а размещение мы правим скриптами — и без этого каждая правка
требовала бы снова лезть в KiCad. Секция `placement` устроена просто: строка
на деталь, миллиметры и угол. Переписываем её по нынешней плате, и выгрузку
можно не трогать, пока не менялась схема.

**Сшивка земли — не в задание.** Заклёпки по сетке 8 мм ставит
`pcb06_planes.py`, и в выгрузке они попадают в `wiring`. Пробовали отдать их
трассировщику закреплёнными: сотня столбов по всему полю, и он развёл заметно
хуже (223 связи не разведены против 154). Порядок работ обратный: сначала
дорожки, потом заклёпки по оставшемуся полю — `pcb06` умеет обходить дорожки.

Запуск: `python3 pcb07_dsn.py`, дальше

    FREEROUTING__ROUTER__MAX_PASSES=250 java -jar freerouting.jar \
        -de console.route.dsn -do console.ses

Число проходов задаётся **только переменной окружения** — это важно, потому
что файл он пишет единственный раз, по окончании работы, а сам не кончает
никогда. Ключи `-mp`, `-oit` и настройки `router.max_passes`,
`router.stop_pass_no`, `router.job_timeout` в `freerouting.json` он молча
пропускает мимо ушей; проверено, при лимите в 10 проходов уходил на 45-й.
"""
import re
from pathlib import Path

import pcbnew

import dsn

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "console.dsn"
DST = ROOT / "console.route.dsn"

ESCAPE = 185                    # мкм, зазор дорожки до площадки под F133
VIA = "Via[0-1]_900:500_um"
STITCH = re.compile(r'\n\s*\(via "[^"]+"\s+-?[\d.]+\s+-?[\d.]+ '
                    r'\(net GND\)\(type \w+\)\)')


def drop_plane(text, layer):
    """Выкинуть `(plane … (polygon СЛОЙ …))` — вместе с закрывающей скобкой."""
    n = 0
    while True:
        m = re.search(r"\n    \(plane \w+ \(polygon " + re.escape(layer), text)
        if not m:
            return text, n
        depth, i = 0, m.start() + 5
        while True:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        text = text[:m.start()] + text[i + 1:]
        n += 1


PLACE = re.compile(r"\(place (\S+) (-?[\d.]+) (-?[\d.]+) (front|back) (-?[\d.]+)")


def replace_places(text, board):
    """Переписать посадку деталей по плате. Единицы `.dsn` — микрометры."""
    missing, moved = [], 0

    def one(m):
        nonlocal moved
        ref = m.group(1)
        f = board.FindFootprintByReference(ref)
        if f is None:
            missing.append(ref)
            return m.group(0)
        pos = f.GetPosition()
        side = "back" if f.IsFlipped() else "front"
        rot = f.GetOrientationDegrees() % 360
        moved += 1
        return (f"(place {ref} {pcbnew.ToMM(pos.x) * 1000:.6f} "
                f"{-pcbnew.ToMM(pos.y) * 1000:.6f} {side} {rot:.6f}")

    text = PLACE.sub(one, text)
    if missing:
        raise SystemExit("в выгрузке есть детали, которых нет на плате: "
                         + ", ".join(missing[:10]))
    return text, moved


def main():
    text = SRC.read_text()
    board = pcbnew.LoadBoard(str(ROOT / "console.kicad_pcb"))
    text, moved = replace_places(text, board)

    if "(type wire_smd)" not in text:
        text = text.replace(
            "      (clearance 50 (type smd_smd))\n",
            "      (clearance 50 (type smd_smd))\n"
            f"      (clearance {ESCAPE} (type wire_smd))\n"
            f"      (clearance {ESCAPE} (type wire_pin))\n")

    vias = re.search(r"\n    \(via [^\n]*\)", text)
    text = text[:vias.start()] + f'\n    (via "{VIA}")' + text[vias.end():]

    text, n = STITCH.subn("", text)
    text = dsn.subset(text, {c for c in dsn.nets_of(text) if c != "GND"})
    planes = 0
    for layer in ("F.Cu", "B.Cu"):
        text, k = drop_plane(text, layer)
        planes += k

    DST.write_text(text)
    print(f"{DST.name}: зазор выхода {ESCAPE} мкм, переходная {VIA}, "
          f"сшивки убрано {n}, заливок убрано {planes}, "
          f"цепей в задании {len(dsn.nets_of(text))}, "
          f"посадка обновлена у {moved}")


if __name__ == "__main__":
    main()
