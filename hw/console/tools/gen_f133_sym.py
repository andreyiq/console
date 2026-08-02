#!/usr/bin/env python3
"""Собирает символ F133-A для KiCad 9 из распиновки даташита.

Запуск:  python3 hw/console/tools/gen_f133_sym.py
Пишет:   hw/console/lib/console.kicad_sym

Распиновка взята из F133_Datasheet_V1.2.pdf, таблица 4-2, и сверена с
hw/ref/Xassette-Asterisk/hw/lib/F133.lib. Расхождения с библиотекой Xassette
(исправлены здесь): пин 47 в даташите NC, а не DZQ; пин 51 — VDD-SYS1, а не
второй VDD-SYS0; пин 25 — X32KIN, а не XX32KIN.

## Раскладка

Символ повторяет корпус, как это сделано в схеме MangoPi
(`docs/mangopi/mq_sch_v1.6.pdf`, стр. 3): один юнит, выводы по четырём
сторонам в порядке нумерации eLQFP128 — 1..32 слева сверху вниз, 33..64 снизу
слева направо, 65..96 справа снизу вверх, 97..128 сверху справа налево. Это
проверено по футпринту (`лапка 33` в левом нижнем углу, `97` в правом верхнем).

Плюс такого символа: он читается как сам чип, и при разводке видно, с какой
стороны корпуса выходит цепь. Функциональные зоны нарисованы поверх — короткой
линией от края и подписью, тоже как у MangoPi.
"""

from pathlib import Path

MM = 2.54          # шаг выводов, 100 mil
PIN_LEN = 2.54
PIN_MARGIN = 4     # пустых шагов от угла корпуса до первого вывода стороны
NAME_ZONE = 14.0   # полоса вдоль края, занятая именами выводов
ZONE_LINE = 22.86  # насколько линия-разделитель зоны заходит внутрь корпуса
ZONE_TEXT = 25.4   # отступ подписи зоны от края — левая и правая стороны
ZONE_TEXT_TB = 33.02  # то же для верха и низа: глубина другая, иначе в углах
                      # подпись низа садится ровно на подпись правой стороны

# PIN_MARGIN нужен из-за углов: имя вывода уходит внутрь корпуса примерно на
# 10 мм, поэтому без отступа первый вывод верхней стороны пишется поверх
# первого вывода левой (EPAD × PG6, PE13 × PE3). Четыре шага = 10.16 мм.
FOOTPRINT = "console:eLQFP-128_14x14_Pitch0.4mm_EPAD_5.72mm"

# (номер, имя, тип). Тип: W power_in, w power_out, I input, O output,
# B bidirectional, N no_connect, P passive
PINS = [
    (1, "PG6", "B"), (2, "PG7", "B"), (3, "PG8", "B"), (4, "PG9", "B"),
    (5, "PG10", "B"), (6, "PG11", "B"),
    (7, "PF0", "B"), (8, "PF1", "B"), (9, "PF2", "B"), (10, "PF3", "B"),
    (11, "PF4", "B"), (12, "PF5", "B"), (13, "PF6", "B"),
    (14, "PC7", "B"), (15, "PC6", "B"), (16, "PC5", "B"), (17, "PC4", "B"),
    (18, "PC3", "B"), (19, "PC2", "B"),
    (20, "VCC-PLL", "W"),
    (21, "REFCLK-OUT", "O"), (22, "DXOUT", "O"), (23, "DXIN", "I"),
    (24, "X32KOUT", "O"), (25, "X32KIN", "I"),
    (26, "VCC-RTC", "W"),
    # 27: даташит «I, OD», домен VCC-RTC = 1.8 В (не 3.3!) — подтяжка на +1V8
    (27, "~{RESET}", "B"),
    (28, "LDOA-OUT", "w"), (29, "LDO-IN", "W"), (30, "LDOB-OUT", "w"),
    (31, "PE13", "B"), (32, "PE12", "B"), (33, "PE3", "B"),
    (34, "VCC-PE", "W"),
    (35, "PE2", "B"), (36, "PE11", "B"), (37, "PE10", "B"), (38, "PE9", "B"),
    (39, "PE8", "B"), (40, "PE7", "B"), (41, "PE6", "B"), (42, "PE5", "B"),
    (43, "PE4", "B"), (44, "PE0", "B"), (45, "PE1", "B"),
    # 47: даташит (Table 4-2, секция «NC») говорит NC, но обе референсные платы
    # вешают на него 240 Ω на землю и называют DZQ. Тип passive, чтобы резистор
    # можно было нарисовать; разбор — blocks/08-decoupling.md §5, вывод 2.
    (46, "VDD-SYS0", "W"), (47, "NC/DZQ", "P"),
    (48, "VCC-DRAM0", "W"), (49, "VCC-DRAM1", "W"), (50, "VDD18-DRAM", "W"),
    (51, "VDD-SYS1", "W"),
    (52, "PD22", "B"), (53, "PD21", "B"), (54, "PD20", "B"),
    (55, "PD0", "B"), (56, "PD1", "B"), (57, "PD2", "B"), (58, "PD3", "B"),
    (59, "PD4", "B"), (60, "PD5", "B"), (61, "PD6", "B"), (62, "PD7", "B"),
    (63, "PD8", "B"), (64, "PD9", "B"),
    (65, "VCC-LVDS", "W"), (66, "VCC-PD", "W"),
    (67, "PD10", "B"), (68, "PD11", "B"), (69, "PD13", "B"), (70, "PD12", "B"),
    (71, "PD14", "B"), (72, "PD15", "B"), (73, "PD16", "B"), (74, "PD17", "B"),
    (75, "PD18", "B"), (76, "PD19", "B"),
    (77, "VCC-TVOUT", "W"), (78, "TVOUT0", "O"),
    (79, "PB7", "B"), (80, "PB6", "B"),
    (81, "VDD-SYS2", "W"),
    (82, "PB5", "B"),
    (83, "VCC-IO", "W"),
    (84, "PB4", "B"), (85, "PB3", "B"), (86, "PB2", "B"),
    (87, "MICIN3P", "I"), (88, "MICIN3N", "I"),
    (89, "AVCC", "W"), (90, "VRA2", "O"), (91, "AGND", "W"), (92, "VRA1", "O"),
    (93, "FMINR", "I"), (94, "FMINL", "I"),
    (95, "LINEINR", "I"), (96, "LINEINL", "I"),
    (97, "HPVCC", "W"), (98, "HPOUTR", "O"), (99, "HPOUTL", "O"),
    (100, "HPOUTFB", "I"), (101, "GPADC0", "I"),
    (102, "TP-X1", "I"), (103, "TP-X2", "I"), (104, "TP-Y1", "I"),
    (105, "TP-Y2", "I"),
    (106, "NC0", "N"),
    # 107..111 — блок CVBS IN, у F133-A все пять NC. Прямой источник —
    # Table 7-1 «Pin Map Difference between F133-A and F133-B» (стр. 74):
    # строка «107 | NC | VCC-TVIN». То же в сноске (1) к Table 4-2 и в тексте
    # на стр. 19. Обе референсные платы вывод 107 питают, но обе рисовались и
    # под F133-B. Разбор — blocks/08-decoupling.md §2.4 и §5.
    (107, "VCC-TVIN", "N"), (108, "TVIN0", "N"), (109, "TVIN1", "N"),
    (110, "TVIN-VRP", "N"), (111, "TVIN-VRN", "N"),
    (112, "USB1-DP", "B"), (113, "USB1-DM", "B"),
    (114, "USB0-DM", "B"), (115, "USB0-DP", "B"),
    (116, "VDD-CORE0", "W"), (117, "VDD-CORE1", "W"),
    (118, "PG1", "B"), (119, "PG2", "B"), (120, "PG0", "B"), (121, "PG3", "B"),
    (122, "PG5", "B"), (123, "PG4", "B"), (124, "PG12", "B"), (125, "PG13", "B"),
    (126, "PG14", "B"), (127, "PG15", "B"),
    (128, "VCC-PG", "W"),
    # Термопад корпуса — единственная цифровая земля чипа
    ("EPAD", "GND", "W"),
]

KI_TYPE = {
    "W": "power_in", "w": "power_out", "I": "input", "O": "output",
    "B": "bidirectional", "N": "no_connect", "P": "passive",
}

# Стороны корпуса, зоны внутри стороны: (подпись зоны, [номера выводов]).
# Порядок зон = порядок нумерации выводов вдоль стороны.
#
# Подписи держим короткими: у верха и низа зона шириной всего в пару выводов,
# и длинный текст налезает на соседнюю. У левой и правой сторон места больше,
# но и там подпись не должна дотягиваться до середины — там сидит "F133-A".
SIDES = {
    # 1..32 — левая сторона, сверху вниз
    "left": [
        ("PG · кнопки",   [1, 2, 3, 4, 5, 6]),
        ("PF · microSD",  [7, 8, 9, 10, 11, 12, 13]),
        ("PC · SPI0/NOR", [14, 15, 16, 17, 18, 19]),
        ("PLL · DCXO · RTC", [20, 21, 22, 23, 24, 25, 26, 27]),
        ("LDO",           [28, 29, 30]),
        ("PE",            [31, 32]),
    ],
    # 33..64 — низ, слева направо
    "bottom": [
        ("PE · UART/JTAG", [33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45]),
        ("SYS",            [46]),
        ("DRAM",           [47, 48, 49, 50]),
        ("SYS ",           [51]),
        ("PD · дисплей",   [52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64]),
    ],
    # 65..96 — правая сторона, снизу вверх
    "right": [
        ("PD · дисплей ", [65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76]),
        ("TVOUT",         [77, 78]),
        ("PB",            [79, 80, 81, 82, 83, 84, 85, 86]),
        ("аудиокодек",    [87, 88, 89, 90, 91, 92, 93, 94, 95, 96]),
    ],
    # 97..128 + EPAD — верх, справа налево
    "top": [
        ("наушники", [97, 98, 99, 100]),
        ("ADC",      [101, 102, 103, 104, 105]),
        ("CVBS·NC",  [106, 107, 108, 109, 110, 111]),
        ("USB",      [112, 113, 114, 115]),
        ("CORE",     [116, 117]),
        ("PG",       [118, 119, 120, 121, 122, 123, 124, 125, 126, 127, 128]),
        ("GND",      ["EPAD"]),
    ],
}

NAME = {p[0]: p[1] for p in PINS}
PTYPE = {p[0]: p[2] for p in PINS}


def check():
    nums = [p[0] for p in PINS]
    assert len(nums) == len(set(nums)), "дубли номеров выводов"
    assert sorted(n for n in nums if isinstance(n, int)) == list(range(1, 129)), \
        "выводы 1..128 не полны"
    placed = [n for side in SIDES.values() for _, zone in side for n in zone]
    assert len(placed) == len(set(placed)), \
        f"вывод размещён дважды: {[n for n in set(placed) if placed.count(n) > 1]}"
    assert set(placed) == set(nums), \
        f"не размещены: {sorted(set(nums) - set(placed), key=str)}"
    # порядок вдоль стороны обязан совпадать с нумерацией корпуса
    for side, zones in SIDES.items():
        seq = [n for _, zone in zones for n in zone]
        ints = [n for n in seq if isinstance(n, int)]
        assert ints == sorted(ints), f"сторона {side}: выводы не по порядку"


def slots(zones):
    """Позиции выводов вдоль стороны с пустым слотом между зонами.

    Возвращает (всего слотов, [(индекс слота, номер вывода)],
                [(индекс слота, подпись зоны, центр зоны в слотах)]).
    """
    pos, pins, marks = 0, [], []
    for i, (label, zone) in enumerate(zones):
        if i:
            pos += 1                      # пустой слот = разделитель
        marks.append((pos - 0.5 if i else None, label, pos + (len(zone) - 1) / 2))
        for n in zone:
            pins.append((pos, n))
            pos += 1
    return pos, pins, marks


def pin(name, num, x, y, rotation):
    return f"""			(pin {KI_TYPE[PTYPE[num]]} line
				(at {x:g} {y:g} {rotation})
				(length {PIN_LEN:g})
				(name "{name}"
					(effects (font (size 1.27 1.27)))
				)
				(number "{num}"
					(effects (font (size 1.27 1.27)))
				)
			)
"""


def line(x1, y1, x2, y2):
    return f"""			(polyline
				(pts (xy {x1:g} {y1:g}) (xy {x2:g} {y2:g}))
				(stroke (width 0.1524) (type default))
				(fill (type none))
			)
"""


def label(text, x, y, justify=None):
    """Подпись зоны. justify=None — по центру (у верхней и нижней сторон)."""
    j = f"\n\t\t\t\t\t(justify {justify})" if justify else ""
    return f"""			(text "{text}"
				(at {x:g} {y:g} 0)
				(effects
					(font (size 1.27 1.27) (italic yes)){j}
				)
			)
"""


def prop(name, value, x, y, hide=False, justify=None):
    j = f"\n\t\t\t\t(justify {justify})" if justify else ""
    h = "\n\t\t\t\t(hide yes)" if hide else ""
    return f"""		(property "{name}" "{value}"
			(at {x:g} {y:g} 0)
			(effects
				(font (size 1.27 1.27)){j}{h}
			)
		)
"""


def build():
    n_left, pins_l, marks_l = slots(SIDES["left"])
    n_right, pins_r, marks_r = slots(SIDES["right"])
    n_top, pins_t, marks_t = slots(SIDES["top"])
    n_bot, pins_b, marks_b = slots(SIDES["bottom"])

    h = (max(n_left, n_right) + 2 * PIN_MARGIN) * MM
    w = (max(n_top, n_bot) + 2 * PIN_MARGIN) * MM
    hw, hh = w / 2, h / 2

    def off(i):
        """Отступ вывода со слотом i от начала стороны. Кратен 1.27 мм."""
        return (PIN_MARGIN + i + 0.5) * MM

    def clamp(x, text):
        """Не даём подписи зоны верха/низа заехать на имена боковых выводов."""
        half = len(text) * 0.45
        return max(-hw + NAME_ZONE + half, min(hw - NAME_ZONE - half, x))

    out = [f"""			(rectangle
				(start {-hw:g} {hh:g})
				(end {hw:g} {-hh:g})
				(stroke (width 0.254) (type default))
				(fill (type background))
			)
"""]

    # выводы 1..32: левая сторона, сверху вниз
    for i, n in pins_l:
        out.append(pin(NAME[n], n, -hw - PIN_LEN, hh - off(i), 0))
    # 33..64: низ, слева направо
    for i, n in pins_b:
        out.append(pin(NAME[n], n, -hw + off(i), -hh - PIN_LEN, 90))
    # 65..96: правая сторона, снизу вверх
    for i, n in pins_r:
        out.append(pin(NAME[n], n, hw + PIN_LEN, -hh + off(i), 180))
    # 97..128 + EPAD: верх, справа налево
    for i, n in pins_t:
        out.append(pin(NAME[n], n, hw - off(i), hh + PIN_LEN, 270))

    # разделители зон и подписи
    for sep, text, mid in marks_l:
        if sep is not None:
            y = hh - off(sep)
            out.append(line(-hw, y, -hw + ZONE_LINE, y))
        out.append(label(text, -hw + ZONE_TEXT, hh - off(mid), "left"))
    for sep, text, mid in marks_r:
        if sep is not None:
            y = -hh + off(sep)
            out.append(line(hw, y, hw - ZONE_LINE, y))
        out.append(label(text, hw - ZONE_TEXT, -hh + off(mid), "right"))
    for sep, text, mid in marks_b:
        if sep is not None:
            x = -hw + off(sep)
            out.append(line(x, -hh, x, -hh + ZONE_LINE))
        out.append(label(text, clamp(-hw + off(mid), text), -hh + ZONE_TEXT_TB))
    for sep, text, mid in marks_t:
        if sep is not None:
            x = hw - off(sep)
            out.append(line(x, hh, x, hh - ZONE_LINE))
        out.append(label(text, clamp(hw - off(mid), text), hh - ZONE_TEXT_TB))

    return "".join(out), w, h


def main():
    check()
    draw, w, h = build()
    hw, hh = w / 2, h / 2

    body = [
        '	(symbol "F133-A"\n',
        "		(pin_names (offset 0.508))\n",
        "		(exclude_from_sim no)\n		(in_bom yes)\n		(on_board yes)\n",
        # justify не указываем: по центру — это значение по умолчанию, а явный
        # (justify center) парсер библиотеки символов не принимает
        prop("Reference", "U", -hw, hh + 6.35, justify="left bottom"),
        prop("Value", "F133-A", 0, 0),
        prop("Footprint", FOOTPRINT, 0, -3.81, hide=True),
        prop("Datasheet",
             "docs/Allwinner-SoC/Allwinner D1s-F133 RISC-V/F133_Datasheet_V1.2.pdf",
             0, -6.35, hide=True),
        prop("Description",
             "Allwinner F133-A, RISC-V C906 @1GHz, DDR2 64MB in package, eLQFP-128",
             0, -8.89, hide=True),
        prop("ki_keywords", "Allwinner F133 D1s RISC-V SiP", 0, 0, hide=True),
        prop("ki_fp_filters", "eLQFP*14x14*0.4mm*", 0, 0, hide=True),
        f'		(symbol "F133-A_1_1"\n{draw}		)\n',
        "		(embedded_fonts no)\n	)\n",
    ]

    text = (
        "(kicad_symbol_lib\n"
        "	(version 20241209)\n"
        '	(generator "gen_f133_sym.py")\n'
        '	(generator_version "9.0")\n'
        + "".join(body) +
        ")\n"
    )
    out = Path(__file__).resolve().parent.parent / "lib" / "console.kicad_sym"
    out.write_text(text)
    print(f"{out}: {len(PINS)} выводов, корпус {w:g} × {h:g} мм")
    sync_schematic()


def sync_schematic():
    """Обновить копию символа внутри console.kicad_sch.

    KiCad держит в самой схеме копию каждого символа (секция `lib_symbols`),
    и библиотеку при открытии не перечитывает. Значит правка распиновки,
    не доехавшая до схемы, разъезжается молча: в библиотеке вывод 107 уже NC,
    а на листе всё ещё питание. Поэтому генератор чинит обе копии сразу.
    """
    import kicadsch

    kicadsch._LIB_CACHE.clear()
    body = kicadsch.symbol_def("console:F133-A")
    body = "\n".join("\t" + ln if ln.strip() else ln for ln in body.split("\n"))

    sch = kicadsch.SCH
    text = sch.read_text()
    start = text.index('(symbol "console:F133-A"')
    depth, i = 0, start
    while True:
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        elif c == '"':
            i += 1
            while text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        i += 1
    if text[start:i + 1] == body.strip():
        print(f"{sch.name}: копия символа уже совпадает")
        return
    sch.write_text(text[:start] + body.strip() + text[i + 1:])
    print(f"{sch.name}: копия символа обновлена")


if __name__ == "__main__":
    main()
