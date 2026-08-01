#!/usr/bin/env python3
"""Создаёт каркас схемы KiCad 9 в hw/console/: один лист A2, F133 и рамки блоков.

Запуск:  python3 hw/console/tools/scaffold.py

Одноразовый скрипт: он пишет файлы только если их нет (иначе пропускает), чтобы
не затереть схему, которую уже правили руками. Дальше лист редактируется
напрямую — в KiCad или текстом.

UUID детерминированные (uuid5 от строкового ключа), поэтому повторный запуск
на чистой папке даёт побайтово тот же результат.

Раскладка сделана как на стр. 3 схемы MangoPi (docs/mangopi/mq_sch_v1.6.pdf):
F133 целиком в середине листа, обвязка вокруг него блоками — каждый блок с той
стороны корпуса, с которой выходят его выводы. Соединяется всё глобальными
метками, проводов между блоками нет.
"""

import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NS = uuid.UUID("6f9b1c2e-0000-4000-8000-000000000001")

PROJECT = "console"
PAPER = "A2"          # 594 × 420 мм
REV = "v2"
TITLE = "Консоль v2 — F133-A"

# Центр F133 на листе. Обе координаты обязаны быть кратны 1.27 мм, иначе выводы
# уедут с сетки и ERC начнёт ругаться endpoint_off_grid (середина A2 — 297.0,
# это не кратно, поэтому берём ближайшее 297.18 = 117 × 2.54).
F133_AT = (297.18, 203.2)


def uid(key):
    return str(uuid.uuid5(NS, key))


# Блоки: номер, имя, рамка (x1, y1, x2, y2) в мм.
#
# Каждый блок стоит с той стороны корпуса, откуда выходят его выводы —
# как на стр. 3 MangoPi. Символ занимает x 246..348, y 155..252, с учётом
# выводов и номеров — примерно x 236..358, y 145..261, туда рамки не лезут.
#
#   лево-верх   1..6    PG        → кнопки (плюс 118..128 в левой части верха)
#   лево        7..19   PF, PC    → microSD и SPI NOR
#   лево-низ    20..30  PLL/RTC   → кварц, RESET, LDO
#   низ-лево    31..45  PE        → UART, JTAG, светодиоды
#   низ-центр   46..51  SYS/DRAM  → развязка и контрольные точки
#   низ-право   52..76  PD        → дисплей (продолжается на правой стороне)
#   право-верх  87..100 аудио     → PAM8301
#   верх        101..117          → ADC, USB, CORE
BLOCKS = [
    (1, "КНОПКИ — 10 игровых + Reset",
     20.0, 20.0, 218.0, 130.0),
    (2, "ПИТАНИЕ — 3 × SY8089AAAC, TP4056, движок, GPADC0",
     226.0, 20.0, 448.0, 130.0),
    (3, "USB и FEL — USB-C 16P, USBLC6-2SC6",
     456.0, 20.0, 574.0, 130.0),
    (4, "ХРАНЕНИЕ — W25Q32, microSD, boot-страпы",
     20.0, 140.0, 218.0, 266.0),
    (5, "ЗВУК — PAM8301, динамик",
     378.0, 140.0, 574.0, 226.0),
    (6, "ДИСПЛЕЙ — FFC-40, обе шины, IM_SEL, подсветка",
     378.0, 234.0, 574.0, 344.0),
    (7, "ТАКТ и СБРОС — кварц 24 МГц, RESET на +1V8",
     20.0, 274.0, 218.0, 344.0),
    (8, "РАЗВЯЗКА и КОНТРОЛЬНЫЕ ТОЧКИ",
     226.0, 278.0, 368.0, 344.0),
    (9, "UART, JTAG, СВЕТОДИОДЫ — PE2/PE3, PE4..PE7",
     20.0, 352.0, 368.0, 400.0),
]


def read_symbol_def(name):
    """Вырезает (symbol "name" ...) из библиотеки — для секции lib_symbols."""
    text = (ROOT / "lib" / "console.kicad_sym").read_text()
    start = text.index(f'(symbol "{name}"')
    depth, i = 0, start
    while True:
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                break
        elif text[i] == '"':
            i += 1
            while text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        i += 1
    block = text[start:i + 1]
    block = block.replace(f'(symbol "{name}"', f'(symbol "{PROJECT}:{name}"', 1)
    return "\n".join("\t" + ln if ln.strip() else ln for ln in block.split("\n"))


def symbol_geometry(name):
    """(номера выводов, левый край, верхний край) — для подписей над корпусом."""
    text = (ROOT / "lib" / "console.kicad_sym").read_text()
    m = re.search(r'\(symbol "%s_1_1"(.*?)\n\t\t\)\n' % re.escape(name),
                  text, re.S)
    body = m.group(1)
    rect = re.search(r"\(start (-?[\d.]+) (-?[\d.]+)\)", body)
    return (re.findall(r'\(number "([^"]+)"', body),
            float(rect.group(1)), float(rect.group(2)))


def prop(name, value, x, y, hide=False, justify="left bottom"):
    h = "\n\t\t\t\t(hide yes)" if hide else ""
    j = f"\n\t\t\t\t(justify {justify})" if justify else ""
    return f"""		(property "{name}" "{value}"
			(at {x:g} {y:g} 0)
			(effects
				(font (size 1.27 1.27)){j}{h}
			)
		)
"""


def block_frame(num, name, x1, y1, x2, y2):
    """Рамка блока и заголовок. Графика — на цепи и netlist не влияет."""
    return f"""	(rectangle
		(start {x1:g} {y1:g})
		(end {x2:g} {y2:g})
		(stroke (width 0.2) (type dash))
		(fill (type none))
		(uuid "{uid(f'block/{num}')}")
	)
	(text "{num}. {name}"
		(exclude_from_sim yes)
		(at {x1 + 2.54:g} {y1 - 1.27:g} 0)
		(effects
			(font (size 2.54 2.54) (bold yes))
			(justify left bottom)
		)
		(uuid "{uid(f'block-title/{num}')}")
	)
"""


def f133_instance(x, y, pins, box_left, box_top):
    pin_lines = "".join(
        f'\t\t(pin "{n}"\n\t\t\t(uuid "{uid(f"pin/U1/{n}")}")\n\t\t)\n'
        for n in pins)
    # Y в символе растёт вверх, на листе — вниз, отсюда минус
    fx, fy = x + box_left, y - box_top
    return f"""	(symbol
		(lib_id "{PROJECT}:F133-A")
		(at {x:g} {y:g} 0)
		(unit 1)
		(exclude_from_sim no)
		(in_bom yes)
		(on_board yes)
		(dnp no)
		(uuid "{uid('sym/U1')}")
{prop("Reference", "U1", fx, fy - 6.35)}{prop("Value", "F133-A", x, y, justify=None)}{prop("Footprint", "console:eLQFP-128_14x14_Pitch0.4mm_EPAD_5.72mm", x, y - 3.81, hide=True, justify=None)}{pin_lines}		(instances
			(project "{PROJECT}"
				(path "/{uid('root')}"
					(reference "U1")
					(unit 1)
				)
			)
		)
	)
"""


def write(path, text):
    rel = path.relative_to(ROOT.parent.parent)
    if path.exists():
        print(f"пропущен (уже есть): {rel}")
        return False
    path.write_text(text)
    print(f"создан: {rel}")
    return True


def main():
    pins, box_left, box_top = symbol_geometry("F133-A")
    symdef = read_symbol_def("F133-A")

    sch = f"""(kicad_sch
	(version 20250114)
	(generator "scaffold.py")
	(generator_version "9.0")
	(uuid "{uid('root')}")
	(paper "{PAPER}")
	(title_block
		(title "{TITLE}")
		(rev "{REV}")
		(comment 1 "Требования: hw/TZ.md   Числа и распиновка: hw/BOARD.md   Выбор микросхем: hw/CHIPS.md")
		(comment 2 "Соединения — глобальными метками. Порядок сборки блоков — BOARD.md §9")
	)
	(lib_symbols
{symdef}
	)
"""
    sch += "".join(block_frame(*b) for b in BLOCKS)
    sch += f133_instance(*F133_AT, pins, box_left, box_top)
    sch += f'	(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n\t\t)\n\t)\n'
    sch += "	(embedded_fonts no)\n)\n"
    write(ROOT / f"{PROJECT}.kicad_sch", sch)

    write(ROOT / "sym-lib-table",
          '(sym_lib_table\n  (version 7)\n'
          f'  (lib (name "{PROJECT}")(type "KiCad")'
          '(uri "${KIPRJMOD}/lib/console.kicad_sym")(options "")'
          '(descr "Символы этой платы"))\n)\n')
    write(ROOT / "fp-lib-table",
          '(fp_lib_table\n  (version 7)\n'
          f'  (lib (name "{PROJECT}")(type "KiCad")'
          '(uri "${KIPRJMOD}/lib/console.pretty")(options "")'
          '(descr "Футпринты этой платы"))\n)\n')
    return 0


if __name__ == "__main__":
    sys.exit(main())
