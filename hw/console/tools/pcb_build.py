#!/usr/bin/env python3
"""Весь круг блочной сборки платы — одной командой.

Порядок тот же, что и в работе руками (10-mech.md §8.3.0), и он не
переставляется:

1. `pcb_park.py`    — всё, кроме привязанного к месту, уезжает за край;
1a. `pcb01_outline.py` — контур и крепёж, до расстановки;
2. `pcb04_fine.py`  — расстановка блок за блоком, кольцо развязки последним;
3. `pcb07_fanout.py`— лучи выхода из-под корпуса; ЗАОДНО снимает всю медь;
4. `pcb_hand.py`    — дорожки, положенные руками, по таблице полос;
5. `pcb10_route.py` — USB отдельным проходом (критичная пара, первой);
6. `pcb10_route.py` — всё остальное, кроме земли.

Почему круг собран в команду: каждый раз руками я прогонял его чуть иначе —
то забывал веер после сдвига детали, то гнал трассировщик по старому списку
цепей. Замер, который каждый делает руками, каждый делает по-своему.

Цены трассировщика для второго прохода выбраны замером (заклёпка дорогая,
изнанка как лицо): мерить надо заклёпками, потому что каждая — это пайка
руками с двух сторон, а сплошная изнанка у нас только ради удобства
травления.

    | заклёпка | изнанка | развёл | не смог | заклёпок |
    |---|---|---|---|---|
    | 12  | ×1.2 | 113 | 17 | 71 |
    | 100 | ×3.0 | 104 | 26 | 49 |
    | 100 | ×1.0 | 107 | 23 | 46 |  <- взято
    | 300 | ×1.0 | 108 | 22 | 49 |

Запуск:  python3 hw/console/tools/pcb_build.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

USB = ["USB0-DP", "USB0-DM", "Net-(D301-I{slash}O1-Pad1)",
       "Net-(D301-I{slash}O2-Pad3)", "Net-(J301-CC1)", "Net-(J301-CC2)"]
# Цена изнанки перемерена, когда изнанку стали считать и по длине, а не
# только по заклёпкам (она — сплошная земля, дорожка на ней режет плоскость):
#
#     | изнанка | под дорожками | сигнальных заклёпок |
#     |---|---|---|
#     | ×1.0 | 253 мм | 29 |
#     | ×1.5 | 206 мм | 30 |
#     | ×2.0 | 204 мм | 30 |  <- взято: −49 мм за одну заклёпку
#     | ×3.0 | 201 мм | 30 |
#
# Числа — после того, как сборка стала повторяемой (`pcb10_route.py`,
# сортировка прочитанного). Прежний замер (215/167/157 мм) делался, когда
# итог плыл от прогона к прогону, и сравнивал не цены, а удачу.
REST_ENV = {"PCB_VIA": os.environ.get("PCB_VIA", "100"),
            "PCB_BACK": os.environ.get("PCB_BACK", "2.0")}


def step(title, args, env=None, keep=("проложено связей", "лучей выведено",
                                        "courtyard", "положено своих",
                                        "сужено по зазору", "не разошлись",
                                        "мест, где не прошло", ") — ")):
    t0 = time.time()
    print(f"-- {title} ...", flush=True)
    full = {**os.environ, **(env or {})}
    r = subprocess.run([sys.executable, str(TOOLS / args[0])] + args[1:],
                       capture_output=True, text=True, env=full, cwd=ROOT)
    out = [l for l in (r.stdout + r.stderr).splitlines()
           if any(k in l for k in keep)]
    for l in out:
        print("   " + l.strip())
    if r.returncode:
        print(f"   ОШИБКА: шаг «{title}» кончился кодом {r.returncode}")
        print("   " + "\n   ".join((r.stderr or r.stdout).splitlines()[-8:]))
        sys.exit(r.returncode)
    print(f"   готово за {time.time() - t0:.0f} с", flush=True)


def remaining():
    """Цепи, у которых медь ещё не связана. Земля — не в счёт, её кладёт
    заливка."""
    sys.path.insert(0, str(TOOLS))
    import pcbnew
    import pcb_view
    b = pcbnew.LoadBoard(str(ROOT / "console.kicad_pcb"))
    return sorted({n for n, _, _ in pcb_view.air_lines(b, set()) if n != "GND"})


def main():
    t0 = time.time()
    step("увезти всё за край", ["pcb_park.py"])
    # Контур и крепёж — до расстановки: отверстия должны стоять раньше
    # обвязки, иначе она сядет туда, где потом встанет винт.
    step("контур и крепёж", ["pcb01_outline.py"], keep=("контур",))
    step("расставить блоки", ["pcb04_fine.py"],
         keep=("courtyard", "дросселями"))
    step("лучи из-под корпуса", ["pcb07_fanout.py"])
    step("ручные полосы", ["pcb_hand.py"])
    # Те же цены, что у остального: при цене изнанки по умолчанию (1.2) пара
    # USB ложится иначе, и +3V3 потом не проходит у розетки — замерено.
    step("USB", ["pcb10_route.py"] + USB, env=REST_ENV)
    rest = [n for n in remaining() if n not in USB]
    step(f"остальное: {len(rest)} цепей", ["pcb10_route.py"] + rest,
         env=REST_ENV)
    left = remaining()
    import pcbnew
    b = pcbnew.LoadBoard(str(ROOT / "console.kicad_pcb"))
    signal = sum(1 for t in b.GetTracks() if isinstance(t, pcbnew.PCB_VIA))
    step("тупиковые лучи", ["pcb_trim.py"], keep=("снято", "снимать руками"))
    step("земля: заливка и сшивка", ["pcb06_planes.py"],
         keep=("термопадом", "по полю", "по краю"))
    step("земля: добивка кусков", ["pcb09_gnd.py"],
         keep=("кусков заливки", "добито", "мм²", "горячих петель",
               "НЕ ПОСТАВЛЕНА"))
    b = pcbnew.LoadBoard(str(ROOT / "console.kicad_pcb"))
    gnd = sum(1 for t in b.GetTracks() if isinstance(t, pcbnew.PCB_VIA)
              and t.GetNetname() == "GND")
    # Изнанка у нас — сплошная земля; каждый миллиметр дорожки на ней режет
    # опорную плоскость (A64 PCB Layout Guide, EMC п. 1).
    back = sum(pcbnew.ToMM(t.GetLength()) for t in b.GetTracks()
               if not isinstance(t, pcbnew.PCB_VIA)
               and t.GetLayer() == pcbnew.B_Cu)
    print(f"изнанка под дорожками: {back:.0f} мм")
    print(f"ИТОГ за {time.time() - t0:.0f} с: заклёпок {signal + gnd} "
          f"(сигнал и питание {signal}, земля {gnd}), "
          f"цепей с разрывом {len(left)}"
          + (f" — {' '.join(left)}" if left else ""))
    drc()


def drc():
    """Итог DRC одной строкой: по видам нарушений и несоединённым.

    Считаем по JSON-отчёту, а не по тексту: текст меняется от версии KiCad.
    """
    import json
    import tempfile
    out = Path(tempfile.mkdtemp()) / "drc.json"
    subprocess.run(["kicad-cli", "pcb", "drc", "--severity-all",
                    "--schematic-parity", "--format", "json", "-o", str(out),
                    str(ROOT / "console.kicad_pcb")],
                   capture_output=True, text=True)
    d = json.loads(out.read_text())
    kinds = {}
    for v in d["violations"]:
        kinds[v["type"]] = kinds.get(v["type"], 0) + 1
    print(f"DRC: нарушений {len(d['violations'])}"
          + (f" ({', '.join(f'{k} {n}' for k, n in sorted(kinds.items()))})"
             if kinds else "")
          + f", несоединённых {len(d['unconnected_items'])}, "
          f"расхождений со схемой {len(d.get('schematic_parity', []))}")


if __name__ == "__main__":
    main()
