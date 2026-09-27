#!/usr/bin/env python3
"""Закупочный перечень деталей — из схемы, одной командой.

Запуск:  python3 hw/console/tools/bom.py      -> hw/console/bom.csv

Зачем команда, а не таблица руками. Прежний `bom.csv` был выгрузкой старой
ревизии (флешка там стояла в корпусе 208 mil, которого на плате давно нет), и
отличить устаревший перечень от свежего по виду файла нельзя. Здесь перечень
каждый раз собирается из `console.kicad_sch`, а характеристики, которых в
схеме нет (напряжение и диэлектрик конденсаторов, допуск резисторов),
выводятся ПРАВИЛАМИ из цепей, на которых деталь стоит. Правила — в одном
месте, ниже; схема их не повторяет.

Покупка — Чип и Дип (ЛУТ, паяем сами). Колонка «Что искать» — описание для
поиска по характеристикам, а не артикул магазина: артикулов мы не сверяли.
"""
import csv
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCH = ROOT / "console.kicad_sch"
OUT = ROOT / "bom.csv"

# Рабочее напряжение цепи, В. Всё, чего нет в таблице и что не земля, считаем
# сигналом 3.3 В (банки ввода-вывода F133 и дисплей питаются от +3V3).
NET_V = {
    "VBUS": 5.0,
    "VSYS": 4.2, "/PWR_EN": 4.2, "EN_3V3": 4.2, "EN_1V8": 4.2,
    "Net-(U501-VDD)": 4.2,
    "+3V3": 3.3, "EN_0V9": 3.3, "VCC-TVOUT": 3.3,
    "+1V8": 1.8, "AVCC": 1.8, "LDOA-OUT": 1.8, "LDOB-OUT": 1.8,
    "+0V9": 0.9, "VRA1": 1.8, "VRA2": 1.8,
    # выходы баков до перемычек R16…R18
    "Net-(C4-Pad1)": 3.3, "Net-(C5-Pad1)": 1.8, "Net-(C10-Pad1)": 0.9,
}
# VBUS — не меньше 16 В: при втыкании кабеля выброс на индуктивности провода
# доходит почти до двойного напряжения, а TP4056 держит 8 В на VCC.
V_MIN = {"VBUS": 16}
GROUND = {"GND", "AGND"}

# Резисторы, у которых от допуска зависит результат, — 1 %; остальные 5 %.
# R1…R6 — делители обратной связи баков (при 5 % ядро 0.9 В уходит на ±5 %),
# R14/R15 — делитель замера банки, R11 — ток заряда TP4056, R802 — `DZQ`
# (240 Ом 1 %, 08-decoupling.md §3.4).
PRECISE = {"R1", "R2", "R3", "R4", "R5", "R6", "R11", "R14", "R15", "R802"}

# Детали, которые по номиналу не описать, — что именно искать.
PART = {
    "U1": "Allwinner F133-A, eLQFP-128 14×14 (есть на руках)",
    "U2": "SY8089AAAC, понижающий 2 А, SOT-23-5",
    "U5": "TP4056, зарядник Li-Ion, ESOP-8 с термопадом",
    "U401": "W25Q32JVSNIQ, SPI NOR 4 МБ, SOIC-8 150 mil (НЕ SSIQ 208 mil)",
    "U501": "PAM8301AAF, УНЧ класса D, SOT-23-6",
    "D301": "USBLC6-2SC6, защита USB, SOT-23-6",
    "Q601": "AO3400A, N-MOSFET, SOT-23",
    "L1": "Sunlord SWPA3015S2R2NT, 2.2 мкГн, Isat ≥ 1.6 А, 3×3×1.5",
    "Y1": "кварц 24 МГц SMD 3225, 4 вывода, CL 18 пФ, ±20 ppm",
    "Y2": "кварц 32.768 кГц SMD 3215, CL 12.5 пФ",
    "J1": "JST B2B-PH-SM4-TB, PH 2.0 мм, 2 контакта, вертикальный SMD "
          "(+ ответная PHR-2 с проводами на банку)",
    "J301": "USB-C 16P, HRO TYPE-C-31-M-12",
    "J501": "штыревой разъём 1×2, шаг 2.54, прямой (провода динамика)",
    "J601": "Hirose FH12-40S-0.5SH(55), FFC 40 контактов, шаг 0.5, нижний контакт",
    "J901": "штыревой разъём 1×4, шаг 2.54, прямой (UART)",
    "SW1": "движковый переключатель MSK-12C02, SMD",
    "SW101": "тактовая кнопка PTS645, SMD 6×6",
    "D1": "светодиод 0805 красный",
    "D2": "светодиод 0805 зелёный",
    "D901": "светодиод 0805 жёлтый",
}
NOT_BOUGHT = ("TP", "JP")      # контрольные точки и перемычки — это медь платы


def netlist():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "n.xml"
        subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format",
                        "kicadxml", "-o", str(out), str(SCH)],
                       check=True, capture_output=True)
        return ET.parse(out).getroot()


def cap_spec(value, nets):
    """Диэлектрик и напряжение конденсатора — по номиналу и цепям."""
    m = re.match(r"([\d.]+)\s*([pnu])", value)
    farad = float(m.group(1)) * {"p": 1e-12, "n": 1e-9, "u": 1e-6}[m.group(2)]
    if farad <= 1e-9:
        return "C0G (NP0), 50 В"
    live = [n for n in nets if n not in GROUND]
    v = max((NET_V.get(n, 3.3) for n in live), default=3.3)
    # Запас «вдвое» — не от пробоя, а против потери ёмкости под смещением у
    # керамики X5R/X7R: 22 мкФ 0805 на 6.3 В при 3.3 В теряет больше
    # половины, на 10 В — заметно меньше.
    floor = max((V_MIN.get(n, 0) for n in live), default=0)
    for rated in (6.3, 10, 16, 25, 50):
        if rated >= 2.0 * v and rated >= floor:
            break
    return f"X7R, {rated:g} В" if farad <= 1e-6 else f"X5R/X7R, {rated:g} В"


def main():
    root = netlist()
    pins = defaultdict(set)
    for net in root.iter("net"):
        for node in net.iter("node"):
            pins[node.get("ref")].add(net.get("name"))

    groups = defaultdict(list)
    for comp in root.iter("comp"):
        ref = comp.get("ref")
        if ref.startswith(NOT_BOUGHT):
            continue
        value = comp.findtext("value")
        fp = comp.findtext("footprint") or ""
        size = re.search(r"_(0805|0603|1206)_", fp)
        kind = re.match(r"[A-Z]+", ref).group(0)
        if kind == "C":
            spec = cap_spec(value, pins[ref])
            what = f"конденсатор керамический {value}Ф, {size.group(1) if size else fp}"
        elif kind == "R":
            if value in ("0", "0R"):
                spec = "перемычка 0 Ом, до 2 А"
            else:
                spec = "1 %" if ref in PRECISE else "5 %"
            what = f"резистор {value}Ом, {size.group(1) if size else fp}"
        else:
            spec = ""
            # одна строка на тип: SW101…SW110, L1…L3, U2…U4 описаны по первому
            base = {"SW1": "SW101", "U3": "U2", "U4": "U2", "L2": "L1",
                    "L3": "L1"}.get(ref, ref)
            base = "SW101" if ref.startswith("SW1") and len(ref) == 5 else base
            what = PART.get(base, f"{value} ({fp.split(':')[-1]})")
            if ref == "SW1":
                what = PART["SW1"]
        groups[(what, spec)].append(ref)

    def key(ref):
        m = re.match(r"([A-Z]+)(\d+)", ref)
        return m.group(1), int(m.group(2))

    rows = sorted(groups.items(), key=lambda it: key(sorted(it[1], key=key)[0]))
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Кол", "Что искать", "Характеристики", "Позиции"])
        for (what, spec), refs in rows:
            refs = sorted(refs, key=key)
            w.writerow([len(refs), what, spec, " ".join(refs)])
    total = sum(len(r) for r in groups.values())
    print(f"позиций: {len(groups)}, деталей: {total} -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
