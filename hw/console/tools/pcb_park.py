#!/usr/bin/env python3
"""Убрать с платы всё, что не привязано к месту жёстко, — за её пределы.

Зачем. Разводить всю плату разом и смотреть на одно число «не разведено N»
оказалось плохим способом: по такому числу не видно, ЧТО именно плохо. Целый
день ушёл на настройки трассировщика, пока заказчик не открыл плату и за
десять секунд не увидел, что обвязка флешки лежит ровным рядом в двух
сантиметрах от неё.

Поэтому порядок другой, человеческий: на плате остаётся только то, чьё место
задано снаружи, остальное уезжает за край и не мешает. Дальше блок за блоком —
поставили, развели, ПОСМОТРЕЛИ, поправили, и только потом следующий.

Что считается привязанным жёстко:

* **кнопки** — их место задаёт рука, а не разводка;
* **разъём шлейфа** — он стоит на оси хвоста панели (06-display.md §7.2);
* **движок питания** — его рычаг торчит из торца;
* **сам F133** — его ставим осознанно, он опорная точка для всего остального.

**Крепёж тоже уезжает**, и это не забывчивость: отверстия кладёт
`pcb01_outline.py`, он идемпотентный, и вернуть их — значит просто прогнать
его. Заодно появляется свобода поставить их иначе, если окажется, что они
мешают разводке. ВЕРНУТЬ ОБЯЗАТЕЛЬНО: без крепежа плату не к чему привинтить.

Всё прочее уезжает на «стоянку» правее платы. Там оно видно в KiCad, не
потеряно и в любой момент возвращается своим скриптом расстановки.

Запуск:  python3 hw/console/tools/pcb_park.py          — увезти
         python3 hw/console/tools/pcb_park.py --list   — только показать
"""
import sys
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"

OX, OY = 50.0, 40.0
BOARD_W = 156.0

# Стоянка: правее платы, рядами. Плата кончается на OX + BOARD_W = 206.
PARK_X = OX + BOARD_W + 15.0
PARK_STEP = 6.0
PARK_COLS = 14

# Кто остаётся. Кнопки — по префиксу, остальное поимённо.
KEEP_PREFIX = ("SW1",)
KEEP = {"J601", "U1"}


def keep(ref):
    return ref in KEEP or ref.startswith(KEEP_PREFIX)


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    stay, move = [], []
    for f in board.GetFootprints():
        (stay if keep(f.GetReference()) else move).append(f)
    move.sort(key=lambda f: f.GetReference())

    if "--list" in sys.argv:
        print(f"останется на плате: {len(stay)} — "
              f"{' '.join(sorted(f.GetReference() for f in stay))[:200]}")
        print(f"уедет на стоянку: {len(move)}")
        return

    for i, f in enumerate(move):
        x = PARK_X + (i % PARK_COLS) * PARK_STEP
        y = OY + (i // PARK_COLS) * PARK_STEP
        f.SetLocked(False)
        f.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))

    # Медь снимаем всю: она вела к деталям, которых на плате больше нет.
    gone = 0
    for t in list(board.GetTracks()):
        board.RemoveNative(t)
        gone += 1
    for z in board.Zones():
        z.UnFill()

    board.Save(str(BOARD))
    print(f"на плате осталось {len(stay)} корпусов: "
          f"{' '.join(sorted(f.GetReference() for f in stay))}")
    print(f"увезено на стоянку: {len(move)}; снято меди: {gone}")
    print("заливки сняты — вернёт pcb06_planes.py")
    print("НЕ ЗАБЫТЬ В КОНЦЕ: крепёжные отверстия вернёт pcb01_outline.py")


if __name__ == "__main__":
    main()
