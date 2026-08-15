#!/usr/bin/env python3
"""Нарезка `.dsn` по кучкам и разбор `.ses` — чтобы дробить разводку.

Зачем. Freerouting разводит всё разом и, если ему станет тесно, с одинаковым
безразличием пожертвует и кнопкой, и парой `D+`/`D−`. Разделение труда лучше:
**порядок и приоритеты — наши, тупая геометрия — его.**

Что для этого нужно, и всё это здесь:

* `sections` — разобрать `.dsn` на части по балансу скобок;
* `subset` — оставить в секции `network` только выбранные цепи. Их площадки
  никуда не деваются (они в секциях размещения), то есть чужое остаётся
  препятствием, а трогает он только выданное;
* `merge_wiring` — вписать уже проложенное обратно в `wiring` следующего круга.
  Для freerouting это становится неприкосновенной медью;
* `parse_ses` — разобрать ответ: дорожки и переходные в тех же координатах.

Единицы в DSN — микрометры, ось Y направлена вверх (в KiCad вниз), поэтому
знак Y меняется при переводе в координаты платы.
"""
import bisect
import re


# Имя цепи бывает в кавычках, и внутри кавычек встречаются скобки:
# `(net "Net-(C10-Pad1)"`. Наивный шаблон на таких ломается.
NET_NAME = re.compile(r'\(net\s+("[^"]*"|[^\s()]+)')


def _block(text, start):
    """Конец s-выражения, начинающегося в позиции start."""
    depth, i = 0, start
    while True:
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1


def sections(text):
    """Границы секций верхнего уровня: {имя: (начало, конец)}."""
    out = {}
    for m in re.finditer(r"\n  \((network|wiring|placement|library|structure)\b", text):
        s = m.start() + 3
        out[m.group(1)] = (s, _block(text, s))
    return out


def nets_of(text):
    """Имена цепей из секции network — без кавычек.

    Кавычки снимаем здесь, а не у вызывающего: `subset` сравнивает имена
    снятыми, и стоило один раз отдать их с кавычками, как все автоимена вида
    `"Net-(C10-Pad1)"` молча выпали из задания — сто тридцать цепей из ста
    шестидесяти. Ошибка выглядела как удача: трассировщик отчитался вчетверо
    лучше обычного.
    """
    s, e = sections(text)["network"]
    return [n.strip('"') for n in re.findall(NET_NAME, text[s:e])]


def subset(text, keep):
    """Оставить в network только цепи из `keep`, не тронув остальное.

    «Остальное» — это класс цепей, и он там не для красоты: в нём ширина
    дорожки, зазор и переходная. Пересобирая секцию из одних `(net …)`, я его
    выбрасывал, цепи оставались без правил вовсе, и freerouting разводил
    крохи. Стояло это с первой же пробы и объясняло её целиком.

    Имена выброшенных цепей вычищаются и из списка при классе — иначе он
    ссылается на то, чего в задании больше нет.
    """
    s, e = sections(text)["network"]
    body, out, i = text[s:e], ["(network\n"], 0
    while True:
        n = body.find("\n    (", i)
        if n < 0:
            break
        st = n + 5
        en = _block(body, st)
        blk = body[st:en]
        if blk.startswith("(net "):
            name = NET_NAME.match(blk).group(1).strip('"')
            if name in keep:
                out.append("    " + blk + "\n")
        elif blk.startswith("(class "):
            out.append("    " + _class_keep(blk, keep) + "\n")
        else:
            out.append("    " + blk + "\n")
        i = en
    out.append("  )")
    return text[:s] + "".join(out) + text[e:]


def _class_keep(blk, keep):
    """Вычистить из списка при классе имена цепей, которых не осталось."""
    head = blk.index("(", 1) if "(" in blk[1:] else len(blk) - 1
    names, rest = blk[:head], blk[head:]
    words = names.split()
    out = words[:2]                      # `(class` и его имя
    for w in words[2:]:
        if w.strip('"') in keep:
            out.append(w)
    return " ".join(out) + "\n      " + rest


def drop_planes(text):
    """Выкинуть `(plane …)` из секции structure.

    Для freerouting плоскость — территория своей цепи, куда чужим нельзя. У нас
    земля покрывает всю плату с обеих сторон, и пока плоскости были в файле,
    разводить ему было негде совсем: он честно возвращал «ничего не удалось»
    даже на кучке из двух цепей. Заливка не нужна ему вовсе — она переливается
    в KiCad после импорта и сама расступается перед дорожками.
    """
    out, i = [], 0
    while True:
        j = text.find("\n    (plane ", i)
        if j < 0:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:j])
        i = _block(text, j + 5)


def merge_wiring(text, extra):
    """Дописать строки в секцию wiring — это станет неприкосновенной медью."""
    if not extra:
        return text
    s, e = sections(text)["wiring"]
    return text[:e - 1] + "\n" + "\n".join(extra) + "\n  " + text[e - 1:]


def parse_ses(text):
    """Разобрать `.ses`: [('wire', слой, ширина, [(x, y)…], цепь), ('via', …)].

    Координаты — микрометры DSN, как есть; перевод в миллиметры и разворот Y
    делает тот, кто кладёт это на плату.

    Цепь берётся из ближайшего `(net ИМЯ` слева: в `.ses` дорожки лежат внутри
    такой обёртки. Все её вхождения размечаются один раз, дальше двоичный
    поиск, иначе на полутысяче отрезков разбор уходит в квадрат.
    """
    marks = [(m.start(), m.group(1).strip('"')) for m in NET_NAME.finditer(text)]
    starts = [s for s, _ in marks]

    def net_at(pos):
        i = bisect.bisect_left(starts, pos)
        return marks[i - 1][1] if i else None

    out = []
    for m in re.finditer(r"\(wire\s*\(path\s+(\S+)\s+([\d.]+)([^)]*)\)", text):
        layer, width, coords = m.group(1), float(m.group(2)), m.group(3)
        nums = [float(v) for v in re.findall(r"-?[\d.]+", coords)]
        pts = list(zip(nums[0::2], nums[1::2]))
        net = net_at(m.start())
        out.append(("wire", layer, width, pts, net))
    for m in re.finditer(r'\(via\s+(\S+)\s+(-?[\d.]+)\s+(-?[\d.]+)', text):
        net = net_at(m.start())
        out.append(("via", m.group(1), 0.0,
                    [(float(m.group(2)), float(m.group(3)))], net))
    return out
