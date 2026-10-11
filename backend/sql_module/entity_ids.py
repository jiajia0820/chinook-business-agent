"""Explicit catalog identifiers shared by question and period parsing."""
import re

ID_PATTERN = re.compile(r'(?:Track\s*Id|商品(?:编号|ID))\s*(?:(?:为|等于|是)\s*)?[:：=]?\s*(\d+(?:\s*(?:[-–—至]|[,，、])\s*\d+)*)', re.I)


def identifier_spans(question):
    return list(ID_PATTERN.finditer(question))


def track_selection(question):
    ranges, ids = [], []
    for match in identifier_spans(question):
        for item in re.split(r'[,，、]', match[1]):
            pair = re.split(r'\s*[-–—至]\s*', item.strip())
            if len(pair) == 2:
                start, end = map(int, pair)
                if start < 1 or end < start:
                    raise ValueError('商品编号范围无效')
                ranges.append([start, end])
            else:
                value = int(pair[0])
                if value < 1:
                    raise ValueError('商品编号无效')
                ids.append(value)
    return {'ranges': ranges, 'ids': sorted(set(ids))}


def catalog_ids(question, limit=200):
    selection = track_selection(question)
    ids = set(selection['ids'])
    for start, end in selection['ranges']:
        if end - start + 1 > limit:
            raise ValueError('商品目录查询最多支持200个编号')
        ids.update(range(start, end + 1))
    if not ids or len(ids) > limit:
        raise ValueError('商品目录查询需要1至200个明确编号')
    return sorted(ids)


def mask_identifiers(question):
    return ID_PATTERN.sub(lambda m: ' ' * len(m[0]), question)


def selected(track_id, selection):
    return track_id in selection['ids'] or any(a <= track_id <= b for a, b in selection['ranges'])
