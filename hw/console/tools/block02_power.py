#!/usr/bin/env python3
"""Рисует блок 2 «ПИТАНИЕ» в hw/console/console.kicad_sch.

Запуск:  python3 hw/console/tools/block02_power.py

Скрипт идемпотентный: перед вставкой выкидывает из схемы всё, что лежит внутри
рамки блока 2, а рамку и заголовок не трогает. Значит его можно гонять сколько
угодно раз, и правки в других блоках при этом не страдают.

Все номиналы — из hw/console/blocks/02-power.md, ссылка на раздел лежит в поле
`Источник` каждого компонента: правило проекта «ни одного номинала без ссылки на
источник» проверяется прямо по схеме.

Общая машинерия (координаты выводов, свойства, провода, вставка в файл) — в
tools/kicadsch.py, там же описаны грабли KiCad, на которые уже наступили.
Символы берутся из системных библиотек, кроме `parts:SY8089AAAC` и рельс
`parts:VSYS` / `parts:+0V9` — их нет ни в одной штатной библиотеке.
"""

import sys

from kicadsch import Sheet, root_uuid, write

# Рамка блока 2 из tools/scaffold.py — по ней чистится старое содержимое.
FRAME = (226.0, 20.0, 448.0, 130.0)


# --------------------------------------------------------------------- блоки

def buck(s, X, Y, rail, en_label, refs, r1, r2, c22pf, purpose):
    """Один канал: SY8089AAAC + L + CIN/COUT + делитель FB + перемычка + TP."""
    u, l, cin, cout, c01, rt, rb, jp, tp = refs
    src = "02-power.md §6.1"
    hor = dict(rdx=0.0, rdy=-3.81, vdx=0.0, vdy=4.45, just=None)

    ch = s.sym("parts:SY8089AAAC", u, "SY8089AAAC", X, Y, 0,
               src=src, lcsc="C78988",
               rdx=-5.08, rdy=-6.35, vdx=-5.08, vdy=-8.89)

    # вход: VSYS, развязка CIN
    s.power("parts:VSYS", (X - 25.4, Y - 12.7))
    s.wire((X - 25.4, Y - 12.7), (X - 25.4, Y - 2.54), ch.pin("4"))
    cinp = s.sym("Device:C", cin, "22u", X - 25.4, Y + 1.27, 0, src="02-power.md §2.4",
                 rdy=1.27, vdy=4.45)
    s.junction((X - 25.4, Y - 2.54))
    s.wire(cinp.pin("2"), (X - 25.4, Y + 7.62))
    s.power("power:GND", (X - 25.4, Y + 7.62))

    # включение
    s.wire(ch.pin("1"), (X - 12.7, Y))
    s.glabel(en_label, (X - 12.7, Y), 180)

    # земля микросхемы
    s.wire(ch.pin("2"), (X, Y + 10.16))
    s.power("power:GND", (X, Y + 10.16))

    # дроссель и выходная шина
    ind = s.sym("Device:L", l, "2.2u", X + 13.97, Y - 2.54, 90, src=src, **hor)
    s.wire(ch.pin("3"), ind.pin("1"))

    taps = [X + 20.32, X + 25.4, X + 33.02, X + 40.64, X + 45.72]
    xs = [X + 17.78] + taps + [X + 49.53]
    for a, b in zip(xs, xs[1:]):
        s.wire((a, Y - 2.54), (b, Y - 2.54))
    for tap in taps:
        if tap == X + 20.32 and not c22pf:
            continue
        s.junction((tap, Y - 2.54))

    s.sym("Device:C", cout, "22u", X + 33.02, Y + 1.27, 0, src="02-power.md §2.4")
    s.wire((X + 33.02, Y + 5.08), (X + 33.02, Y + 7.62))
    s.power("power:GND", (X + 33.02, Y + 7.62))
    s.sym("Device:C", c01, "0.1u", X + 40.64, Y + 1.27, 0, src="02-power.md §3, §4")
    s.wire((X + 40.64, Y + 5.08), (X + 40.64, Y + 7.62))
    s.power("power:GND", (X + 40.64, Y + 7.62))

    # обратная связь: делитель и возврат на FB
    s.sym("Device:R", rt, r1, X + 25.4, Y + 1.27, 0, src=src)
    s.sym("Device:R", rb, r2, X + 25.4, Y + 8.89, 0, src=src)
    s.junction((X + 25.4, Y + 5.08))
    s.wire((X + 25.4, Y + 12.7), (X + 25.4, Y + 15.24))
    s.power("power:GND", (X + 25.4, Y + 15.24))
    # провод обязан быть разрезан там, где стоит junction, иначе KiCad
    # разрывает цепь: половины провода оказываются в разных узлах
    back = [ch.pin("5"), (X + 15.24, Y), (X + 15.24, Y + 5.08)]
    if c22pf:
        back.append((X + 20.32, Y + 5.08))
    back.append((X + 25.4, Y + 5.08))
    s.wire(*back)

    if c22pf:
        s.sym("Device:C", c22pf, "22p", X + 20.32, Y + 1.27, 0,
              src="02-power.md §2.4, §7.5", rdx=-2.54, rdy=5.72, vdx=-2.54,
              vdy=8.26, just="right")
        s.junction((X + 20.32, Y + 5.08))

    # контрольная точка, перемычка и сама рельса
    s.sym("Connector:TestPoint", tp, rail, X + 45.72, Y - 2.54, 0,
          src="ТЗ, требование 5", show_value=False,
          rdx=0.0, rdy=-5.08, just=None)
    s.sym("Device:R", jp, "0", X + 53.34, Y - 2.54, 90,
          src="ТЗ, требование 5",
          rdx=0.0, rdy=3.81, vdx=0.0, vdy=6.35, just=None)
    s.wire((X + 57.15, Y - 2.54), (X + 57.15, Y - 7.62))
    s.power("parts:+0V9" if rail == "+0V9" else f"power:{rail}",
            (X + 57.15, Y - 7.62))
    s.note(purpose, (X + 12.7, Y - 9.53), 1.4)


def sequencer(s):
    """Движок, две ступени EN и RC для 0.9 В — 02-power.md §6.2."""
    src = "02-power.md §6.2"
    # Движок — переключатель на три вывода (MSK-12C02, §6.2): общий вывод 2,
    # крайние 1 и 3. Символ повёрнут на 180°, поэтому общий смотрит вправо, на
    # узел `PWR_EN`, а `VSYS` заходит слева в вывод 3. Вывод 1 никуда не идёт:
    # в выключенном положении `PWR_EN` садится на него и висит, а вниз его
    # тянет `R9` 1M — этого и требует даташит SY8089 («Do not float», §2.4).
    s.power("parts:VSYS", (325.12, 30.48))
    sw = s.sym("Switch:SW_SPDT", "SW1", "Движок", 330.2, 38.1, 180,
               src="CHIPS.md §7", rdx=0.0, rdy=-6.35,
               vdx=0.0, vdy=6.99, just=None,
               fp="Button_Switch_SMD:SW_SPDT_Shouhan_MSK12C02")
    s.wire((325.12, 30.48), sw.pin("3"))
    s.no_connect(sw.pin("1"))

    s.wire((335.28, 33.02), (335.28, 35.56), (335.28, 38.1),
           (335.28, 40.64), (335.28, 45.72))
    s.label("PWR_EN", (335.28, 33.02))
    s.junction((335.28, 35.56))
    s.junction((335.28, 38.1))
    s.junction((335.28, 40.64))

    # подписи двух параллельных ступеней разведены в разные стороны:
    # между резисторами всего 5.08 мм, иначе текст ложится на соседа
    for y, ref, lbl, up in ((35.56, "R7", "EN_3V3", True),
                            (40.64, "R8", "EN_1V8", False)):
        r = s.sym("Device:R", ref, "1k", 345.44, y, 90, src=src, just=None,
                  rdx=0.0, rdy=-3.81 if up else 6.99,
                  vdx=0.0, vdy=-6.35 if up else 4.45)
        s.wire((335.28, y), r.pin("1"))
        s.wire(r.pin("2"), (354.33, y))
        s.glabel(lbl, (354.33, y), 0)

    s.sym("Device:R", "R9", "1M", 335.28, 49.53, 0, src=src)
    s.wire((335.28, 53.34), (335.28, 55.88))
    s.power("power:GND", (335.28, 55.88))

    s.power("power:+3V3", (325.12, 60.96))
    r10 = s.sym("Device:R", "R10", "47k", 331.47, 66.04, 90, src=src,
                rdx=0.0, rdy=4.45, vdx=0.0, vdy=6.99, just=None)
    s.wire((325.12, 60.96), (325.12, 66.04), r10.pin("1"))
    s.wire(r10.pin("2"), (340.36, 66.04))
    s.wire((340.36, 66.04), (345.44, 66.04))
    s.junction((340.36, 66.04))
    s.glabel("EN_0V9", (345.44, 66.04), 0)
    s.sym("Device:C", "C11", "0.47u", 340.36, 69.85, 0, src=src)
    s.wire((340.36, 73.66), (340.36, 76.2))
    s.power("power:GND", (340.36, 76.2))
    s.note("T1 = 2.9…13.4 мс > 2 мс (§6.2)", (325.12, 88.9), 1.6)


def charger(s):
    """TP4056 по типовой схеме №2 — 02-power.md §6.4."""
    src = "02-power.md §6.4"
    TX, TY = 396.24, 48.26
    hor = dict(rdx=0.0, rdy=-3.81, vdx=0.0, vdy=-6.35, just=None)
    # Корпус берём БЕЗ заложенных термопереходных: в штатном варианте
    # `..._ThermalVias` они со сверлом 0.2 мм, а наш процесс — ЧПУ-сверловка и
    # заклёпки из проволоки, мельче 0.5 мм не делаем (10-mech.md §5). Прошивку
    # пятака ставим свою при разводке.
    u5 = s.sym("Battery_Management:TP4056-42-ESOP8", "U5", "TP4056",
               TX, TY, 0, src=src, lcsc="C16581",
               fp="Package_SO:SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm",
               rdx=10.16, rdy=-21.59, vdx=10.16, vdy=-24.13)

    s.power("power:VBUS", (TX, 27.94))
    s.wire((TX, 27.94), (TX, 30.48), u5.pin("4"))
    s.junction((TX, 30.48))
    s.wire((367.03, 30.48), (374.65, 30.48))
    s.wire((374.65, 30.48), (382.27, 30.48))
    s.wire((382.27, 30.48), (TX, 30.48))
    s.junction((374.65, 30.48))
    s.junction((382.27, 30.48))

    s.sym("Device:C", "C12", "10u", 374.65, 34.29, 0, src="02-power.md §2.5",
          rdx=-2.54, vdx=-2.54, just="right")
    s.wire((374.65, 38.1), (374.65, 40.64))
    s.power("power:GND", (374.65, 40.64))

    # CE на VCC — нормальный режим (§2.5)
    s.wire(u5.pin("8"), (382.27, 43.18), (382.27, 30.48))

    # индикаторы заряда. Выводы CHRG и STDBY стоят через 2.54 мм — корпуса
    # светодиодов туда не встают, поэтому нижний отнесён ниже проводом.
    # В номинале светодиода — цвет: функцию и так видно по выводу микросхемы
    # рядом, а вот по какому цвету паять, кроме как отсюда, узнать неоткуда.
    for y, dref, rref, val in ((48.26, "D1", "R12", "красный"),
                               (58.42, "D2", "R13", "зелёный")):
        d = s.sym("Device:LED", dref, val, 378.46, y, 180, src="02-power.md §2.5", **hor)
        r = s.sym("Device:R", rref, "1k", 370.84, y, 90, src="02-power.md §2.5", **hor)
        if y == 48.26:
            s.wire(u5.pin("7"), d.pin("1"))
            s.wire(r.pin("1"), (367.03, 30.48))
        else:
            s.wire(u5.pin("6"), (383.54, 50.8), (383.54, y), d.pin("1"))
            s.wire(r.pin("1"), (367.03, 48.26))
        s.wire(d.pin("2"), r.pin("2"))
    s.junction((367.03, 48.26))

    # банка
    s.wire(u5.pin("5"), (414.02, 43.18), (421.64, 43.18))
    s.junction((414.02, 43.18))
    s.power("parts:VSYS", (414.02, 35.56))
    s.wire((414.02, 43.18), (414.02, 35.56))
    s.sym("Device:C", "C13", "10u", 414.02, 46.99, 0, src="02-power.md §2.5",
          rdx=-2.54, vdx=-2.54, just="right")
    s.wire((414.02, 50.8), (414.02, 53.34))
    s.power("power:GND", (414.02, 53.34))
    # разъём и его футпринт — как на плате v1 (там J3 «Batt»), деталь есть
    # в закромах: вывод 1 на плюс банки, вывод 2 на землю
    j1 = s.sym("Connector:Conn_01x02_Pin", "J1", "Batt", 426.72, 43.18, 0,
               src="как в v1", mirror="y",
               rdx=-2.54, rdy=-8.89, vdx=-2.54, vdy=-6.35)
    s.wire(j1.pin("2"), (421.64, 50.8))
    s.power("power:GND", (421.64, 50.8))

    # TEMP на землю — термоконтроль выключен (§2.5)
    s.wire(u5.pin("1"), (408.94, 48.26), (408.94, 55.88))
    s.power("power:GND", (408.94, 55.88))

    # ток заряда
    s.wire(u5.pin("2"), (406.4, 58.42), (414.02, 58.42))
    s.sym("Device:R", "R11", "1.1k", 414.02, 62.23, 0, src="02-power.md §2.5")
    s.wire((414.02, 66.04), (414.02, 68.58))
    s.power("power:GND", (414.02, 68.58))

    # земля корпуса и термопад
    s.wire(u5.pin("9"), u5.pin("3"), (TX, 63.5))
    s.power("power:GND", (TX, 63.5))
    s.note("1.1k → 1 А, порог окончания 130 мА (§6.4)", (367.03, 78.74))


def telemetry(s):
    """Делитель банки на GPADC0 — 02-power.md §6.5."""
    src = "02-power.md §6.5"
    s.power("parts:VSYS", (378.46, 85.09))
    s.sym("Device:R", "R14", "100k", 378.46, 96.52, 0, src=src,
          rdx=-2.54, vdx=-2.54, just="right")
    s.wire((378.46, 85.09), (378.46, 92.71))
    s.sym("Device:R", "R15", "68k", 378.46, 104.14, 0, src=src,
          rdx=-2.54, vdx=-2.54, just="right")
    s.junction((378.46, 100.33))
    s.wire((378.46, 107.95), (378.46, 110.49))
    s.power("power:GND", (378.46, 110.49))
    s.wire((378.46, 100.33), (383.54, 100.33))
    s.wire((383.54, 100.33), (388.62, 100.33))
    s.junction((383.54, 100.33))
    s.glabel("GPADC0", (388.62, 100.33), 0)
    s.sym("Device:C", "C14", "0.1u", 383.54, 104.14, 0, src=src)
    s.wire((383.54, 107.95), (383.54, 110.49))
    s.power("power:GND", (383.54, 110.49))
    s.note("4.2 В → 1.70 В при шкале 0…AVCC = 1.8 В (§6.5)",
           (367.03, 121.92))


def flags(s):
    """Флаги питания.

    ERC не видит источник рельсы: от `LX` бака до самой рельсы стоят дроссель,
    делитель и перемычка, а через пассив признак «выход питания» не проходит.
    Флаг говорит ERC, что цепь запитана. В BOM не попадает (ссылка `#FLG`).
    `VBUS` намеренно без флага — её источник появится в блоке 3 вместе с USB-C.
    """
    for i, rail in enumerate(("power:+3V3", "power:+1V8", "parts:+0V9")):
        x = 320.04 + i * 10.16
        s.power(rail, (x, 99.06))
        s.wire((x, 99.06), (x, 106.68))
        s.power("power:PWR_FLAG", (x, 106.68), 180, ref=f"#FLG2{i:02d}")
    s.power("power:PWR_FLAG", (350.52, 99.06), 0, ref="#FLG203")
    s.wire((350.52, 99.06), (350.52, 106.68))
    s.power("power:GND", (350.52, 106.68))
    s.note("Флаги питания — только для ERC, в плату не идут",
           (317.5, 118.11))


def probes(s):
    """Контрольные точки на VSYS и GND — ТЗ, требование 5."""
    s.sym("Connector:TestPoint", "TP4", "VSYS", 317.5, 35.56, 180,
          src="ТЗ, требование 5", show_value=False,
          rdx=0.0, rdy=6.35, just=None)
    s.wire((317.5, 35.56), (317.5, 30.48))
    s.power("parts:VSYS", (317.5, 30.48))
    s.sym("Connector:TestPoint", "TP5", "GND", 317.5, 55.88, 0,
          src="ТЗ, требование 5", show_value=False,
          rdx=0.0, rdy=-5.08, just=None)
    s.wire((317.5, 55.88), (317.5, 60.96))
    s.power("power:GND", (317.5, 60.96))


def main():
    s = Sheet(root_uuid(), 2, FRAME)
    rows = [
        (41.91, "+3V3", "EN_3V3",
         ("U2", "L1", "C1", "C4", "C7", "R1", "R2", "R16", "TP1"),
         "680k", "150k", None, "3.3 В — VCC-IO, PD, PE, PG, TVOUT"),
        (74.93, "+1V8", "EN_1V8",
         ("U3", "L2", "C2", "C5", "C8", "R3", "R4", "R17", "TP2"),
         "300k", "150k", None, "1.8 В — DRAM, RTC, PLL, аудио"),
        (107.95, "+0V9", "EN_0V9",
         ("U4", "L3", "C3", "C6", "C9", "R5", "R6", "R18", "TP3"),
         "75k", "150k", "C10", "0.9 В — VDD-CORE, VDD-SYS"),
    ]
    for y, rail, en, refs, r1, r2, c22, purpose in rows:
        buck(s, 254.0, y, rail, en, refs, r1, r2, c22, purpose)
    sequencer(s)
    charger(s)
    telemetry(s)
    flags(s)
    probes(s)
    write(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
