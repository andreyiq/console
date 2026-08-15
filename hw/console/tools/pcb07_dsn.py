#!/usr/bin/env python3
"""Подготовить задание трассировщику из того, что выгрузил KiCad.

KiCad пишет `console.dsn` (File → Export → Specctra DSN; из командной строки
этого нет). Отдавать его freerouting как есть нельзя — три поправки, и каждая
получена набитой шишкой, а не из общих соображений.

**Зазор не трогаем — 0,2 мм, как на всей плате.** Послабление до 185 мкм
здесь было и оказалось вредным. Оно нужно ровно там, где дорожка идёт по оси
площадки с шагом 0,4 — то есть под F133, — но в задании его иначе как на всю
плату не задать, и трассировщик честно поджимал 0,185 у чужих деталей: 32
нарушения DRC вдали от чипа.

А под самим чипом послабление больше не нужно: выход из-под корпуса делает
`pcb07_fanout.py`, и исключение для него записано в `console.kicad_dru`
адресно, по имени корпуса.

(Если послабление всё же понадобится, имя пары пишется в каноническом
порядке — `smd_wire`, а не `wire_smd`. Неизвестное имя freerouting принимает
молча и не применяет.)

**Переходная — 0,7/0,4, и это выбрано измерением.** KiCad предлагает свою
0,6/0,3 и нашу 0,9/0,5; freerouting берёт первую из списка. Проверили обе на
одном и том же задании:

| переходная | поставил | дорожек по изнанке | не разведено |
|---|---|---|---|
| 0,9 / 0,5 | **1** | 23 | 120 |
| 0,6 / 0,3 | 21 | 23 | 110 |

С нашей 0,9 он остаётся в одном слое: медь 0,9 плюс зазор 0,2 с двух сторон —
это круг 1,3 мм, в тесноте у корпуса ему просто негде встать. Плата при этом
двухсторонняя только на бумаге.

Берём середину, 0,7/0,4: сверло 0,4 из середины набора (у нас 0,3…1,2), а не
с его края, и поясок 0,15 на сторону — под заклёпку из проволоки хватает.
Колодку объявляем сами, в выгрузке её нет.

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

**Лучи из-под F133 уходят в задание неприкосновенной медью.** Их кладёт
`pcb07_fanout.py`, и трассировщику остаётся подхватить их концы в открытом
поле. Секция `wiring` целиком пересобирается по плате — там же, где и посадка.

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

VIA = "Via[0-1]_700:400_um"
VIA_SHAPE = f"""    (padstack "{VIA}"
      (shape (circle F.Cu 700))
      (shape (circle B.Cu 700))
      (attach off)
    )
"""
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


def fix_class(text):
    """Наша переходная — и в правила класса.

    Класс перекрывает общие правила задания: в нём своя переходная и свой
    зазор. Правки только общих правил класс молча возвращает обратно.
    """
    i = text.index("(class ")
    head, blk = text[:i], text[i:]
    end = blk.index("\n    )") + 6
    blk, tail = blk[:end], blk[end:]
    blk = re.sub(r'use_via "[^"]+"', f'use_via "{VIA}"', blk)
    return head + blk + tail


def replace_wiring(text, board):
    """Пересобрать `wiring` по закреплённой меди платы — это наши лучи."""
    out = ["  (wiring\n"]
    n = 0
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA) or not t.IsLocked():
            continue
        net = t.GetNetname()
        if not net:
            continue
        a, b = t.GetStart(), t.GetEnd()
        layer = board.GetLayerName(t.GetLayer())
        out.append(
            f"    (wire (path {layer} {pcbnew.ToMM(t.GetWidth()) * 1000:.0f}"
            f"  {pcbnew.ToMM(a.x) * 1000:.0f} {-pcbnew.ToMM(a.y) * 1000:.0f}"
            f"  {pcbnew.ToMM(b.x) * 1000:.0f} {-pcbnew.ToMM(b.y) * 1000:.0f})"
            f'(net "{net}")(type protect))\n')
        n += 1
    out.append("  )")
    s, e = dsn.sections(text)["wiring"]
    return text[:s] + "".join(out) + text[e:], n


def main():
    text = SRC.read_text()
    board = pcbnew.LoadBoard(str(ROOT / "console.kicad_pcb"))
    text, moved = replace_places(text, board)
    text, wires = replace_wiring(text, board)

    vias = re.search(r"\n    \(via [^\n]*\)", text)
    text = text[:vias.start()] + f'\n    (via "{VIA}")' + text[vias.end():]
    if VIA_SHAPE not in text:
        anchor = re.search(r'    \(padstack "Via\[0-1\]', text)
        text = text[:anchor.start()] + VIA_SHAPE + text[anchor.start():]

    text, n = STITCH.subn("", text)
    text = dsn.subset(text, {c for c in dsn.nets_of(text) if c != "GND"})
    text = fix_class(text)
    planes = 0
    for layer in ("F.Cu", "B.Cu"):
        text, k = drop_plane(text, layer)
        planes += k

    DST.write_text(text)
    print(f"{DST.name}: переходная {VIA}, "
          f"сшивки убрано {n}, заливок убрано {planes}, "
          f"цепей в задании {len(dsn.nets_of(text))}, "
          f"посадка обновлена у {moved}, лучей в задании {wires}")


if __name__ == "__main__":
    main()
