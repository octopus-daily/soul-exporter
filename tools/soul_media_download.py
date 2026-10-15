r"""
Soul 导出数据的媒体批量下载器  v3
==================================
配合 _soul_api_export.js 使用：先用浏览器控制台脚本导出 soul_export.json，
再用本脚本把里面的图片 / 视频 / 语音下到本地。

实测出来的坑（v1 → v3 逐条修掉）
--------------------------------
1. 阿里云**内网**地址（*-internal.aliyuncs.com）在公网必然连不上 —— 直接丢弃。
   这类地址是 attachments[].resizeFileUrl 字段。
2. 同一张图有 fileUrl / resizeFileUrl / shuiyinUrl 多个变体，URL 路径相同 ——
   按路径去重，只下一次（原始 1784 个链接 → 实际约 1000 个文件）。
3. 不是所有 URL 都是内容：commodityUrl 是商品链接、postExtModel.tailAnchor 是
   装饰图 —— 默认只取 attachments 里的真媒体，其余字段可用 --all-fields 放开。
4. 文件名按媒体类型命名（01_图片.jpeg / 02_视频.mp4 / 03_语音.m4a），
   文件夹名用「序号_日期_正文前几字」，翻目录一眼能认出来。
5. **不能只信 HTTP 状态码**：Soul 的 CDN 会用 HTTP 200 返回一段 JSON 错误体
   （如 Access denied），只看状态码会把错误页当图片存下来。
   现在按魔数校验内容，非法的一律标记为 denied 且不落盘。

用法
----
    python soul_media_download.py --json soul_export.json --out soul-media --dry-run
    python soul_media_download.py --json soul_export.json --out soul-media

常用参数
--------
    --dry-run       只打印计划，不真下（建议先跑）
    --workers 4     并发数（默认 4）
    --max 0         最多下多少个文件，0 = 不限
    --with-cover    连视频封面图一起下（默认不下）
    --all-fields    连非 attachments 的链接一起下（商品图、装饰图等）
    --estimate      抽样估算总体积（默认开，加 --no-estimate 关闭）
    --index         额外生成 soul索引.md（全部瞬间的可读清单）

目录结构
--------
    soul-media\
      瞬间\0001_20260726_我这里的红霞走在路上还是\01_图片.jpeg
      瞬间\0007_20260624_骑行vlog\02_视频.mp4
"""

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

CTX = ssl.create_default_context()
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36')
HEADERS = {
    'User-Agent': UA,
    'Referer': 'https://home.soulapp.cn/',
    'Accept': 'image/avif,image/webp,image/apng,image/*,video/*,*/*;q=0.8',
}

SKIP_HOST_RE = re.compile(r'-internal\.aliyuncs\.com$', re.I)
SKIP_FIELD_RE = re.compile(r'resize|shuiyin|moji', re.I)
# 只认 attachments 里的真媒体
CONTENT_FIELD_RE = re.compile(r'^attachments\.\d+\.(fileUrl|videoCoverUrl)$')

EXT_BY_TYPE = {
    'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/png': '.png',
    'image/webp': '.webp', 'image/gif': '.gif', 'image/avif': '.avif',
    'video/mp4': '.mp4', 'video/quicktime': '.mov', 'video/webm': '.webm',
}
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif', '.bmp'}
VIDEO_EXT = {'.mp4', '.mov', '.webm', '.m3u8', '.mkv'}
AUDIO_EXT = {'.m4a', '.mp3', '.aac', '.amr', '.wav', '.ogg'}


def safe(name, fallback='file', maxlen=40):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', '', str(name or '')).strip(' ._')
    return name[:maxlen] or fallback


def url_path(url):
    return urllib.parse.urlparse(url).path


def kind_of(url):
    """按 URL 判断媒体类型，给个中文名。"""
    ext = os.path.splitext(url_path(url))[1].lower()
    p = url.lower()
    if ext in AUDIO_EXT or '/audio' in p or '/voice' in p:
        return '语音', AUDIO_EXT
    if ext in VIDEO_EXT or '/video' in p:
        return '视频', VIDEO_EXT
    if ext in IMAGE_EXT or '/image' in p or '/img' in p:
        return '图片', IMAGE_EXT
    return '媒体', IMAGE_EXT


def guess_ext(url, content_type=None, fallback='.jpg'):
    if content_type:
        ct = content_type.split(';')[0].strip().lower()
        if ct in EXT_BY_TYPE:
            return EXT_BY_TYPE[ct]
    ext = os.path.splitext(url_path(url))[1].lower()
    if ext and len(ext) <= 6:
        return ext
    return fallback


def build_post_meta(data):
    meta = {}
    for arr in (data.get('posts') or [], data.get('videoPosts') or []):
        for p in arr:
            pid = p.get('postIdEcpt')
            if not pid:
                continue
            ts = p.get('createTime')
            date = ''
            if isinstance(ts, (int, float)) and ts > 1e11:
                date = datetime.fromtimestamp(ts / 1000).strftime('%Y%m%d')
            meta[pid] = {'date': date,
                         'text': safe((p.get('content') or '').strip()[:18], '无正文', 24)}
    return meta


def build_tasks(data, outdir, limit=0, with_cover=False, all_fields=False):
    meta = build_post_meta(data)
    manifest = data.get('mediaManifest')
    if not manifest:
        manifest = [{'label': m.get('label', '未分类'), 'index': i + 1,
                     'postIdEcpt': m.get('postIdEcpt', ''), 'media': [m]}
                    for i, m in enumerate(data.get('mediaUrls') or [])]

    tasks, seen, skipped = [], set(), Counter()

    for entry in manifest:
        label = safe(entry.get('label') or '未分类')
        idx = entry.get('index') or 0
        pid = entry.get('postIdEcpt') or ''
        m = meta.get(pid) or {}
        parts = [f'{idx:04d}', m.get('date') or '', m.get('text') or safe(pid, 'noid', 12)]
        folder = os.path.join(outdir, label, '_'.join(x for x in parts if x))

        seq = Counter()
        for item in entry.get('media') or []:
            url = item.get('url')
            field = item.get('field') or ''
            if not url:
                continue
            host = urllib.parse.urlparse(url).netloc
            if SKIP_HOST_RE.search(host):
                skipped['内网地址(公网连不上)'] += 1
                continue
            if SKIP_FIELD_RE.search(field):
                skipped['缩略图/水印变体'] += 1
                continue
            is_cover = bool(re.search(r'cover', field, re.I))
            if is_cover and not with_cover:
                skipped['视频封面'] += 1
                continue
            if not all_fields and not CONTENT_FIELD_RE.match(field):
                skipped['非内容链接(商品/装饰等)'] += 1
                continue
            key = url_path(url)
            if key in seen:
                skipped['同文件重复变体'] += 1
                continue
            seen.add(key)

            kind, _ = kind_of(url)
            seq[kind] += 1
            if is_cover:
                kind = '封面'
            tasks.append({'url': url, 'dir': folder, 'stem': f'{seq[kind]:02d}_{kind}',
                          'label': label, 'field': field, 'kind': kind})

    if limit:
        tasks = tasks[:limit]
    return tasks, skipped


def head_size(url):
    try:
        req = urllib.request.Request(url, headers=HEADERS, method='HEAD')
        with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
            n = r.headers.get('Content-Length')
            return int(n) if n and n.isdigit() else 0
    except Exception:
        return 0


def estimate_size(tasks, sample=40):
    """抽样估算总体积（只对视频这种大件有意义）。"""
    picks = tasks[:sample]
    if not picks:
        return 0, 0
    total = sum(head_size(t['url']) for t in picks)
    avg = total / len(picks)
    return avg, int(avg * len(tasks))


def looks_like_media(data):
    """按魔数判断是不是真的图片/视频/音频。
    Soul 的 CDN 会拿 HTTP 200 返回一段 JSON 错误体（如 Access denied），
    只看状态码会把错误页当图片存下来 —— 必须验内容。"""
    if len(data) < 12:
        return False
    if data[:3] == b'\xff\xd8\xff':                      # JPEG
        return True
    if data[:8] == b'\x89PNG\r\n\x1a\n':                 # PNG
        return True
    if data[:6] in (b'GIF87a', b'GIF89a'):               # GIF
        return True
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':    # WEBP
        return True
    if data[4:8] == b'ftyp':                             # MP4 / MOV
        return True
    if data[:4] == b'\x1aE\xdf\xa3':                     # WEBM / MKV
        return True
    if data[:4] == b'fLaC' or data[:3] == b'ID3' or data[:2] == b'\xff\xfb':  # 音频
        return True
    if data[:6] == b'#!AMR\n':
        return True
    return False


def download(task, dry_run=False):
    url, folder, stem = task['url'], task['dir'], task['stem']
    if dry_run:
        return ('dry', url)
    os.makedirs(folder, exist_ok=True)
    for f in os.listdir(folder):
        if f.startswith(stem + '.') and os.path.getsize(os.path.join(folder, f)) > 0:
            return ('skip', os.path.join(folder, f))
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=180, context=CTX) as r:
            ctype = r.headers.get('Content-Type') or ''
            data = r.read()
        if not data:
            return ('empty', url)
        if not looks_like_media(data):
            hint = data[:80].decode('utf-8', 'replace').replace('\n', ' ')
            return ('denied', '%s  [%s] %s' % (url, ctype.split(';')[0], hint))
        dst = os.path.join(folder, stem + guess_ext(url, ctype, '.jpg'))
        with open(dst, 'wb') as fh:
            fh.write(data)
        return ('ok', dst, len(data))
    except urllib.error.HTTPError as e:
        return ('http%s' % e.code, url)
    except Exception as e:
        return ('err', '%s %r' % (url, e))


def write_index(data, outdir):
    rows = ['# Soul 瞬间索引', '',
            '导出时间：%s' % data.get('exportedAt', '?'),
            '共 %d 条瞬间 / %d 条视频' % (len(data.get('posts') or []), len(data.get('videoPosts') or [])),
            '', '| # | 时间 | 正文 | 附件数 |', '|---|---|---|---|']
    for i, p in enumerate(data.get('posts') or [], 1):
        ts = p.get('createTime')
        date = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M') \
            if isinstance(ts, (int, float)) and ts > 1e11 else ''
        text = (p.get('content') or '').replace('\n', ' ').replace('|', '\\|').strip()
        rows.append('| %d | %s | %s | %d |' % (i, date, text, len(p.get('attachments') or [])))
    os.makedirs(outdir, exist_ok=True)
    dst = os.path.join(outdir, 'soul索引.md')
    with open(dst, 'w', encoding='utf-8') as f:
        f.write('\n'.join(rows))
    return dst


def main():
    ap = argparse.ArgumentParser(description='Soul 导出媒体批量下载器 v3')
    ap.add_argument('--json', required=True)
    ap.add_argument('--out', default='soul-media')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--max', type=int, default=0)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--with-cover', action='store_true')
    ap.add_argument('--all-fields', action='store_true')
    ap.add_argument('--estimate', dest='estimate', action='store_true', default=True)
    ap.add_argument('--no-estimate', dest='estimate', action='store_false')
    ap.add_argument('--index', action='store_true')
    args = ap.parse_args()

    with open(args.json, 'r', encoding='utf-8') as f:
        data = json.load(f)

    tasks, skipped = build_tasks(data, args.out, args.max, args.with_cover, args.all_fields)
    by_label = Counter(t['label'] for t in tasks)
    by_kind = Counter(t['kind'] for t in tasks)

    print('=== 下载计划 ===')
    for k, v in by_label.items():
        print('  %s：%d 个文件' % (k, v))
    print('  按类型：' + '，'.join('%s %d' % (k, v) for k, v in by_kind.most_common()))
    print('  合计：%d 个文件' % len(tasks))
    if skipped:
        print('  已跳过：' + '，'.join('%s %d' % (k, v) for k, v in skipped.most_common()))
    n_m3u8 = sum(1 for t in tasks if '.m3u8' in t['url'].lower())
    if n_m3u8:
        print('  注意：%d 个 m3u8（HLS 流）本脚本不处理，需 ffmpeg' % n_m3u8)

    if args.index:
        print('  索引已生成：', write_index(data, args.out))

    if args.dry_run:
        if args.estimate and tasks:
            vids = [t for t in tasks if t['kind'] == '视频']
            if vids:
                avg, tot = estimate_size(vids)
                print('\n  视频抽样估算：均个 %.1f MB × %d 个 ≈ %.0f MB' % (avg / 1048576, len(vids), tot / 1048576))
            print('  （图片未估算体积）')
        print('\n（--dry-run，未真的下载）前 5 个任务示例：')
        for t in tasks[:5]:
            rel = os.path.relpath(os.path.join(t['dir'], t['stem']), args.out)
            print('  ' + rel)
        return

    if not tasks:
        print('没有可下载的媒体')
        return

    stats, done, total_bytes = Counter(), 0, 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = [pool.submit(download, t, False) for t in tasks]
        for fut in as_completed(futs):
            res = fut.result()
            stats[res[0]] += 1
            done += 1
            if res[0] == 'ok':
                total_bytes += res[2]
            if done % 25 == 0 or done == len(tasks):
                print('  [%d/%d] 已下 %.1f MB  用时 %.0fs' % (done, len(tasks), total_bytes / 1048576, time.time() - t0))
            if res[0] not in ('ok', 'skip'):
                print('    ! %s  %s' % (res[0], res[1][:120]))

    print('\n完成。统计：', dict(stats))
    print('合计 %.1f MB，用时 %.0fs' % (total_bytes / 1048576, time.time() - t0))
    print('输出目录：', os.path.abspath(args.out))


if __name__ == '__main__':
    main()
