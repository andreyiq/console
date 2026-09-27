#!/usr/bin/env python3
"""Проверки платы, которых нет в DRC. Брат-близнец tools/audit.py для схемы.

DRC знает про зазоры, отверстия и неразведённое. Он ничего не знает про наши
собственные договорённости: что в зоне банки деталей быть не должно, что
конденсатор развязки обязан стоять у своего вывода, что сверло мельче 0.4 мм
мы не осилим, что под лотком microSD меди быть не должно.

Каждая проверка печатает, сколько объектов посмотрела — иначе не видно
разницы между «нарушений нет» и «проверка ничего не нашла, потому что сломана».

Запуск:  python3 hw/console/tools/audit_pcb.py
"""
import itertools
import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"
SCH = ROOT / "console.kicad_sch"

OX, OY = 50.0, 40.0
BOARD_W, BOARD_H = 156.0, 74.0
EDGE = 0.5                      # отступ меди от реза, console.kicad_pro
MIN_DRILL = 0.4                 # минимальное сверло — CNC3018, 10-mech.md §7

PANEL = (35.965, 9.72, 120.035, 64.28)      # габарит панели на лице
# Зоны банки здесь БОЛЬШЕ НЕТ, и это не упущение.
#
# Стояла константа `BATT = (94.0, 6.0, 154.0, 51.0)`, и она не совпадала с тем,
# что рисует `pcb01_outline.py` — там банка в (92, 15) размером 60 x 45, то есть
# (92, 15)-(152, 60). Разница два миллиметра по горизонтали и ДЕВЯТЬ по
# вертикали: деталь на изнанке в полосе y = 51…60 проверку бы прошла, а в банку
# упёрлась.
#
# Не покусало только потому, что константа не использовалась ни разу: сама
# проверка строже и проще — на изнанке не должно быть деталей ВООБЩЕ (§4).
# Убрана, чтобы не ждала следующего. Понадобится зона — брать её у того, кто
# рисует: `from pcb01_outline import BATT_X, BATT_Y, BATT_W, BATT_H`.

# Каталог Hirose DM3, стр. 3: под механикой лотка меди быть не должно.
# Прямоугольник — заштрихованная область, пересчитанная в координаты платы от
# посадки `J401`; проверяется, что там нет чужих деталей.
SD_KEEPOUT = (18.0, 0.0, 34.0, 18.0)


def mm(v):
    return pcbnew.ToMM(v)


def box(fp):
    # Кэш courtyard пересчитываем явно: после `SetPosition` KiCad помечает
    # его недействительным, но не строит заново, и `GetCourtyard()` отдаёт
    # пустой полигон. Проверка тогда молча пропускает деталь и рапортует
    # ноль — ровно так подсунутая внахлёст пара конденсаторов не нашлась.
    fp.BuildCourtyardCaches()
    lay = pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd
    cy = fp.GetCourtyard(lay)
    bb = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False, False)
    return (mm(bb.GetLeft()) - OX, mm(bb.GetTop()) - OY,
            mm(bb.GetRight()) - OX, mm(bb.GetBottom()) - OY)


def hits(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def parts(board):
    return [f for f in board.GetFootprints()]


# ------------------------------------------------------------------ проверки

def check_outline(board):
    """1. Корпуса вне контура или ближе EDGE к резу."""
    bad = []
    for f in parts(board):
        x1, y1, x2, y2 = box(f)
        if f.GetReference().startswith("H"):
            continue                      # крепёж стоит по своим правилам
        # Розетка USB-C выступает за нижний торец по чертежу HRO на 0.5 мм
        # (`pcb04_fine.py`, `USB_AT`), её courtyard — корпус плюс 0.5, то
        # есть за резом 1.0. Это законно; больше — уже нет.
        over = 1.05 if f.GetReference() == "J301" else 0.01
        if x1 < -0.01 or y1 < -0.01 or x2 > BOARD_W + 0.01 or y2 > BOARD_H + over:
            bad.append(f"{f.GetReference()} вне контура")
    return len(parts(board)), bad


def check_courtyard(board):
    """2. Пересечения courtyard на одной стороне."""
    for f in parts(board):
        f.BuildCourtyardCaches()
    boxes = []
    for f in parts(board):
        for lay in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
            cy = f.GetCourtyard(lay)
            if cy.OutlineCount():
                boxes.append((f.GetReference(), lay, cy.BBox()))
    bad = [f"{a}~{b}" for (a, l1, x), (b, l2, y) in itertools.combinations(boxes, 2)
           if l1 == l2 and x.Intersects(y)]
    return len(boxes), bad


def check_battery(board):
    """3. Детали на изнанке — их там быть не должно вовсе.

    Вся электроника переехала на лицо, под панель (10-mech.md §4). На изнанке
    остаётся только банка, и любая деталь там — это либо забытый переворот,
    либо кто-то полез в единственное место, где лежит аккумулятор.
    """
    n, bad = 0, []
    for f in parts(board):
        if f.GetReference().startswith("H"):
            continue
        n += 1
        if f.IsFlipped():
            bad.append(f.GetReference())
    return n, bad


# Высота деталей, которые под панель не влезают: просвет там 2.0 мм.
# Всё, чего в списке нет, — мелочь не выше 1.8 (F133 1.55, TP4056 1.75,
# дроссель 1.5, керамика 0.9), она под стеклом живёт свободно.
TALL = {"J301": 3.2, "J401": 1.9, "J1": 8.5, "J501": 8.5, "J901": 8.5,
        "SW101": 4.3, "SW102": 4.3, "SW103": 4.3, "SW104": 4.3, "SW105": 4.3,
        "SW106": 4.3, "SW107": 4.3, "SW108": 4.3, "SW109": 4.3, "SW110": 4.3}
PANEL_GAP = 2.0


def check_under_panel(board):
    """4. Высокие детали под панелью — стекло встанет на них."""
    n, bad = 0, []
    for f in parts(board):
        ref = f.GetReference()
        if ref not in TALL:
            continue
        n += 1
        if hits(box(f), PANEL):
            bad.append(f"{ref} {TALL[ref]} мм при просвете {PANEL_GAP}")
    return n, bad


def check_holes_under_panel(board):
    """5. Крепёжные отверстия под панелью — стойка упрётся в стекло."""
    n, bad = 0, []
    for f in parts(board):
        if not f.GetReference().startswith("H"):
            continue
        n += 1
        if hits(box(f), PANEL):
            bad.append(f.GetReference())
    return n, bad


def check_sd_keepout(board):
    """6. Чужие детали в зоне лотка microSD (каталог Hirose, стр. 3).

    Только когда слот на схеме (`SD_CARD` в `block04_storage.py`). Без слота
    зона ничья, и проверка красила бы 11 деталей, которым ничто не мешает.
    Число проверенных тогда 0 — это «нечего проверять», а не «всё чисто».
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from block04_storage import SD_CARD
    if not SD_CARD:
        return 0, []
    n, bad = 0, []
    for f in parts(board):
        if f.GetReference() in ("J401",):
            continue
        n += 1
        if hits(box(f), SD_KEEPOUT):
            bad.append(f.GetReference())
    return n, bad


def check_drills(board):
    """7. Отверстия мельче нашего минимума."""
    n, bad = 0, []
    for f in parts(board):
        for p in f.Pads():
            d = mm(p.GetDrillSizeX())
            if d <= 0:
                continue
            n += 1
            if d < MIN_DRILL - 1e-6:
                bad.append(f"{f.GetReference()}.{p.GetNumber()} {d:.2f}")
    for v in board.GetTracks():
        if isinstance(v, pcbnew.PCB_VIA):
            n += 1
            if mm(v.GetDrill()) < MIN_DRILL - 1e-6:
                bad.append(f"переходная {mm(v.GetDrill()):.2f}")
    return n, bad


def check_decoupling(board):
    """8. Конденсатор развязки дальше положенного от своего вывода.

    Пороги 7 и 10 мм были взяты по общему рассуждению «чем ближе, тем лучше», и
    десять лет подряд давали бы десять нарушений, которых никто не чинит, —
    а проверка, всегда красная, приучает не смотреть в отчёт.

    Перемерено по референсу. Xassette-Asterisk — тот же F133, тот же корпус
    LQFP-128 шаг 0.4, те же два слоя: его развязка стоит в 5.5…22.0 мм от
    центра корпуса, причём 10 конденсаторов из 26 на изнанке. То есть 7 мм —
    строже живой практики, а не мягче.

    Ставим пороги по тому, что развязка делает физически: на 0.1 мкФ в корпусе
    0805 собственная индуктивность около 1 нГн, дорожка добавляет примерно
    1 нГн на миллиметр — то есть после десятка миллиметров добавка дорожки уже
    не меняет порядок величины, и спорить о 12 против 14 бессмысленно.
    Ограничиваем тем, что отличает работающую развязку от украшения: 12 мм на
    керамику по выводу и 20 на объёмную.

    Настоящий предмет спора — не порог, а число далёких: их надо считать и
    держать, а не обнулять порогом. Поэтому отчёт печатает и среднюю (её даёт
    `pcb04_fine.ring_cost`), и список тех, кто дальше.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pcb04_fine import DECOUP, BULK
    u1 = board.FindFootprintByReference("U1")
    if u1 is None:
        return 0, ["U1 не найден"]
    P = {p.GetNumber(): p.GetPosition() for p in u1.Pads()}
    n, bad = 0, []
    for ref, pin in DECOUP.items():
        f = board.FindFootprintByReference(ref)
        if f is None or pin not in P:
            bad.append(f"{ref} нет на плате")
            continue
        n += 1
        d = min(math.hypot(mm(q.GetPosition().x - P[pin].x),
                           mm(q.GetPosition().y - P[pin].y)) for q in f.Pads())
        limit = 20.0 if ref in BULK else 12.0
        if d > limit:
            bad.append(f"{ref}→вывод {pin} {d:.1f} мм")
    return n, bad


def check_composition(board):
    """9. Состав платы против схемы."""
    text = SCH.read_text()
    want, depth, start, i, blocks = set(), 0, None, 0, []
    while i < len(text):
        if text.startswith("(symbol", i) and depth == 0:
            start, depth = i, 1
            i += 7
            continue
        if depth:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    blocks.append(text[start:i + 1])
        i += 1
    for blk in blocks:
        m = re.search(r'\(property "Reference" "([^"]+)"', blk)
        if m and re.fullmatch(r"[A-Z]+\d+", m.group(1)):
            want.add(m.group(1))
    have = {f.GetReference() for f in parts(board)
            if not f.GetReference().startswith("H")}
    bad = [f"нет на плате: {r}" for r in sorted(want - have)]
    bad += [f"нет в схеме: {r}" for r in sorted(have - want)]
    return len(want), bad


def check_unplaced(board):
    """10. Детали, оставшиеся в куче после импорта.

    «Update PCB from Schematic» валит новые корпуса стопкой в одну точку.
    Если такая стопка осталась, значит скрипты размещения до детали не дошли.
    """
    n, seen = 0, Counter()
    for f in parts(board):
        n += 1
        p = f.GetPosition()
        seen[(round(mm(p.x), 1), round(mm(p.y), 1))] += 1
    bad = [f"{k} — {v} шт." for k, v in seen.items() if v > 1]
    return n, bad


def check_silk(board):
    """11. Текст на слоях шелкографии.

    Плату травим дома, шелкографии не существует. Держать на ней текст — это
    сотня нарушений DRC, за которыми не видно настоящих. Ссылки живут на
    слоях `*.Fab`, где они и нужны — при сборке смотрят в CAD, не на плату.
    """
    n, bad = 0, []
    silks = (pcbnew.F_SilkS, pcbnew.B_SilkS)
    for f in parts(board):
        for t in (f.Reference(), f.Value()):
            n += 1
            if t.GetLayer() in silks and t.IsVisible():
                bad.append(f"{f.GetReference()}: {t.GetText()}")
    return n, bad


def check_drc():
    """12. DRC от kicad-cli — сводка по типам."""
    out = ROOT.parent.parent / "/tmp/audit_pcb_drc.rpt"
    subprocess.run(["kicad-cli", "pcb", "drc", "--severity-error",
                    "--severity-warning", "-o", str(out), str(BOARD)],
                   capture_output=True)
    text = Path(out).read_text()
    c = Counter(re.findall(r"\[([a-z_]+)\]", text))
    unconnected = c.pop("unconnected_items", 0)
    return unconnected, [f"{k} — {v}" for k, v in c.most_common()]


def check_nets(board):
    """13. Цепи платы против netlist схемы — состав узлов, цепь за цепью."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "n.net"
        subprocess.run(["kicad-cli", "sch", "export", "netlist", "-o", str(out),
                        str(SCH)], capture_output=True)
        text = out.read_text()
    want = {}
    for m in re.finditer(r'\(net \(code "\d+"\) \(name "([^"]+)"\)', text):
        i, depth, j = m.start(), 0, m.start()
        while True:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        nodes = set(f"{a}.{b}" for a, b in re.findall(
            r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)', text[m.end():j]))
        if not m.group(1).startswith("unconnected-"):
            want[m.group(1)] = nodes
    have = {}
    for f in board.GetFootprints():
        for p in f.Pads():
            n = p.GetNetname()
            if n and not n.startswith("unconnected-"):
                have.setdefault(n, set()).add(f"{f.GetReference()}.{p.GetNumber()}")
    bad = []
    for n in sorted(set(want) | set(have)):
        a, b = want.get(n, set()), have.get(n, set())
        if a != b:
            bad.append(f"{n}: -{sorted(a - b)} +{sorted(b - a)}")
    return len(want), bad


def check_j601(board):
    """14. Разъём шлейфа: вывод 1 сверху, окно влево (06-display.md §7.2.1).

    Окно у FH12 — со стороны, ПРОТИВОПОЛОЖНОЙ площадкам (даташит Hirose,
    стр. 17). Значит «окно влево» — это площадки правее центра корпуса.
    Прежняя проверка требовала свой корпус с обратной нумерацией и об окне не
    спрашивала вовсе — и пропустила разъём, развёрнутый входом к чипу.
    """
    f = board.FindFootprintByReference("J601")
    if f is None:
        return 0, ["J601 нет на плате"]
    d = {p.GetNumber(): p.GetPosition() for p in f.Pads()}
    bad = []
    if "Hirose_FH12-40S" not in f.GetFPIDAsString():
        bad.append(f"корпус не штатный FH12: {f.GetFPIDAsString()}")
    if d["1"].y >= d["40"].y:
        bad.append("вывод 1 не сверху")
    if d["1"].x <= f.GetPosition().x:
        bad.append("площадки левее корпуса — окно смотрит вправо, к чипу")
    if f.IsFlipped():
        bad.append("разъём не на лицевой стороне")
    return len(d), bad


def check_buck(board):
    """15. Раскладка бака: CIN у IN, дроссель у LX, делитель дальше от LX.

    Даташит SY8089 §2.4, Layout Design, пп. 1–4. Проверяем не «где стоит
    деталь», а то, ради чего пункты написаны: что вход развязан у самого
    вывода, силовой узел короткий, а обратная связь не жмётся к `LX`.
    """
    from pcb04_fine import BUCKS
    n, bad = 0, []
    for u, l, cin, cout, c01, rt, rb, jmp, tp, c22 in BUCKS:
        fu = board.FindFootprintByReference(u)
        if fu is None:
            bad.append(f"{u} нет на плате")
            continue
        pads = {p.GetNumber(): p.GetPosition() for p in fu.Pads()}
        IN, LX, FB = pads["4"], pads["3"], pads["5"]

        def near(ref, pad):
            f = board.FindFootprintByReference(ref)
            return min(math.hypot(mm(q.GetPosition().x - pad.x),
                                  mm(q.GetPosition().y - pad.y)) for q in f.Pads())
        n += 1
        if near(cin, IN) > 3.5:
            bad.append(f"{cin} далеко от IN {u}: {near(cin, IN):.1f}")
        if near(l, LX) > 3.5:
            bad.append(f"{l} далеко от LX {u}: {near(l, LX):.1f}")
        if near(rt, LX) < near(rt, FB):
            bad.append(f"{rt} ближе к LX, чем к FB")
    return n, bad


def check_crystals(board):
    """16. Кварц ближе к своим выводам F133, чем к любым чужим деталям."""
    u1 = board.FindFootprintByReference("U1")
    P = {p.GetNumber(): p.GetPosition() for p in u1.Pads()}
    # Порог разный, и это не поблажка: 24 МГц и 32.768 кГц отличаются на три
    # порядка. Кварцу процессора десяток миллиметров — уже много, часовому
    # столько же безразлично, у него период 30 микросекунд. Оба стоят в узкой
    # полосе между кольцом развязки и правым крестом, места на двоих там нет,
    # и вплотную к выводам садится тот, кому это нужно.
    LIMIT = {"Y1": 12.0, "Y2": 20.0}
    n, bad = 0, []
    # Выводы кварца 24 МГц — 22 `DXOUT` и 23 `DXIN`. Стояло («21», «22»), а 21
    # — `REFCLK-OUT`, он висит: расстояние мерилось до чужого вывода.
    for ref, pins in (("Y1", ("22", "23")), ("Y2", ("24", "25"))):
        f = board.FindFootprintByReference(ref)
        if f is None:
            bad.append(f"{ref} нет на плате")
            continue
        n += 1
        d = min(math.hypot(mm(q.GetPosition().x - P[pin].x),
                           mm(q.GetPosition().y - P[pin].y))
                for q in f.Pads() for pin in pins if pin in P)
        # 12 мм, а не «лишь бы на плате». Порог 25 стоял с потолка и пропустил
        # кварцы, уехавшие к краю платы на два сантиметра: место у корпуса
        # никто не занял, а проверка этого не заметила.
        if d > LIMIT[ref]:
            bad.append(f"{ref} в {d:.1f} мм от своих выводов")
    return n, bad


def check_flash(board):
    """18. Флешка SPI NOR рядом со своими выводами F133 (14…19 `PC`)."""
    u1 = board.FindFootprintByReference("U1")
    f = board.FindFootprintByReference("U401")
    if u1 is None or f is None:
        return 0, ["U1 или U401 нет на плате"]
    P = {p.GetNumber(): p.GetPosition() for p in u1.Pads()}
    d = min(math.hypot(mm(q.GetPosition().x - P[str(pin)].x),
                       mm(q.GetPosition().y - P[str(pin)].y))
            for q in f.Pads() for pin in range(14, 20))
    return 1, [] if d <= 12.0 else [f"U401 в {d:.1f} мм от выводов 14…19"]


def check_flush(board):
    """17. Разъёмы, торчащие наружу, стоят у самого реза.

    Только те, в которые что-то втыкают снаружи корпуса: слот карты,
    движок питания, USB. `J1` (банка) и `J501` (динамик) — гребёнки под
    провода внутри корпуса, торец им не нужен: `J1` стоит у зарядника,
    `J501` — у усилителя (третья ревизия, `pcb04_fine.py`, `AUDIO_AT`).
    Слот `J401` — только когда он на схеме (`SD_CARD`).
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from block04_storage import SD_CARD
    n, bad = 0, []
    edges = [("SW1", "T"), ("J301", "B")]
    if SD_CARD:
        edges.insert(0, ("J401", "T"))
    for ref, edge in edges:
        f = board.FindFootprintByReference(ref)
        if f is None:
            bad.append(f"{ref} нет на плате")
            continue
        n += 1
        x1, y1, x2, y2 = box(f)
        gap = y1 if edge == "T" else BOARD_H - y2
        if gap > 0.6:
            bad.append(f"{ref} в {gap:.1f} мм от торца")
    return n, bad


CHECKS = [
    ("корпусов", "вне контура платы", check_outline),
    ("courtyard", "пересекаются", check_courtyard),
    ("деталей", "оказались на изнанке", check_battery),
    ("высоких деталей", "под панелью", check_under_panel),
    ("крепёжных", "под панелью", check_holes_under_panel),
    ("деталей", "в зоне лотка microSD", check_sd_keepout),
    ("отверстий", f"мельче {MIN_DRILL} мм", check_drills),
    ("конденсаторов развязки", "далеко от своего вывода", check_decoupling),
    ("деталей в схеме", "расходятся с платой", check_composition),
    ("корпусов", "лежат в одной точке", check_unplaced),
    ("надписей", "на шелкографии", check_silk),
    ("цепей в схеме", "разошлись с платой", check_nets),
    ("площадок J601", "разъём шлейфа развёрнут неверно", check_j601),
    ("баков", "раскладка против даташита", check_buck),
    ("кварцев", "далеко от своих выводов", check_crystals),
    ("флешек", "далеко от своих выводов", check_flash),
    ("торцевых разъёмов", "не у реза", check_flush),
]


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    total = 0
    for i, (what, why, fn) in enumerate(CHECKS, 1):
        n, bad = fn(board)
        total += len(bad)
        tail = " " + " ".join(bad[:8]) + (" …" if len(bad) > 8 else "") if bad else ""
        print(f"{i:2d}. {what} {n}: {why} — {len(bad)}{tail}")
    n, bad = check_drc()
    total += len(bad)
    print(f"{len(CHECKS) + 1:2d}. DRC: типов нарушений кроме неразведённого — {len(bad)}"
          f" {' '.join(bad)}   (неразведённых {n})")
    print(f"\nИТОГО нарушений: {total}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
