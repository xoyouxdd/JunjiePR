# -*- coding: utf-8 -*-
"""周班表解析：只用 Python 标准库读取 .xlsx。"""
import re
import zipfile
import datetime as dt
import xml.etree.ElementTree as ET

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

# 注释中出现这些词：当天不进入轮岗，单独列出
EXCLUDE_WORDS = ['OJT', 'ZPD', '考试', 'Open', 'AP M&G', 'summer break']
# 整格是这些（不是班次）：当天不在本区域
OFF_WORDS = ['off', 'AL', 'ML', 'LOA', 'NA', 'TA LEAD', 'WTP', 'SDMT', 'SL', 'PH']


def _col_index(ref):
    letters = re.match(r'[A-Z]+', ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _text(node):
    return ''.join(t.text or '' for t in node.iter('{%s}t' % NS['m']))


def read_xlsx(path_or_file):
    """返回 {sheet_name: [[cell,...], ...]}，数值保持 float，字符串保持 str。"""
    z = zipfile.ZipFile(path_or_file)
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        root = ET.fromstring(z.read('xl/sharedStrings.xml'))
        shared = [_text(si) for si in root.findall('m:si', NS)]
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    target = {r.get('Id'): r.get('Target') for r in rels}
    out = {}
    for sh in wb.find('m:sheets', NS):
        t = target[sh.get(REL)]
        t = t.lstrip('/')
        if not t.startswith('xl/'):
            t = 'xl/' + t
        root = ET.fromstring(z.read(t))
        rows = []
        for row in root.iter('{%s}row' % NS['m']):
            r = int(row.get('r')) - 1
            while len(rows) <= r:
                rows.append([])
            cells = rows[r]
            for c in row.findall('m:c', NS):
                ci = _col_index(c.get('r'))
                typ = c.get('t')
                v = c.find('m:v', NS)
                if typ == 's' and v is not None:
                    val = shared[int(v.text)]
                elif typ == 'inlineStr':
                    val = _text(c)
                elif v is None:
                    val = None
                elif typ in ('str', 'e'):
                    val = v.text
                elif typ == 'b':
                    val = v.text == '1'
                else:
                    try:
                        val = float(v.text)
                    except (TypeError, ValueError):
                        val = v.text
                while len(cells) <= ci:
                    cells.append(None)
                cells[ci] = val
        out[sh.get('name')] = rows
    return out


def serial_to_date(x):
    return dt.date(1899, 12, 30) + dt.timedelta(days=int(x))


def hm(s):
    h, m = s.split(':')
    return int(h) * 60 + int(m)


def fmt(m):
    m = int(round(m))
    return '%02d:%02d' % ((m // 60) % 24, m % 60)


SHIFT_RE = re.compile(r'^\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})(.*)$', re.S)


def classify(cell):
    """把一个单元格解释为当天安排。"""
    s = '' if cell is None else str(cell).strip()
    if not s:
        return {'kind': 'off', 'label': ''}
    m = SHIFT_RE.match(s)
    if not m:
        return {'kind': 'off', 'label': s.replace('\n', ' ')}
    start, end = hm(m.group(1)), hm(m.group(2))
    if end <= start:
        end += 1440
    note = re.sub(r'\s+', ' ', m.group(3)).strip()
    info = {'start': start, 'end': end, 'note': note, 'tags': [], 'absences': []}
    low = note.lower()
    if note.startswith('(s)') or note.startswith('(m)'):
        info.update(kind='off', label='SDMT ' + fmt(start) + '-' + fmt(end))
        return info
    if '放休' in note:
        info.update(kind='off', label='放休')
        return info
    for w in EXCLUDE_WORDS:
        if w.lower() in low:
            info.update(kind='excluded', label=note)
            return info
    if '嚎叫节' in note:
        info['tags'].append('嚎叫节')
    for a, b in re.findall(r'(\d{4})\s*-\s*(\d{4})', note):
        s1 = int(a[:2]) * 60 + int(a[2:])
        e1 = int(b[:2]) * 60 + int(b[2:])
        label = re.sub(r'\d{4}\s*-\s*\d{4}', '', note).strip() or '固定暂离'
        info['absences'].append({'start': s1, 'end': e1, 'label': label})
    info['kind'] = 'rotation'
    return info


def parse_roster(path_or_file):
    """返回 {'people': {工号: {...}}, 'days': {'YYYY-MM-DD': {工号: 原始单元格}}}"""
    sheets = read_xlsx(path_or_file)
    rows = None
    for name, rs in sheets.items():
        if any(r and 'Name' in [str(x).strip() for x in r if x is not None] for r in rs[:20]):
            rows = rs
            break
    if rows is None:
        raise ValueError('没有找到包含 Name 列的工作表')
    hdr_i = next(i for i, r in enumerate(rows)
                 if r and 'Name' in [str(x).strip() for x in r if x is not None])
    H = [None if x is None else str(x).strip() for x in rows[hdr_i]]
    col = {name: H.index(name) for name in ('Name', 'ID') if name in H}
    if 'Name' not in col or 'ID' not in col:
        raise ValueError('表头缺少 Name 或 ID 列')
    c_type = H.index('Type') if 'Type' in H else None
    c_mark = H.index('Mark') if 'Mark' in H else None
    date_row = rows[hdr_i - 1] if hdr_i > 0 else []
    # 找到所有 Sun 开头的连续 7 列
    blocks = []
    for j, x in enumerate(H):
        if x == 'Sun' and H[j:j + 7] == WEEKDAYS:
            first = None
            for k in range(7):
                v = date_row[j + k] if j + k < len(date_row) else None
                if isinstance(v, float) and 30000 < v < 80000:
                    first = serial_to_date(v) - dt.timedelta(days=k)
                    break
            if first is None:
                continue
            blocks.append((j, first))
    if not blocks:
        raise ValueError('没有找到带日期的 Sun–Sat 班次列')
    hours = {}
    for back in (2, 3):
        if hdr_i - back < 0:
            continue
        hr = rows[hdr_i - back]
        for j, first in blocks:
            for k in range(7):
                v = hr[j + k] if j + k < len(hr) else None
                m = re.match(r'^\s*(\d{2})(\d{2})\s*-\s*(\d{2})(\d{2})', str(v or ''))
                if m:
                    d = (first + dt.timedelta(days=k)).isoformat()
                    hours.setdefault(d, {'open': '%s:%s' % m.group(1, 2), 'close': '%s:%s' % m.group(3, 4)})
    people, days = {}, {}
    for r in rows[hdr_i + 1:]:
        if not r or len(r) <= col['ID']:
            continue
        name, pid = r[col['Name']], r[col['ID']]
        if not name or pid is None:
            continue
        pid = str(int(pid)) if isinstance(pid, float) else str(pid).strip()
        people[pid] = {
            'pid': pid,
            'name': str(name).strip(),
            'type': (str(r[c_type]).strip() if c_type is not None and c_type < len(r) and r[c_type] else ''),
            'mark': (str(r[c_mark]).replace('\n', ' ').strip() if c_mark is not None and c_mark < len(r) and r[c_mark] else ''),
        }
        for j, first in blocks:
            for k in range(7):
                d = (first + dt.timedelta(days=k)).isoformat()
                v = r[j + k] if j + k < len(r) else None
                days.setdefault(d, {})[pid] = '' if v is None else str(v)
    return {'people': people, 'days': days, 'hours': hours}


if __name__ == '__main__':
    import sys, collections
    res = parse_roster(sys.argv[1])
    print(len(res['people']), 'people', sorted(res['days']))
    for d in sorted(res['days']):
        c = collections.Counter(classify(v)['kind'] for v in res['days'][d].values())
        print(d, dict(c))
