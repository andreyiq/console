#!/usr/bin/env python3
"""Рисует блок 4 «ХРАНЕНИЕ» в hw/console/console.kicad_sch.

Запуск:  python3 hw/console/tools/block04_storage.py

Скрипт идемпотентный: перед вставкой выкидывает из схемы всё, что лежит внутри
рамки блока 4, а рамку и заголовок не трогает.

Все номиналы — из hw/console/blocks/04-storage.md, ссылка на раздел лежит в
поле `Источник` каждого компонента.

Раскладка та же, к которой пришли блоки 6 и 8: **микросхема и разъём получают
только метки, а весь пассив вынесен в свободное поле** и соединяется с ними по
имени цепи. Причина здесь не в шаге выводов, а в том, что подтяжек пять и
страпов четыре: развесить их вокруг восьмивыводного корпуса — значит утопить
сам корпус в проводах.

Метки у ног самого F133 ставит блок 8 (правило из 07-clock-reset.md §6.4).

Общая машинерия — в tools/kicadsch.py.
"""

import sys

from kicadsch import Sheet, root_uuid, write

# Рамка блока 4 из tools/scaffold.py — по ней чистится старое содержимое.
FRAME = (20.0, 140.0, 214.0, 266.0)

DOC = "04-storage.md"

# Все координаты кратны 1.27: иначе ERC ловит вывод детали «не на сетке».
X_LAB_L, X_R, X_LAB_R = 38.1, 60.96, 83.82    # ряд с последовательным резистором
COL0, COL_STEP = 38.1, 25.4                   # столбики подтяжек и страпов


def flash(s):
    """Флешка: метка на каждый сигнальный вывод, питание символами (§6.2)."""
    x, y = 177.8, 165.1
    # подписи уведены далеко влево: при -10.16 «W25Q32JVSSIQ» ложилась прямо
    # на провод вывода VCC, который идёт вверх от корпуса
    u = s.sym("Memory_Flash:W25Q32JVSS", "U401", "W25Q32JVSSIQ", x, y, 0,
              src=f"{DOC} §6.2", lcsc="C179173",
              rdx=-22.86, rdy=-19.05, vdx=-22.86, vdy=-16.51)

    # Порядок выводов в символе сверху вниз: 1, 6, 5, 2, 3, 7.
    for num, net in (("1", "SPI0-CS0"), ("6", "NOR-CLK"), ("5", "SPI0-MOSI"),
                     ("2", "SPI0-MISO"), ("3", "SPI0-WP"), ("7", "SPI0-HOLD")):
        s.glabel(net, u.pin(num), 180)

    s.power("power:+3V3", u.pin("8"), 0)
    s.power("power:GND", u.pin("4"), 0)


def card(s):
    """Слот microSD `Hirose DM3AT-SF-PEJM5`: метки на все контакты (§6.3).

    Детектор здесь — **изолированный ключ на двух выводах**, 9 и 10, а не
    контакт, замкнутый на корпус. Каталог Hirose (`docs/chips/
    microSD-socket_DM3-series_Hirose.pdf`, стр. 3, врезка «Card detection
    switch») даёт состояния прямым текстом: **без карты — Open, карта
    вставлена — Closed**. Поэтому 9 идёт на `SDC0-DET` с подтяжкой `R407`
    10k вверх, 10 — на землю: `DET` = 0 означает «карта на месте».

    Лапки корпуса — отдельный вывод 11, тоже на землю: у DM3AT четыре точки
    крепления и они же экран (каталог, «4-connection points of the metal cover
    … assures secure connection of the ground circuit and provides EMI
    protection»).
    """
    x, y = 177.8, 226.06
    j = s.sym("Connector:Micro_SD_Card_Det_Hirose_DM3AT", "J401",
              "DM3AT-SF-PEJM5", x, y, 0, src=f"{DOC} §6.3", lcsc="C114218",
              fp="Connector_Card:microSD_HC_Hirose_DM3AT-SF-PEJM5",
              rdx=-22.86, rdy=-19.05, vdx=-22.86, vdy=-16.51)

    for num, net in (("1", "SDC0-D2"), ("2", "SDC0-D3"), ("3", "SDC0-CMD"),
                     ("4", "+3V3"), ("5", "SD-CLK"), ("6", "GND"),
                     ("7", "SDC0-D0"), ("8", "SDC0-D1"), ("9", "SDC0-DET"),
                     ("10", "GND")):
        s.glabel(net, j.pin(num), 180)
    s.glabel("GND", j.pin("11"), 0)


def series(s):
    """Последовательные резисторы в обе линии тактирования (§6.5).

    Слева цепь со стороны F133, справа — со стороны потребителя. Шаг рядов
    12.7: подпись резистора отходит на 2.54 вверх и вниз, и при меньшем шаге
    номинал одного ряда прижимался бы к ссылке соседнего.
    """
    for y, left, ref, right in ((152.4, "SPI0-CLK", "R404", "NOR-CLK"),
                                (165.1, "SDC0-CLK", "R405", "SD-CLK")):
        s.wire((X_LAB_L, y), (X_R - 3.81, y))
        s.sym("Device:R", ref, "33", X_R, y, 90, src=f"{DOC} §6.5",
              rdx=0.0, rdy=-2.54, vdx=0.0, vdy=2.54, just=None)
        s.wire((X_R + 3.81, y), (X_LAB_R, y))
        s.glabel(left, (X_LAB_L, y), 180)
        s.glabel(right, (X_LAB_R, y), 0)


def column(s, x, y_top, rail, ref, net, sect, dnp=False, val="10k"):
    """Столбик «рельса — резистор — метка», шаг столбиков 25.4.

    У подтяжки вниз столбик перевёрнут: символ земли рисуется остриём вниз, и
    сверху колонки он читается как ошибка. Метка горизонтальная — вертикальная
    у KiCad растёт вверх, прямо в резистор.

    Номинал параметром: у страпов вниз он другой, и причина не косметическая
    (§6.4) — 10k там не пересиливает внутреннюю подтяжку `PC4`/`PC5`.
    """
    y_r, y_bot = y_top + 7.62, y_top + 15.24
    down = rail.endswith("GND")
    y_rail, y_lab = (y_bot, y_top) if down else (y_top, y_bot)
    s.power(rail, (x, y_rail))
    s.wire((x, y_top), (x, y_r - 3.81))
    s.sym("Device:R", ref, val, x, y_r, 0, src=f"{DOC} {sect}",
          rdx=-2.54, vdx=-2.54, just="right", dnp=dnp)
    s.wire((x, y_r + 3.81), (x, y_bot))
    s.glabel(net, (x, y_lab), 180)


def pulls(s):
    """Пять подтяжек 10k вверх (§6.2, §6.3).

    Три из них требует даташит флешки: `/CS` — прямым текстом (§2.6), `/WP` и
    `/HOLD` — потому что это активные низкие входы, пока QE=0.
    """
    for i, (ref, net) in enumerate((("R401", "SPI0-CS0"),
                                    ("R402", "SPI0-WP"),
                                    ("R403", "SPI0-HOLD"),
                                    ("R406", "SDC0-CMD"),
                                    ("R407", "SDC0-DET"))):
        column(s, COL0 + i * COL_STEP, 180.34, "power:+3V3", ref, net, "§6.2")


def straps(s):
    """Boot-страпы: четыре места, запаяны два (§6.4).

    Запаяны `R408` (вверх на `PC4`) и `R411` (вниз на `PC5`) — это
    `Pin_Boot_Select[1:0]` = `01`, то есть SD → SPI NOR → прочее. Два других
    места стоят под смену приоритета пайкой, как у Xassette; в схеме они
    помечены DNP.

    **Подтяжки вниз — 2.2k, а не 10k.** У `PC4` и `PC5` внутренняя подтяжка
    вверх включена сразу после сброса (Table 4-2 «Reset State PU», регистр
    `PC_PULL0` по умолчанию `0x0000_0540`), и её сопротивление 12…18 кΩ
    (Table 5-4). С 10k вниз вывод сел бы на 1.3 В при пороге `VIL` 0.99 В —
    ноль бы не читался. Разбор с числами — 04-storage.md §6.4.
    """
    cols = [
        ("R408", "power:+3V3", "SPI0-MOSI", False, "10k"),   # BOOT-SEL0 = 1
        ("R409", "power:GND", "SPI0-MOSI", True, "2.2k"),
        ("R410", "power:+3V3", "SPI0-MISO", True, "10k"),
        ("R411", "power:GND", "SPI0-MISO", False, "2.2k"),   # BOOT-SEL1 = 0
    ]
    for i, (ref, rail, net, dnp, val) in enumerate(cols):
        column(s, COL0 + i * COL_STEP, 210.82, rail, ref, net, "§6.4", dnp,
               val)


def caps(s):
    """Развязка флешки и карты (§6.6)."""
    for ref, val, x, sect in (("C401", "0.1u", COL0, "§6.6 флешка"),
                              ("C402", "0.1u", COL0 + COL_STEP, "§6.6 карта"),
                              ("C403", "10u", COL0 + 2 * COL_STEP, "§6.6, §7.5")):
        # ниже страпов: у `R409` внизу колонки земля, и её подпись отходит на
        # 5.08 вниз — при 245.11 она ложилась на «+3V3» конденсатора
        y = 250.19
        s.power("power:+3V3", (x, y - 7.62))
        s.wire((x, y - 7.62), (x, y - 3.81))
        s.sym("Device:C", ref, val, x, y, 0, src=f"{DOC} {sect}",
              rdx=-2.54, vdx=-2.54, just="right")
        s.wire((x, y + 3.81), (x, y + 7.62))
        s.power("power:GND", (x, y + 7.62))


def notes(s):
    """Подписи кладутся в просветы между рядами.

    Просвет узкий: у столбика сверху символ питания, и его текст отходит на
    5.08 вверх. Поэтому ординаты подобраны по факту — 172.72 между рядом
    резисторов и подтяжками, 201.93 между подтяжками и страпами.
    """
    s.note("шесть линий к флешке, а не четыре: SPI0 умеет Quad, "
           "PC6 и PC7 больше никому не нужны", (24.0, 172.72))
    s.note("R409 и R410 — места под смену приоритета загрузки, "
           "запаяны R408 и R411: SD → SPI NOR → прочее", (24.0, 201.93))
    s.note("посадочного места под слот в KiCad нет — рисуется по чертежу",
           (114.3, 262.0))


def main():
    s = Sheet(root_uuid(), 4, FRAME)
    flash(s)
    card(s)
    series(s)
    pulls(s)
    straps(s)
    caps(s)
    notes(s)
    write(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
