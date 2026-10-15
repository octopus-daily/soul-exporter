# -*- coding: utf-8 -*-
"""整理结果核验：正文.md 编码、缩略图完整性、浏览页数据自洽性。只读。"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

import argparse
_ap = argparse.ArgumentParser(description='整理结果核验（只读）')
_ap.add_argument('--root', default='.', help='归档根目录，默认当前目录')
_args = _ap.parse_args()

ROOT = os.path.abspath(_args.root)
POSTS = os.path.join(ROOT, '瞬间')
THUMBS = os.path.join(ROOT, '_缩略图')
HTML = os.path.join(ROOT, 'soul浏览.html')

print('=== 1) 正文.md 编码核验（用 UTF-8 读）===')
mds = []
for dp, _, ns in os.walk(POSTS):
    for n in ns:
        if n == '正文.md':
            mds.append(os.path.join(dp, n))
print('  共 %d 个正文.md' % len(mds))
sample = sorted(mds)[0]
with open(sample, 'r', encoding='utf-8') as f:
    content = f.read()
print('  抽样：%s' % os.path.basename(os.path.dirname(sample)))
for line in content.split('\n')[:6]:
    print('      | %s' % line)
# 是否含替换字符（真乱码的标志）
bad = [p for p in mds if '\ufffd' in open(p, encoding='utf-8', errors='replace').read()]
print('  含 U+FFFD 替换字符（真乱码）的文件：%d 个' % len(bad))
print()

print('=== 2) 缩略图核验 ===')
thumbs = []
for dp, _, ns in os.walk(THUMBS):
    for n in ns:
        thumbs.append(os.path.join(dp, n))
size = sum(os.path.getsize(t) for t in thumbs)
zero = [t for t in thumbs if os.path.getsize(t) == 0]
print('  缩略图文件 %d 个，%.1f MB，0 字节 %d 个' % (len(thumbs), size / 1048576, len(zero)))
# 用 PIL 抽验能否打开
try:
    from PIL import Image
    import random
    random.seed(1)
    picks = random.sample(thumbs, min(60, len(thumbs)))
    fail = []
    for p in picks:
        try:
            with Image.open(p) as im:
                im.verify()
        except Exception as e:
            fail.append((p, repr(e)[:60]))
    print('  抽验 60 张：失败 %d' % len(fail))
    for p, e in fail[:5]:
        print('    !', p, e)
except ImportError:
    print('  (无 PIL)')
print()

print('=== 3) 浏览页数据自洽性 ===')
raw = open(HTML, encoding='utf-8').read()
m = re.search(r'<script type="application/json" id="data">(.*?)</script>', raw, re.S)
payload = json.loads(m.group(1).replace('<\\/', '</'))
posts = payload['posts']
print('  内嵌 %d 条瞬间，totalFiles=%s' % (len(posts), payload['totalFiles']))

missing_full, missing_thumb, no_thumb = [], [], 0
n_files = 0
for p in posts:
    for f in p['files']:
        n_files += 1
        if not os.path.exists(os.path.join(ROOT, f['f'].replace('/', os.sep))):
            missing_full.append(f['f'])
        if f['t']:
            if not os.path.exists(os.path.join(ROOT, f['t'].replace('/', os.sep))):
                missing_thumb.append(f['t'])
        else:
            no_thumb += 1
print('  文件条目 %d 个' % n_files)
print('  full 路径不存在：%d' % len(missing_full))
print('  thumb 路径不存在：%d' % len(missing_thumb))
print('  没有缩略图的条目：%d' % no_thumb)

# 纯文字帖在页面里应可见
textonly = [p for p in posts if not p['files']]
print('  纯文字帖（页面中点开会弹正文）：%d 条' % len(textonly))
print('  示例：%s  %s' % (textonly[0]['date_full'], textonly[0]['text'][:30]) if textonly else '  无')
print()

print('=== 4) 年份分布（页面数据）===')
from collections import Counter
c = Counter(p['year'] for p in posts)
print('  ' + '  '.join('%s:%d' % (k, v) for k, v in sorted(c.items())))
print()

print('=== 5) 目录总览 ===')
for name in sorted(os.listdir(ROOT)):
    p = os.path.join(ROOT, name)
    if os.path.isdir(p):
        n = sum(len(fs) for _, _, fs in os.walk(p))
        sz = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(p) for f in fs)
        print('  %-14s %5d 文件  %8.1f MB' % (name + os.sep, n, sz / 1048576))
    else:
        print('  %-14s %8.1f KB' % (name, os.path.getsize(p) / 1024))
