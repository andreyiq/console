#!/usr/bin/env python3
"""Добор несошедшихся связей: прогонять разводку поверх готовой, пока помогает.

Почему это отдельный шаг, а не настройка `pcb10_route.py`. Первый прогон идёт
по чистой плате: коробка поиска узкая (12 мм), и это правильно — обход в
тридцать миллиметров на двуслойной плате дороже непроложенной связи, а широкая
коробка на всех цепях только размазывает бюджет (замер — в `MARGIN`).

Но когда плата уже разведена, вопрос другой. Своя медь помечена `locked`, и
следующий прогон её не снимает: он видит готовые куски цепи и пробует сшить
только то, что не сошлось. Для этих остатков широкая коробка не стоит ничего —
проложенному она уже не повредит, а обход в двадцать пять миллиметров для
последней связи дешевле, чем её отсутствие.

Померено сплошным прогоном конвейера, коробка 250 клеток, бюджет 1500000:

| прогон | связей | не сошлось |
|---|---|---|
| первый, коробка 60 | 196 | 74 |
| добор 1 | 202 | 68 |
| добор 2 | 203 | 67 |
| добор 3 | 203 | 67 — стоп |

Останавливаемся, когда прогон перестал убавлять неудачи, — а не по числу
прогонов: где остановиться, знает замер, а не константа.

Цена честная и немалая: каждый добор идёт дольше первого прогона (широкая
коробка и щедрый бюджет), три добора это около получаса. Поэтому шаг и
отдельный: в обычной итерации его не гоняют, гоняют перед сборкой.

Число берём из `route.json`, который пишет сам `pcb10_route.py`. Разбирать его
текстовый отчёт нельзя: он меняется от каждого улучшения отчёта, и сторож,
читающий слова, ломается ровно на улучшении.

Запуск:  python3 hw/console/tools/pcb11_more.py
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATE = ROOT / "route.json"

MARGIN = int(os.environ.get("PCB_MORE_MARGIN", 250))
BUDGET = int(os.environ.get("PCB_MORE_BUDGET", 1500000))
LIMIT = int(os.environ.get("PCB_MORE_LIMIT", 6))     # потолок от зацикливания

# Предел по времени, и он нужен отдельно от предела по числу заходов. Один
# заход при широкой коробке идёт около девяти минут, шесть — почти час, и всё
# это время шаг не говорит ничего. Упёршись в срок, он скажет, что упёрся во
# ВРЕМЯ, а не в предмет: разница существенная, потому что лечится она разными
# вещами — время ручкой, предмет размещением.
DEADLINE = float(os.environ.get("PCB_MORE_DEADLINE", 1800))   # секунд


def run():
    env = dict(os.environ, PCB_MARGIN=str(MARGIN), PCB_BUDGET=str(BUDGET))
    r = subprocess.run([sys.executable, str(HERE / "pcb10_route.py")],
                       env=env, capture_output=True, text=True)
    if r.returncode != 0:
        tail = "\n".join(r.stderr.strip().split("\n")[-5:])
        raise SystemExit(f"pcb10_route.py упал (код {r.returncode}):\n{tail}")
    if not STATE.exists():
        raise SystemExit(f"нет {STATE} — старый pcb10_route.py, итог не пишет")
    return json.loads(STATE.read_text())


def main():
    if not STATE.exists():
        raise SystemExit("сначала нужен прогон pcb10_route.py: добор идёт "
                         "поверх готовой разводки, а не вместо неё")
    was = json.loads(STATE.read_text())
    print(f"до добора: связей {was['done']}, не сошлось {was['fail']}; "
          f"заходов не больше {LIMIT}, времени не больше {DEADLINE / 60:.0f} мин",
          flush=True)
    t0 = time.monotonic()
    n, why = 0, None
    while True:
        if n >= LIMIT:
            why = f"упёрлись в потолок {LIMIT} заходов"
            break
        left = DEADLINE - (time.monotonic() - t0)
        if left <= 0:
            why = f"упёрлись во ВРЕМЯ ({DEADLINE / 60:.0f} мин), а не в предмет"
            break
        print(f"  заход {n + 1} пошёл, в запасе {left / 60:.0f} мин…", flush=True)
        now = run()
        n += 1
        gain = was["fail"] - now["fail"]
        print(f"  добор {n}: связей {now['done']}, не сошлось {now['fail']}"
              f" ({'убавилось на ' + str(gain) if gain > 0 else 'без изменений'})"
              f", прошло {(time.monotonic() - t0) / 60:.0f} мин", flush=True)
        if gain <= 0:
            why = "перестало убавлять"
            was = now
            break
        was = now
    print(f"добор кончен: заходов {n}, не сошлось {was['fail']} — {why}")


if __name__ == "__main__":
    main()
