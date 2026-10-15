# -*- coding: utf-8 -*-
"""盘点 Soul 归档的数据分布，为「整理」定方案。只读，不写任何文件。"""
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

import argparse
_ap = argparse.ArgumentParser(description='盘点 Soul 归档的数据分布（只读）')
_ap.add_argument('--root', default='.', help='归档根目录（含 soul_export.json），默认当前目录')
_args = _ap.parse_args()

ROOT = os.path.abspath(_args.root)
JSON = os.path.join(ROOT, 'soul_export.json')

with open(JSON, 'r', encoding='utf-8') as f:
    d = json.load(f)

posts = d.get('posts') or []


def ts(p):
    t = p.get('createTime')
    return datetime.fromtimestamp(t / 1000) if isinstance(t, (int, float)) and t > 1e11 else None


def n_media(p):
    att = p.get('attachments') or []
    if not isinstance(att, list):
        return 0
    return len(att)


def has_video(p):
    for a in (p.get('attachments') or []):
        if isinstance(a, dict):
            u = (a.get('fileUrl') or '') + (a.get('relativePath') or '')
            if re.search(r'video|\.mp4|\.mov', u, re.I):
                return True
    return False


# ---- 年份分布 ----
year = defaultdict(lambda: {'条数': 0, '图': 0, '视频条': 0, '纯文字': 0})
for p in posts:
    t = ts(p)
    y = t.year if t else 0
    y = str(y)
    year[y]['条数'] += 1
    m = n_media(p)
    year[y]['图'] += m
    if has_video(p):
        year[y]['视频条'] += 1
    if m == 0:
        year[y]['纯文字'] += 1

print('=== 按年份分布 ===')
print('%-6s %5s %6s %7s %7s' % ('年份', '条数', '附件', '含视频', '纯文字'))
for y in sorted(year):
    r = year[y]
    print('%-6s %5d %6d %7d %7d' % (y, r['条数'], r['图'], r['视频条'], r['纯文字']))
print('%-6s %5d' % ('合计', len(posts)))
print()

# ---- 月份分布（看活跃期）----
month = Counter()
for p in posts:
    t = ts(p)
    if t:
        month['%d-%02d' % (t.year, t.month)] += 1
print('=== 最活跃的 10 个月 ===')
for m, c in month.most_common(10):
    print('  %s  %d 条' % (m, c))
print()

# ---- 正文 ----
lens = [len((p.get('content') or '').strip()) for p in posts]
empty = sum(1 for x in lens if x == 0)
print('=== 正文长度 ===')
print('  为空的 %d 条；平均 %.0f 字；最长 %d 字' % (empty, sum(lens) / len(lens), max(lens)))
print()

# ---- 标签 ----
tags = Counter()
for p in posts:
    for t in (p.get('tags') or []):
        if isinstance(t, dict):
            name = t.get('tagName') or t.get('name') or t.get('title')
            if name:
                tags[str(name)] += 1
        elif isinstance(t, str):
            tags[t] += 1
print('=== 出现最多的标签 top 15 ===')
for t, c in tags.most_common(15):
    print('  %-18s %d' % (t, c))
print()

# ---- 互动 ----
likes = [p.get('likeNum') for p in posts if isinstance(p.get('likeNum'), (int, float))]
comments = [p.get('commentNum') for p in posts if isinstance(p.get('commentNum'), (int, float))]
print('=== 互动（数据里有就统计）===')
print('  有 likeNum 的 %d 条，合计 %s' % (len(likes), sum(likes)))
print('  有 commentNum 的 %d 条，合计 %s' % (len(comments), sum(comments)))
met = d.get('metrics') or {}
for k in ('followNum', 'beFollowNum', 'beViewNum', 'postLikeNum', 'giftValue'):
    if k in met:
        print('  metrics.%s = %s' % (k, met[k]))
print()

# ---- 磁盘现状 ----
dirs = os.path.join(ROOT, '瞬间')
names = sorted(os.listdir(dirs))
print('=== 磁盘现状 ===')
print('  瞬间/ 下有 %d 个文件夹' % len(names))
print('  命名样例：')
for n in names[:3] + names[-2:]:
    print('    ', n)
# 检查命名是否统一
bad = [n for n in names if not re.match(r'^\d{4}_\d{8}_', n)]
print('  不符合「序号_日期_摘要」的文件夹：%d 个' % len(bad))
for n in bad[:5]:
    print('    !', n)
