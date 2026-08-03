#!/usr/bin/env python3
"""Машинерия для скриптов, рисующих блоки схемы.

Формат `.kicad_sch` — текстовые s-выражения, и модуль их просто печатает:
никаких библиотек KiCad здесь нет. Сам по себе модуль ничего не делает, его
импортируют `blockNN_*.py`.

Зачем модуль, а не копия кода в каждом блоке: три грабли, на которые уже
наступили, лечатся в одном месте, а не в девяти.

1. **Координата вывода при повороте символа.** Чтобы провод присоединился, его
   конец обязан попасть точно в вывод. Абсолютная координата = позиция символа
   плюс смещение вывода из библиотеки, повёрнутое на угол символа. Таблица
   поворота проверена опытом (тестовая схема + netlist):

       0°   (px, -py)      90°  (-py, -px)
       180° (-px, py)      270° (py, px)

2. **`(hide yes)` без `(justify)` не работает.** KiCad 9.0.9 в этом случае всё
   равно печатает поле — так служебные ссылки `#PWR`/`#FLG` попадали в PDF.

3. **Провод обязан быть разрезан там, где к нему что-то подключается.** Иначе
   KiCad молча разрывает цепь, и на печати это неотличимо от исправной схемы.
   Модуль тут помочь не может, но выкидывает хотя бы провода нулевой длины.

UUID детерминированные: выводятся из имени элемента и номера блока, поэтому
повторный запуск даёт побайтово тот же файл, а не diff на ровном месте.
"""

import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCH = ROOT / "console.kicad_sch"
SYS = Path("/usr/share/kicad/symbols")
PROJECT = "console"

# Корпуса пассива: плату травим и паяем дома (ТЗ, строка 18), поэтому всё 0805
# в исполнении HandSolder. Символы, которых здесь нет, берут корпус из своей
# библиотеки; если и там пусто — поле остаётся пустым, и это видно в ERC.
FP = {
    "Device:R": "Resistor_SMD:R_0805_2012Metric_Pad1.20x1.40mm_HandSolder",
    "Device:C": "Capacitor_SMD:C_0805_2012Metric_Pad1.18x1.45mm_HandSolder",
    "Device:LED": "LED_SMD:LED_0805_2012Metric_Pad1.15x1.40mm_HandSolder",
    "Connector:TestPoint": "TestPoint:TestPoint_Pad_D1.5mm",
    "Connector:Conn_01x02_Pin":
        "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
}


# ---------------------------------------------------------------- библиотеки

_LIB_CACHE = {}


def lib_path(lib):
    local = ROOT / "lib" / f"{lib}.kicad_sym"
    return local if local.exists() else SYS / f"{lib}.kicad_sym"


def symbol_def(lib_id):
    """Текст (symbol "lib:name" ...) для секции lib_symbols схемы."""
    if lib_id in _LIB_CACHE:
        return _LIB_CACHE[lib_id]
    lib, name = lib_id.split(":")
    text = lib_path(lib).read_text()
    start = text.index(f'(symbol "{name}"')
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
    body = text[start:i + 1].replace(f'(symbol "{name}"',
                                     f'(symbol "{lib_id}"', 1)
    _LIB_CACHE[lib_id] = body
    return body


def lib_pins(lib_id):
    """{номер вывода: (x, y)} в координатах библиотеки."""
    body = symbol_def(lib_id)
    out = {}
    for m in re.finditer(
            r'\(pin \w+ \w+\s*\(at (-?[\d.]+) (-?[\d.]+) \d+\)\s*'
            r'\(length [\d.]+\).*?\(number "([^"]+)"', body, re.S):
        out[m.group(3)] = (float(m.group(1)), float(m.group(2)))
    return out


def rotate(px, py, rot):
    return {0: (px, -py), 90: (-py, -px),
            180: (-px, py), 270: (py, px)}[rot]


# ------------------------------------------------------------------- рисунок

class Part:
    def __init__(self, lib_id, ref, x, y, rot, mirror=""):
        self.lib_id, self.ref = lib_id, ref
        self.x, self.y, self.rot, self.mirror = x, y, rot, mirror
        self._pins = lib_pins(lib_id)

    def pin(self, num):
        dx, dy = rotate(*self._pins[num], self.rot)
        if self.mirror == "y":       # проверено опытом, только для rot 0
            dx = -dx
        return (round(self.x + dx, 4), round(self.y + dy, 4))


class Sheet:
    """Накопитель элементов одного блока.

    `block` — номер блока: он же задаёт пространство имён UUID, поэтому блоки
    не могут случайно выдать одинаковый идентификатор.
    `frame` — прямоугольник блока из tools/scaffold.py, по нему чистится старое
    содержимое при повторном запуске. Блок может добавить себе ещё зоны
    методом `zone()`: обвязка выводов F133 лежит вокруг корпуса, далеко от
    рамки, и без отдельных зон повторный запуск плодил бы дубли. Зоны делаем
    узкими — по одной на вывод, — чтобы блок не стирал чужое: у соседнего
    вывода будет рисовать уже другой блок.

    `protect` — ссылки, которые не выкидываются, даже если попали в зону.
    Символ F133 стоит ровно там, и потерять его при перезапуске нельзя.
    """

    def __init__(self, root_uuid, block, frame, protect=("U1",)):
        self.root = root_uuid
        self.block = block
        self.frames = [frame] if isinstance(frame, tuple) else list(frame)
        self.protect = set(protect)
        self.ns = uuid.UUID(f"6f9b1c2e-0000-4000-8000-{block:012d}")
        self.items = []
        self.used = []

    def zone(self, x1, y1, x2, y2):
        self.frames.append((x1, y1, x2, y2))

    def uid(self, key):
        return str(uuid.uuid5(self.ns, key))

    def _prop(self, name, value, x, y, hide, just="left", rot=0):
        # KiCad 9.0.9 не применяет (hide yes), если в (effects) нет (justify):
        # проверено опытом — скрытые ссылки #PWR/#FLG без justify всё равно
        # печатались в PDF, с ним исчезают. Для скрытого поля выравнивание ни
        # на что не влияет, поэтому просто ставим его всегда.
        if hide and not just:
            just = "left"
        h = "\n\t\t\t\t(hide yes)" if hide else ""
        j = f"\n\t\t\t\t(justify {just})" if just else ""
        # KiCad доворачивает текст свойства вместе с символом, поэтому здесь
        # компенсирующий угол — иначе подписи повёрнутых деталей встают боком
        return (f'\t\t(property "{name}" "{value}"\n'
                f'\t\t\t(at {x:g} {y:g} '
                f'{270 if rot == 90 else (90 if rot == 270 else 0)})\n'
                f'\t\t\t(effects\n\t\t\t\t(font (size 1.27 1.27))'
                f'{j}{h}\n\t\t\t)\n\t\t)\n')

    def sym(self, lib_id, ref, value, x, y, rot=0, src="", lcsc="",
            show_value=True, vdx=2.54, vdy=1.905, rdx=2.54, rdy=-1.27,
            just="left", mirror="", fp=None, dnp=False):
        if lib_id not in self.used:
            self.used.append(lib_id)
        p = Part(lib_id, ref, x, y, rot, mirror)
        if fp is None:
            m = re.search(r'\(property "Footprint" "([^"]*)"',
                          symbol_def(lib_id))
            fp = FP.get(lib_id) or (m.group(1) if m else "")
        hidden = ref.startswith("#")     # #PWR / #FLG в чертеже не нужны
        props = self._prop("Reference", ref, x + rdx, y + rdy, hidden,
                           just, rot)
        props += self._prop("Value", value, x + vdx, y + vdy,
                            not show_value, just, rot)
        props += self._prop("Footprint", fp, x, y, True)
        if src:
            props += self._prop("Источник", src, x, y, True)
        if lcsc:
            props += self._prop("LCSC", lcsc, x, y, True)
        inst = (f'\t\t(instances\n\t\t\t(project "{PROJECT}"\n'
                f'\t\t\t\t(path "/{self.root}"\n'
                f'\t\t\t\t\t(reference "{ref}")\n\t\t\t\t\t(unit 1)\n'
                f'\t\t\t\t)\n\t\t\t)\n\t\t)\n')
        self.items.append(
            f'\t(symbol\n\t\t(lib_id "{lib_id}")\n'
            f'\t\t(at {x:g} {y:g} {rot})\n'
            + (f'\t\t(mirror {mirror})\n' if mirror else "")
            + f'\t\t(unit 1)\n'
            f'\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n'
            f'\t\t(on_board yes)\n\t\t(dnp {"yes" if dnp else "no"})\n'
            f'\t\t(uuid "{self.uid("sym/" + ref)}")\n{props}{inst}\t)\n')
        return p

    def power(self, lib_id, at, rot=0, ref=None):
        # номер блока в ссылке обязателен: без него каждый блок начинал бы
        # нумерацию с нуля и выдавал чужие #PWR000 — ссылки бы совпали
        n = len([i for i in self.items if "#PWR" in i])
        ref = ref or f"#PWR{self.block}{n:02d}"
        name = lib_id.split(":")[1]
        # текст с той стороны, куда смотрит графика символа
        down = (name == "GND") != (rot == 180)
        return self.sym(lib_id, ref, name, at[0], at[1], rot,
                        vdx=0, vdy=5.08 if down else -5.08, just=None)

    def wire(self, *pts):
        for a, b in zip(pts, pts[1:]):
            if a == b:      # выводы совпали — провод между ними не нужен
                continue
            key = f"w/{a[0]:g},{a[1]:g}/{b[0]:g},{b[1]:g}"
            self.items.append(
                f'\t(wire\n\t\t(pts (xy {a[0]:g} {a[1]:g}) '
                f'(xy {b[0]:g} {b[1]:g}))\n'
                f'\t\t(stroke (width 0) (type default))\n'
                f'\t\t(uuid "{self.uid(key)}")\n\t)\n')

    def junction(self, at):
        self.items.append(
            f'\t(junction\n\t\t(at {at[0]:g} {at[1]:g})\n\t\t(diameter 0)\n'
            f'\t\t(color 0 0 0 0)\n'
            f'\t\t(uuid "{self.uid("j/%g,%g" % at)}")\n\t)\n')

    def no_connect(self, at):
        """Крестик «вывод намеренно не подключён».

        Без него ERC ругается на каждый такой вывод, и настоящие находки
        тонут в шуме. У разъёма дисплея так помечены тачскрин и два вывода,
        которые спека велит оставить в воздухе.
        """
        self.items.append(
            f'\t(no_connect\n\t\t(at {at[0]:g} {at[1]:g})\n'
            f'\t\t(uuid "{self.uid("nc/%g,%g" % at)}")\n\t)\n')

    def glabel(self, text, at, rot=0):
        self.items.append(
            f'\t(global_label "{text}"\n\t\t(shape bidirectional)\n'
            f'\t\t(at {at[0]:g} {at[1]:g} {rot})\n'
            f'\t\t(effects (font (size 1.27 1.27)) '
            f'(justify {"right" if rot == 180 else "left"}))\n'
            f'\t\t(uuid "{self.uid("gl/%s/%g,%g" % (text, at[0], at[1]))}")'
            f'\n\t)\n')

    def label(self, text, at, rot=0):
        self.items.append(
            f'\t(label "{text}"\n\t\t(at {at[0]:g} {at[1]:g} {rot})\n'
            f'\t\t(effects (font (size 1.27 1.27)) (justify left bottom))\n'
            f'\t\t(uuid "{self.uid("lb/%s/%g,%g" % (text, at[0], at[1]))}")'
            f'\n\t)\n')

    def note(self, text, at, size=1.6):
        self.items.append(
            f'\t(text "{text}"\n\t\t(exclude_from_sim yes)\n'
            f'\t\t(at {at[0]:g} {at[1]:g} 0)\n'
            f'\t\t(effects (font (size {size} {size}) (italic yes)) '
            f'(justify left bottom))\n'
            f'\t\t(uuid "{self.uid("t/" + text)}")\n\t)\n')


# ------------------------------------------------------------------- вставка

def top_items(text):
    """Разбор верхнего уровня схемы на (тип, кусок текста)."""
    i = text.index("(kicad_sch") + len("(kicad_sch")
    out, depth = [], 0
    start = None
    while i < len(text):
        c = text[i]
        if c == '"':
            i += 1
            while text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "(":
            if depth == 0:
                start = i
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                chunk = text[start:i + 1]
                out.append((re.match(r"\((\w+)", chunk).group(1), chunk))
            elif depth < 0:
                break
        i += 1
    return out


def inside(chunk, frame):
    """Лежит ли элемент в зоне — по точке привязки, а не по подписям.

    У символа своя `(at ...)` стоит раньше свойств, и берём именно её: поля
    `Reference`/`Value` отъезжают от детали на 5 мм и больше, а зоны у выводов
    F133 узкие — по одной на вывод. Считать элемент чужим из-за уехавшей
    подписи нельзя: тогда он не будет вычищен и при перезапуске задвоится.
    """
    x1, y1, x2, y2 = frame
    if re.match(r"\((?:wire|bus)\b", chunk):
        pts = re.findall(r"\(xy (-?[\d.]+) (-?[\d.]+)\)", chunk)
    else:
        m = re.search(r"\(at (-?[\d.]+) (-?[\d.]+)", chunk)
        pts = [m.groups()] if m else []
    return bool(pts) and all(x1 <= float(a) <= x2 and y1 <= float(b) <= y2
                             for a, b in pts)


def root_uuid():
    return re.search(r'\(uuid "([0-9a-f-]+)"\)', SCH.read_text()).group(1)


def write(s):
    """Переписать схему: содержимое рамки блока заменить на накопленное.

    Рамка блока и его заголовок сохраняются — их ставит tools/scaffold.py.
    Всё остальное, что лежит внутри рамки, выкидывается: поэтому скрипт
    блока можно гонять сколько угодно раз, и соседние блоки не страдают.
    """
    text = SCH.read_text()
    kept, dropped = [], 0
    for kind, chunk in top_items(text):
        mine = any(inside(chunk, f) for f in s.frames)
        if mine and kind == "symbol" and re.search(
                r'\(property "Reference" "(%s)"'
                % "|".join(re.escape(r) for r in s.protect), chunk):
            mine = False          # F133 стоит в зоне блока, но не его деталь
        if kind in ("symbol", "wire", "junction", "global_label",
                    "label", "no_connect", "bus") and mine:
            dropped += 1
            continue
        if kind == "text" and mine and "БЛОК" not in chunk \
                and not re.search(r'\(text "%d\.' % s.block, chunk):
            dropped += 1
            continue
        kept.append((kind, chunk))

    # секция lib_symbols: дополняем недостающими определениями
    libs = next(c for k, c in kept if k == "lib_symbols")
    add = "".join("\n" + "\n".join("\t" + ln if ln.strip() else ln
                                   for ln in symbol_def(lid).split("\n"))
                  for lid in s.used if f'(symbol "{lid}"' not in libs)
    new_libs = libs[:libs.rindex(")")].rstrip() + add + "\n\t)"

    parts = [new_libs if kind == "lib_symbols" else chunk
             for kind, chunk in kept
             if kind not in ("sheet_instances", "embedded_fonts")]

    out = ("(kicad_sch\n\t"
           + "\n\t".join(p.strip("\n") for p in parts)
           + "\n" + "".join(s.items)
           + '\t(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n\t\t)\n\t)\n'
           + "\t(embedded_fonts no)\n)\n")
    SCH.write_text(out)
    print(f"блок {s.block}: убрано старых элементов {dropped}, "
          f"вставлено {len(s.items)}, символов {len(s.used)}")
