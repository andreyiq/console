#!/usr/bin/env python3
"""Фотошаблоны под ЛУТ: два PDF в масштабе 1:1 и карта сверловки.

Что такое ЛУТ: рисунок печатается лазерным принтером на плёнке или глянцевой
бумаге, кладётся тонером к меди и приглаживается утюгом. Отсюда два требования,
и оба легко нарушить молча.

**Зеркало — только на лицевой стороне.** Тонер ложится на медь той стороной,
которой напечатан, то есть рисунок при переносе переворачивается. Лицо (`F.Cu`)
печатаем зеркально, изнанку (`B.Cu`) — как есть: её мы кладём на плату снизу, и
она переворачивается второй раз. Перепутать легко, а увидеть — только после
травления, по тому, что корпуса не садятся.

**Масштаб ровно 1:1.** `kicad-cli` печатает в натуральную величину, если не
включать рамку с основной надписью: она добавляет поля и лист начинает
масштабироваться под формат. Поэтому `--include-border-title` не ставим.
Проверять масштаб надо линейкой по контуру платы, а не на глаз: 156 мм.

**`--negative` включаем, и это не опечатка.** Рассуждение подсказывает
обратное («негатив — для фоторезиста, у ЛУТ тонер защищает медь, значит
позитив»), и я так и написал здесь сначала. Рассуждение неверно: без флага
`kicad-cli` рисует плату так же, как показывает редактор, — ЧЁРНАЯ подложка,
БЕЛАЯ медь. На бумаге это залитый тонером лист и белые дорожки, то есть
вытравится всё, кроме фона. С флагом получается то, что нужно: белая плата,
чёрная медь.

Поймано глазами. Числа при этом говорили «324 КБ, 1 с, успех» — полярность
шаблона ни одна проверка не видит, а увидеть её иначе можно только после
травления, по испорченной заготовке.

Сверловка выводится отдельным файлом: заклёпки у нас ставятся вручную, и
сверлить по бумажной карте удобнее, чем по экрану.

Запуск:  python3 hw/console/tools/pcb12_lut.py [каталог]
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "fab"

# Слои каждого шаблона и нужно ли зеркалить. `Edge.Cuts` кладём на оба: по
# контуру совмещают две стороны и режут заготовку.
SHEETS = [
    ("lut-face.pdf", "F.Cu,Edge.Cuts", True, "лицо, зеркально"),
    ("lut-back.pdf", "B.Cu,Edge.Cuts", False, "изнанка, как есть"),
]


def run(args, what):
    t0 = time.monotonic()
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"{what}: kicad-cli вернул {r.returncode}\n"
                         f"{r.stderr.strip()[-500:]}")
    return time.monotonic() - t0


def main():
    if not BOARD.exists():
        raise SystemExit(f"нет платы: {BOARD}")
    OUT.mkdir(parents=True, exist_ok=True)

    for name, layers, mirror, what in SHEETS:
        path = OUT / name
        args = ["kicad-cli", "pcb", "export", "pdf", "-o", str(path),
                "--layers", layers, "--black-and-white", "--negative",
                "--mode-single", "--exclude-refdes", "--exclude-value",
                "--drill-shape-opt", "0"]
        if mirror:
            args.append("--mirror")
        dt = run(args + [str(BOARD)], name)
        kb = path.stat().st_size / 1024
        print(f"  {name:14} {what:20} {kb:7.0f} КБ  ({dt:.0f} c)")

    # Сверловка: карта для глаз и файл Excellon для станка, если дойдёт до него.
    dt = run(["kicad-cli", "pcb", "export", "drill", "-o", str(OUT) + "/",
              "--format", "excellon", "--drill-origin", "plot",
              "--excellon-units", "mm", "--generate-map", "--map-format",
              "pdf", str(BOARD)], "сверловка")
    made = sorted(p.name for p in OUT.iterdir()
                  if p.suffix.lower() in (".drl", ".pdf"))
    print(f"  сверловка: {dt:.0f} c")
    print(f"\nв {OUT}: {', '.join(made)}")
    print("Проверить перед печатью: контур платы линейкой — 156 мм, "
          "и что лицо зеркально, а изнанка нет.")


if __name__ == "__main__":
    main()
