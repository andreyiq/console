#!/usr/bin/env python3
"""Снять тупиковые лучи веера.

`pcb07_fanout.py` выводит луч из-под корпуса у КАЖДОГО вывода с цепью — ещё
до того, как известно, с какой стороны к выводу подойдут. Когда питание
подошло изнутри корпуса (`pcb_hand.py`, полосы под F133), наружный луч
остаётся хвостом в никуда: DRC считает его висящим концом, а на плате это
лишняя медь, которая только сужает зазоры соседям.

Луч узнаём по замку (`locked` ставит веер). Тупик — по связности самого
pcbnew (`TestTrackEndpointDangling`), не по тексту отчёта DRC.

Если к середине тупикового луча подходит чужая дорожка той же цепи (стык
«в бок», так трассировщик и ручные перемычки цепляются к лучу), луч не
снимается, а укорачивается до самой дальней такой точки — иначе вместе с
хвостом ушла бы и связь.

Запускать после разводки и ДО заливки: заливка смотрит на медь.
"""
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "console.kicad_pcb"


def touch_along(t, others):
    """Самая дальняя от начала луча точка, где его касается чужая медь.

    Возвращает долю длины 0…1 или None. Касание — конец другой дорожки или
    переходная, лежащие на оси луча в пределах полуширин.
    """
    a, b = t.GetStart(), t.GetEnd()
    dx, dy = b.x - a.x, b.y - a.y
    n = dx * dx + dy * dy
    if n == 0:
        return None
    best = None
    for o in others:
        pts = ([o.GetPosition()] if isinstance(o, pcbnew.PCB_VIA)
               else [o.GetStart(), o.GetEnd()])
        # Мерка — полуширина ЧУЖОЙ меди, а не сумма полуширин. Новый конец
        # луча должен лечь внутрь чужой меди, иначе pcbnew снова назовёт его
        # висящим: при сумме луч резался у дорожки, идущей рядом впритирку
        # (оси в 0.2 мм), и оставался тупиком, только короче.
        reach = (o.GetWidth(pcbnew.F_Cu) / 2 if isinstance(o, pcbnew.PCB_VIA)
                 else o.GetWidth() / 2)
        for p in pts:
            s = ((p.x - a.x) * dx + (p.y - a.y) * dy) / n
            if not 0.0 <= s <= 1.0:
                continue
            qx, qy = a.x + s * dx, a.y + s * dy
            if (p.x - qx) ** 2 + (p.y - qy) ** 2 <= reach * reach:
                best = s if best is None else max(best, s)
    return best


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    gone = cut = kept = 0
    tried = set()
    for _ in range(4):                 # снятый луч может открыть следующий
        board.BuildConnectivity()
        conn = board.GetConnectivity()
        tracks = list(board.GetTracks())
        dead = [t for t in tracks
                if not isinstance(t, pcbnew.PCB_VIA) and t.IsLocked()
                and t.GetLayer() == pcbnew.F_Cu
                and conn.TestTrackEndpointDangling(t, False)]
        if not dead:
            break
        for t in dead:
            same = [o for o in tracks if o is not t
                    and o.GetNetCode() == t.GetNetCode()]
            s = touch_along(t, same)
            # Начало луча — на площадке: касание там не в счёт, оно и есть
            # тот вывод, от которого луч идёт.
            if s is None or s < 0.05:
                board.RemoveNative(t)
                gone += 1
            elif s > 0.95:
                # Касание у самого конца: в середину луча упёрлась чужая
                # дорожка той же цепи (стык «в бок», его pcbnew считает
                # висящим). Резать некуда. Луч часто лишний — вывод и так
                # сидит на той дорожке, — но это надо доказать, а не
                # предположить: снимаем, спрашиваем связность pcbnew, и если
                # что-то оторвалось, кладём обратно. Первая версия вместо
                # этого разбивала чужую дорожку в точке стыка — и оборвала две
                # связи: её половины потом снимались как тупики.
                key = (t.GetStart().x, t.GetStart().y, t.GetEnd().x, t.GetEnd().y)
                if key in tried:
                    continue
                tried.add(key)
                board.BuildConnectivity()
                before = board.GetConnectivity().GetUnconnectedCount(False)
                spare = pcbnew.PCB_TRACK(t)
                board.RemoveNative(t)
                board.BuildConnectivity()
                if board.GetConnectivity().GetUnconnectedCount(False) > before:
                    board.Add(spare)
                    kept += 1
                else:
                    gone += 1
            else:
                a, b = t.GetStart(), t.GetEnd()
                t.SetEnd(pcbnew.VECTOR2I(int(a.x + s * (b.x - a.x)),
                                         int(a.y + s * (b.y - a.y))))
                cut += 1
    board.Save(str(BOARD))
    board.BuildConnectivity()
    conn = board.GetConnectivity()
    left = [t for t in board.GetTracks()
            if not isinstance(t, pcbnew.PCB_VIA)
            and conn.TestTrackEndpointDangling(t, False)]
    print(f"тупиковых лучей снято: {gone}, укорочено: {cut}, "
          f"оставлено ради связи: {kept}; "
          f"висящих концов осталось: {len(left)}")
    for t in left:
        p = t.GetStart()
        print(f"    {t.GetNetname()} у ({pcbnew.ToMM(p.x):.2f}, "
              f"{pcbnew.ToMM(p.y):.2f}) — не луч веера, снимать руками")


if __name__ == "__main__":
    main()
