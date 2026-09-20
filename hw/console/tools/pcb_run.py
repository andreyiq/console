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

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BOARD = ROOT / "console.kicad_pcb"
STATE = ROOT / "route.json"


def step(name, args, keep=()):
    """Прогнать шаг, показать только те строки, что просили, вернуть их все."""
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, str(HERE / name)] + list(args),
                       capture_output=True, text=True)
    if r.returncode != 0:
        tail = "\n".join(r.stderr.strip().split("\n")[-6:])
        raise SystemExit(f"{name} упал (код {r.returncode}):\n{tail}")
    out = [ln for ln in r.stdout.split("\n") if ln.strip()]
    shown = [ln for ln in out if any(k in ln for k in keep)] if keep else out
    for ln in shown:
        print(f"  {ln.strip()}")
    print(f"  ({name}: {time.monotonic() - t0:.0f} c)")
    return out


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
    print(f"  связей проложено: {st.get('done', '—')}, "
          f"не сошлось: {st.get('fail', '—')}, "
          f"переходных {st.get('vias', '—')}")
    print(f"  нарушений DRC: {sum(bad.values())}"
          + (f" — {', '.join(f'{k} {v}' for k, v in sorted(bad.items()))}"
             if bad else " (чисто)"))
    print(f"  разрывов по DRC: {sum(gap.values())} по {len(gap)} цепям"
          + (f" — {', '.join(f'{k}x{v}' for k, v in gap.most_common(6))}"
             if gap else ""))
    print(f"  весь круг: {time.monotonic() - t0:.0f} c")


if __name__ == "__main__":
    main()
