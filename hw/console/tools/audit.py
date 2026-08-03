#!/usr/bin/env python3
"""Проверка схемы целиком. Всё, чего не видит ERC.

    python3 hw/console/tools/audit.py

Печатает по строке на проверку, и в каждой — **сколько объектов она
посмотрела**. Проверка, которая не печатает число, ничего не доказывает.

Зачем отдельно от ERC. ERC ловит неподключённый вывод и конфликт типов, но
молчит про то, чем схема ломается на самом деле:

- **Т-образное соединение без точки.** Провод, к которому подошёл конец
  другого провода, соединяется только если там стоит junction. Без точки цепь
  тихо распадается, и на печати это неотличимо от исправной схемы. Так в
  блоке 2 однажды развалилась обратная связь канала 0.9 В.
- **Метка не на проводе.** Глобальная метка в пустоте не даёт ошибки, просто
  цепь остаётся разорванной. Так вывод 101 `GPADC0` три блока подряд считался
  подключённым.
- **Корпус, у которого нет площадки под вывод символа.** Всплывает уже при
  разводке, когда менять поздно.
- **Деталь без поля `Источник`** — нарушение правила проекта «ни одного
  номинала без ссылки на источник» (README).

Проверки 1…10 — геометрия и поля, читаются прямо из `.kicad_sch`.
Проверки 11…14 — netlist и ERC, для них вызывается `kicad-cli`.
"""

import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from kicadsch import ROOT, SCH, lib_pins, rotate, top_items

FPLIB = Path("/usr/share/kicad/footprints")
LOCAL = ROOT / "lib"


def R(v):
    return round(float(v), 3)


def prop(chunk, name):
    m = re.search(r'\(property "%s" "([^"]*)"' % name, chunk)
    return m.group(1) if m else None


def parse(text):
    """Разбор листа на символы, провода, точки, крестики и метки."""
    syms, wires, juncts, ncs, labels = [], [], [], [], []
    for kind, chunk in top_items(text):
        if kind == "symbol" and "(lib_id" in chunk:
            m = re.search(r"\(at ([-\d.]+) ([-\d.]+) (\d+)\)", chunk)
            syms.append(dict(
                lib=re.search(r'\(lib_id "([^"]+)"\)', chunk).group(1),
                x=R(m.group(1)), y=R(m.group(2)), rot=int(m.group(3)),
                mirror=("y" if "(mirror y)" in chunk else
                        "x" if "(mirror x)" in chunk else ""),
                ref=prop(chunk, "Reference"), val=prop(chunk, "Value"),
                fp=prop(chunk, "Footprint"), src=prop(chunk, "Источник")))
        elif kind == "wire":
            a, b = re.findall(r"\(xy ([-\d.]+) ([-\d.]+)\)", chunk)
            wires.append(((R(a[0]), R(a[1])), (R(b[0]), R(b[1]))))
        elif kind in ("junction", "no_connect"):
            m = re.search(r"\(at ([-\d.]+) ([-\d.]+)\)", chunk)
            (juncts if kind == "junction" else ncs).append(
                (R(m.group(1)), R(m.group(2))))
        elif kind in ("global_label", "label"):
            m = re.search(r"\(at ([-\d.]+) ([-\d.]+) (\d+)\)", chunk)
            name = re.match(r'\((?:global_)?label "([^"]+)"', chunk).group(1)
            labels.append((name, (R(m.group(1)), R(m.group(2)))))
    return syms, wires, juncts, ncs, labels


def pin_map(syms):
    """{точка: [(ссылка, номер вывода)]} для всех деталей листа."""
    out = defaultdict(list)
    for s in syms:
        for num, (px, py) in lib_pins(s["lib"]).items():
            dx, dy = rotate(px, py, s["rot"])
            if s["mirror"] == "y":
                dx = -dx
            elif s["mirror"] == "x":
                dy = -dy
            out[(R(s["x"] + dx), R(s["y"] + dy))].append((s["ref"], num))
    return out


def on_wire(pt, w):
    """'end' — точка совпала с концом провода, 'mid' — легла на середину."""
    (x1, y1), (x2, y2) = w
    if pt == (x1, y1) or pt == (x2, y2):
        return "end"
    if x1 == x2 == pt[0] and min(y1, y2) < pt[1] < max(y1, y2):
        return "mid"
    if y1 == y2 == pt[1] and min(x1, x2) < pt[0] < max(x1, x2):
        return "mid"
    return None


def span(w):
    """(вертикальный?, координата оси, [от, до]) — для поиска наложений."""
    (x1, y1), (x2, y2) = w
    vert = x1 == x2
    return (vert, x1 if vert else y1,
            sorted([y1, y2] if vert else [x1, x2]))


def say(n, total, what, bad):
    print(f"{n:>2}. {total}: {what} — {len(bad)}", bad if bad else "")
    return len(bad)


def main():
    text = SCH.read_text()
    syms, wires, juncts, ncs, labels = parse(text)
    pins = pin_map(syms)
    real = [s for s in syms if not s["ref"].startswith("#")]
    ends = Counter()
    for a, b in wires:
        ends[a] += 1
        ends[b] += 1
    label_at = defaultdict(list)
    for name, pt in labels:
        label_at[pt].append(name)

    print(f"лист: символов {len(syms)} (деталей {len(real)}), "
          f"проводов {len(wires)}, точек {len(juncts)}, крестиков {len(ncs)}, "
          f"меток {len(labels)}")
    bad = 0

    # --- геометрия цепей
    bad += say(1, f"концов проводов {len(ends)}", "висит в пустоте",
               sorted(pt for pt, n in ends.items()
                      if n == 1 and pt not in pins and pt not in label_at
                      and pt not in ncs
                      and not any(on_wire(pt, w) == "mid" for w in wires)))

    tee = sorted({pt for pt in list(ends) + list(pins)
                  if pt not in juncts
                  and any(on_wire(pt, w) == "mid" for w in wires)})
    bad += say(2, f"касаний проверено {len(ends) + len(pins)}",
               "Т-соединений без точки", tee)

    bad += say(3, f"точек соединения {len(juncts)}",
               "стоят там, где сходится меньше трёх концов",
               [pt for pt in juncts
                if ends[pt] + len(pins.get(pt, []))
                + 2 * sum(1 for w in wires if on_wire(pt, w) == "mid") < 3])

    bad += say(4, f"меток {len(labels)}", "не лежат на цепи",
               [(n, pt) for n, pt in labels
                if pt not in pins and not ends[pt]
                and not any(on_wire(pt, w) for w in wires)])

    bad += say(5, f"выводов на листе {sum(len(v) for v in pins.values())}",
               "ни к чему не подключены (кроме no_connect в самом символе)",
               [(sorted(o), pt) for pt, o in pins.items()
                if not ends[pt] and pt not in ncs and pt not in label_at
                and len(o) == 1
                and not any(on_wire(pt, w) for w in wires)
                and not (o[0][0] == "U1" and o[0][1] in
                         ("106", "107", "108", "109", "110", "111"))])

    bad += say(6, f"крестиков {len(ncs)}",
               "стоят на подключённом выводе или не на выводе",
               [pt for pt in ncs
                if pt not in pins or ends[pt]
                or any(on_wire(pt, w) for w in wires)])

    over, cross = [], []
    for i, wi in enumerate(wires):
        vi, ci, (a1, a2) = span(wi)
        for wj in wires[i + 1:]:
            vj, cj, (b1, b2) = span(wj)
            if vi == vj and ci == cj and min(a2, b2) - max(a1, b1) > 1e-6:
                over.append((wi, wj))
            elif vi != vj:
                (xv, (y1, y2)), (yh, (x1, x2)) = ((ci, (a1, a2)), (cj, (b1, b2))) \
                    if vi else ((cj, (b1, b2)), (ci, (a1, a2)))
                if x1 < xv < x2 and y1 < yh < y2:
                    cross.append((wi, wj))
    bad += say(7, f"пар проводов {len(wires) * (len(wires) - 1) // 2}",
               "накладываются друг на друга", over)
    bad += say(8, f"пар проводов {len(wires) * (len(wires) - 1) // 2}",
               "пересекаются крест-накрест", cross)

    # --- поля деталей и корпуса
    bad += say(9, f"символов {len(syms)}", "в совпадающих координатах",
               [k for k, v in Counter((s["x"], s["y"]) for s in syms).items()
                if v > 1])

    nofp = sorted(s["ref"] for s in real if not s["fp"])
    print(f"10. деталей {len(real)}: без корпуса — {len(nofp)} {nofp}")
    bad += say(11, f"деталей {len(real)}", "без поля Источник",
               sorted(s["ref"] for s in real if not s["src"]))

    pads_bad = []
    for s in real:
        if not s["fp"]:
            continue
        lib, name = s["fp"].split(":", 1)
        for p in (FPLIB / f"{lib}.pretty" / f"{name}.kicad_mod",
                  LOCAL / f"{lib.split('/')[-1]}.pretty" / f"{name}.kicad_mod",
                  LOCAL / "console.pretty" / f"{name}.kicad_mod"):
            if p.exists():
                pads = set(re.findall(r'\(pad "([^"]*)"', p.read_text()))
                need = set(lib_pins(s["lib"]))
                if not need <= pads:
                    pads_bad.append((s["ref"], sorted(need - pads)))
                break
        else:
            pads_bad.append((s["ref"], "корпус не найден"))
    bad += say(12, f"корпусов проверено {len([s for s in real if s['fp']])}",
               "без площадки под вывод символа", pads_bad)

    norm = defaultdict(list)
    for n in {n for n, _ in labels}:
        norm[n.upper().replace("_", "-").replace(" ", "")].append(n)
    bad += say(13, f"имён цепей {len(set(n for n, _ in labels))}",
               "различаются только регистром или разделителем",
               {k: v for k, v in norm.items() if len(v) > 1})

    # --- netlist и ERC
    with tempfile.TemporaryDirectory() as tmp:
        net, rpt = Path(tmp) / "n.net", Path(tmp) / "e.rpt"
        subprocess.run(["kicad-cli", "sch", "export", "netlist",
                        "-o", str(net), str(SCH)], check=True,
                       capture_output=True)
        subprocess.run(["kicad-cli", "sch", "erc", "-o", str(rpt), str(SCH)],
                       capture_output=True)
        nt = net.read_text()
        nets = {}
        for b in re.split(r"\n    \(net \(code ", nt)[1:]:
            nm = re.search(r'\(name "([^"]*)"\)', b).group(1)
            nets[nm] = sorted(set(f"{a}.{c}" for a, c in re.findall(
                r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)', b)))
        refs = re.findall(r'\n    \(comp \(ref "([^"]+)"\)', nt)
        bad += say(14, f"компонентов в netlist {len(refs)}",
                   "с одинаковой ссылкой",
                   [r for r, n in Counter(refs).items() if n > 1])
        bad += say(15, f"цепей {len(nets)}", "с одним узлом",
                   [n for n, v in nets.items()
                    if len(v) < 2 and not n.startswith("unconnected-")])
        kinds = Counter(re.findall(r"\[(\w+)\]", rpt.read_text()))
        print(f"16. ERC: нарушений — {sum(kinds.values())} {dict(kinds)}")
        bad += sum(kinds.values())

    print("\nИТОГО нарушений:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
