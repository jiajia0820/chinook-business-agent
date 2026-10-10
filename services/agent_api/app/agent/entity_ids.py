"""Consume explicit catalog identifiers before time parsing."""
import re

ID_PATTERN = re.compile(r'(?:TrackId|商品(?:编号|ID))\s*[:：]?\s*(\d+(?:\s*(?:[-–—至]|[,，、])\s*\d+)*)', re.I)


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
                ranges.append([start,end])
            else:
                value=int(pair[0])
                if value < 1:
                    raise ValueError('商品编号无效')
                ids.append(value)
    return {'ranges':ranges,'ids':sorted(set(ids))}


def mask_identifiers(question):
    return ID_PATTERN.sub(lambda m:' ' * len(m[0]), question)


def selected(track_id, selection):
    return track_id in selection['ids'] or any(a<=track_id<=b for a,b in selection['ranges'])
