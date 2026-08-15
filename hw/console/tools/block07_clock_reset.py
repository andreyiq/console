#!/usr/bin/env python3
"""Рисует блок 7 «ТАКТ И СБРОС» в hw/console/console.kicad_sch.

Запуск:  python3 hw/console/tools/block07_clock_reset.py

Скрипт идемпотентный: перед вставкой выкидывает из схемы всё, что лежит внутри
рамки блока 7, а рамку и заголовок не трогает.

Все номиналы — из hw/console/blocks/07-clock-reset.md, ссылка на раздел лежит в
поле `Источник` каждого компонента.

Блок соединяется с F133 глобальными метками `DXIN`, `DXOUT`, `X32KIN`,
`X32KOUT`, `RESET`. Вторые концы этих меток ставит блок 8 — там же, где вся
обвязка выводов F133. До этого netlist блока 7 замкнут сам на себя, и это
нормально.

Общая машинерия — в tools/kicadsch.py.
"""

import sys

from kicadsch import Sheet, root_uuid, write

# Рамка блока 7 из tools/scaffold.py — по ней чистится старое содержимое.
FRAME = (20.0, 274.0, 218.0, 344.0)

ROW = 302.26        # общая горизонталь, на которой стоят оба кварца
DOC = "07-clock-reset.md"


def crystal(s, cx, lib_id, ref, value, left_label, right_label,
            caps, src, fp, bias=None):
    """Кварц с двумя нагрузочными ёмкостями и метками по краям.

    Ёмкости висят на самой горизонтали, поэтому провод под ними обязан быть
    разрезан: иначе KiCad считает половины разными цепями (см. kicadsch.py).
    """
    y = ROW
    xl, xr = cx - 10.16, cx + 10.16          # где стоят ёмкости
    x_lab_l, x_lab_r = cx - 15.24, cx + 15.24

    dy = 6.35 if bias else -6.35        # с R20 сверху подписи не помещаются
    q = s.sym(lib_id, ref, value, cx, y, 0, src=src, fp=fp,
              rdx=0.0, rdy=dy, vdx=0.0, vdy=dy + (2.54 if bias else -2.54),
              just=None)

    # горизонталь: метка — ёмкость — кварц — ёмкость — метка, кусками
    for a, b in ((x_lab_l, xl), (xl, cx - 3.81),
                 (cx + 3.81, xr), (xr, x_lab_r)):
        s.wire((a, y), (b, y))
    s.glabel(left_label, (x_lab_l, y), 180)
    s.glabel(right_label, (x_lab_r, y), 0)

    for x, cref in ((xl, caps[0]), (xr, caps[1])):
        s.junction((x, y))
        s.sym("Device:C", cref, "22p", x, y + 3.81, 0, src=src,
              rdx=-2.54, vdx=-2.54, just="right")
        s.wire((x, y + 7.62), (x, y + 10.16))
        s.power("power:GND", (x, y + 10.16))

    # корпус 4-выводного кварца — на землю (выводы 2 и 4 объединены в символе)
    if lib_id.endswith("Crystal_GND24"):
        s.wire(q.pin("2"), (cx, y + 10.16))
        s.power("power:GND", (cx, y + 10.16))

    # резистор смещения — параллельно кварцу, сверху
    if bias:
        ref_r, val_r = bias
        r = s.sym("Device:R", ref_r, val_r, cx, y - 11.43, 90, src=src,
                  rdx=7.62, rdy=-1.27, vdx=7.62, vdy=1.905)
        for pin, x in ((r.pin("1"), cx - 3.81), (r.pin("2"), cx + 3.81)):
            s.wire(pin, (x, pin[1]), (x, y))
            s.junction((x, y))      # тут сходятся вывод кварца и два провода
    return q


def reset(s):
    """Подтяжка на +1V8, ёмкость и кнопка — 07-clock-reset.md §6.2."""
    src = f"{DOC} §6.2"
    X, y = 170.18, ROW

    s.power("power:+1V8", (X, 292.1))
    r = s.sym("Device:R", "R19", "10k", X, 298.45, 0, src=src)
    s.wire((X, 292.1), r.pin("1"))

    s.wire((X, y), (180.34, y))
    s.wire((180.34, y), (190.5, y))
    s.junction((X, y))
    s.junction((180.34, y))
    s.glabel("RESET", (190.5, y), 0)

    s.sym("Device:C", "C19", "0.1u", X, y + 3.81, 0, src=src)
    s.wire((X, y + 7.62), (X, y + 10.16))
    s.power("power:GND", (X, y + 10.16))

    sw = s.sym("Jumper:SolderJumper_2_Open", "JP1", "RESET", 180.34, y + 7.62, 90,
               src="ТЗ, «Кнопки служебные»",
               # Не кнопка, а пара площадок под пинцет. Сброс кнопкой избыточен:
               # движок снимает `EN` со всех трёх баков разом, а это жёстче, чем
               # импульс на выводе 27. Серийные консоли кнопки сброса не носят
               # вовсе. Разбор — 03-usb.md §1.
               fp="Jumper:SolderJumper-2_P1.3mm_Open_Pad1.0x1.5mm",
               rdx=2.54, rdy=-1.27, vdx=2.54, vdy=1.905)
    s.wire((180.34, y), sw.pin("2"))
    s.wire(sw.pin("1"), (180.34, y + 15.24))
    s.power("power:GND", (180.34, y + 15.24))


def main():
    s = Sheet(root_uuid(), 7, FRAME)

    crystal(s, 60.96, "Device:Crystal_GND24", "Y1", "24 МГц",
            "DXOUT", "DXIN", ("C15", "C16"), f"{DOC} §6.1",
            "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm_HandSoldering")
    s.note("24 МГц: 22/2 + 6.5 = 17.5 пФ при CL 18 пФ (§6.1)",
           (35.56, 287.02), 1.4)

    crystal(s, 121.92, "Device:Crystal", "Y2", "32.768 кГц",
            "X32KOUT", "X32KIN", ("C17", "C18"), f"{DOC} §6.3",
            "Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm",
            bias=("R20", "680k"))
    s.note("32.768 кГц: 22/2 + 1.1 = 12.1 пФ при CL 12.5 (§6.3)",
           (99.06, 287.02), 1.4)

    reset(s)
    s.note("Подтяжка на +1V8 — домен вывода 27 (§6.2)",
           (160.02, 325.12), 1.4)
    s.note("Ёмкости обоих кварцев — NP0/C0G, не X7R (§7.2)",
           (35.56, 325.12), 1.4)
    s.note("R20 задаёт рабочую точку DCXO: 680k по MangoPi, у Xassette 10M (§5)",
           (99.06, 331.47), 1.4)

    write(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
