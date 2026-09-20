#!/usr/bin/env python3
"""Полный круг разводки одной командой, с одной сводкой в конце.

Зачем это в репозитории, а не в одноразовом скрипте. Замер, который каждый
делает руками, каждый делает по-своему и ошибается по-своему: то забудет
`pcb07_fanout.py` перед прогоном (тогда прежняя медь остаётся препятствием и
числа получаются втрое хуже — я на этом попался), то сравнит один проход с
тремя, то возьмёт число из середины вывода. Плюс сам файл со скриптом дважды
терялся вместе с временным каталогом.

Считает и печатает:
  * связи и неудачи — из `route.json`, который пишет сам роутер;
  * сшивку и добивку земли — из их отчётов;
  * DRC — по своему прогону `kicad-cli`, отдельно нарушения и отдельно
    разрывы, потому что это разные вещи и лечатся по-разному.

Запуск:  python3 hw/console/tools/pcb_run.py
         python3 hw/console/tools/pcb_run.py --no-more   # без добора, быстро

Добор (`pcb11_more.py`) занимает больше времени, чем весь остальной круг, — в
обычной итерации его пропускают, перед сборкой гоняют.
"""
import argparse
import collections
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BOARD = ROOT / "console.kicad_pcb"
STATE = ROOT / "route.json"


def step(name, args, keep=()):
    """Прогнать шаг, показывая нужные строки ПО ХОДУ, и вернуть их все.

    Вывод читается построчно, а не забирается в конце. Разница не
    косметическая: добор идёт двадцать минут, и пока его вывод копился до
    конца шага, «идёт» и «зависло» были неотличимы ровно там, где ждать дольше
    всего.
    """
    t0 = time.monotonic()
    p = subprocess.Popen([sys.executable, "-u", str(HERE / name)] + list(args),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, bufsize=1)
    out = []
    for ln in p.stdout:
        ln = ln.rstrip()
        if not ln.strip():
            continue
        out.append(ln)
        if not keep or any(k in ln for k in keep):
            print(f"  {ln.strip()}")
    err = p.stderr.read()
    if p.wait() != 0:
        tail = "\n".join(err.strip().split("\n")[-6:])
        raise SystemExit(f"{name} упал (код {p.returncode}):\n{tail}")
    print(f"  ({name}: {time.monotonic() - t0:.0f} c)")
    return out


def picture():
    """Картинка меди обеих сторон — чтобы на плату СМОТРЕЛИ, а не только мерили.

    Это не украшение отчёта. Три круга проверок давали ноль, и каждый раз
    осмотр глазами находил то, чего ни одна проверка не видела: резисторы под
    банкой, защиту USB в стороне от розетки, зарядник в противоположном углу
    (`10-mech.md §9.1`). Цифры проверяют только то, о чём догадался спросить.

    Поэтому картинка делается КАЖДЫЙ круг и её путь печатается в сводке: цена
    осмотра должна быть один клик, иначе о нём забывают. Забыли уже — целую
    сессию мерили числа и ни разу не взглянули.
    """
    made = []
    for name, layers in (("медь-лицо.png", "F.Cu,Edge.Cuts"),
                         ("медь-изнанка.png", "B.Cu,Edge.Cuts")):
        path = ROOT / "view" / name
        path.parent.mkdir(exist_ok=True)
        r = subprocess.run(["kicad-cli", "pcb", "render", "-o", str(path),
                            "--side", "top", "-w", "1800", "-h", "900",
                            str(BOARD)], capture_output=True, text=True)
        if not path.exists():
            # Рендер трёхмерный и требует моделей; если его нет — плоский SVG.
            path = path.with_suffix(".svg")
            r = subprocess.run(["kicad-cli", "pcb", "export", "svg",
                                "-o", str(path), "--layers", layers,
                                "--black-and-white", "--mode-single",
                                "--exclude-drawing-sheet", str(BOARD)],
                               capture_output=True, text=True)
        if path.exists():
            made.append(path)
    return made


def drc():
    """Нарушения и разрывы по отчёту DRC — двумя числами, не одним.

    Отчёт пишем во временный каталог, а не рядом с платой: файл нужен на одну
    секунду, а в репозитории он остаётся навсегда и потом попадает в коммит
    вместе с полезным.
    """
    with tempfile.TemporaryDirectory() as tmp:
        rpt = Path(tmp) / "drc.json"
        r = subprocess.run(["kicad-cli", "pcb", "drc", "--severity-error",
                            "--severity-warning", "--format", "json",
                            "-o", str(rpt), str(BOARD)],
                           capture_output=True, text=True)
        if not rpt.exists():
            raise SystemExit(f"kicad-cli drc не дал отчёта:\n{r.stderr[-400:]}")
        d = json.loads(rpt.read_text())
    bad = collections.Counter(v["type"] for v in d.get("violations", []))
    gap = collections.Counter(
        v["items"][0]["description"].split("[")[1].split("]")[0]
        for v in d.get("unconnected_items", []))
    return bad, gap


def main():
    # Построчно, а не блоками. Питон при выводе в файл копит вывод по 8 КБ, и
    # круг на полчаса не печатает ни строки до самого конца — «идёт» и
    # «зависло» становятся неотличимы ровно там, где это дороже всего.
    sys.stdout.reconfigure(line_buffering=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("--no-more", action="store_true",
                    help="без добора: быстро, но связей меньше")
    a = ap.parse_args()

    t0 = time.monotonic()
    print("веер из-под F133:")
    step("pcb07_fanout.py", [], keep=("лучей выведено",))
    print("разводка:")
    step("pcb10_route.py", [], keep=("проложено связей",))
    if not a.no_more:
        print("добор:")
        step("pcb11_more.py", [], keep=("добор",))
    print("заливки и сшивка:")
    step("pcb06_planes.py", [], keep=("сшивки", "полигон", "островков"))
    print("добивка земли:")
    step("pcb09_gnd.py", [], keep=("добито", "кусок"))

    bad, gap = drc()
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    print("\nСВОДКА")
    # Медь считаем ПО ПЛАТЕ, а не по последнему прогону роутера. Последний
    # заход добора кладёт ноль отрезков — тем он и кончается, — и «переходных
    # 0, отрезков 0» на полностью разведённой плате читается как «ничего не
    # вышло». Ноль как отсутствие предмета и ноль как результат — разные вещи.
    board = pcbnew.LoadBoard(str(BOARD))
    vias = sum(1 for t in board.GetTracks() if isinstance(t, pcbnew.PCB_VIA))
    segs = sum(1 for t in board.GetTracks() if not isinstance(t, pcbnew.PCB_VIA))
    print(f"  связей проложено: {st.get('done', '—')}, "
          f"не сошлось: {st.get('fail', '—')}")
    print(f"  на плате: отрезков {segs}, заклёпок {vias} "
          f"(их паять руками с двух сторон)")
    print(f"  нарушений DRC: {sum(bad.values())}"
          + (f" — {', '.join(f'{k} {v}' for k, v in sorted(bad.items()))}"
             if bad else " (чисто)"))
    print(f"  разрывов по DRC: {sum(gap.values())} по {len(gap)} цепям"
          + (f" — {', '.join(f'{k}x{v}' for k, v in gap.most_common(6))}"
             if gap else ""))
    pics = picture()
    if pics:
        print("  ПОСМОТРЕТЬ ГЛАЗАМИ: " + ", ".join(str(p) for p in pics))
    else:
        print("  картинку сделать не удалось — осмотр глазами придётся "
              "открывать вручную в KiCad")
    print(f"  весь круг: {time.monotonic() - t0:.0f} c")


if __name__ == "__main__":
    main()
