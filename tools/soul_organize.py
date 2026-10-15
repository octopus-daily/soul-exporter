# -*- coding: utf-8 -*-
r"""
Soul 归档整理器（可重复运行 / 幂等）
=====================================
用法：python soul_organize.py --root <归档根目录>

干三件事：
  1. 瞬间\<序号>_<日期>_<摘要>\  →  瞬间\<年份>\<序号>_<日期>_<摘要>\
  2. 纯文字帖（无附件）补成 瞬间\<年份>\<序号>_<日期>_<摘要>\正文.md
  3. 生成缩略图 _缩略图\ 和离线浏览页 soul浏览.html

设计成幂等的：已经整理过的会跳过，所以以后新增导出后**直接再跑一次**即可。
一切以 soul_export.json 为准，磁盘上的文件夹名不改，只挪位置。

用法：
    python soul_organize.py                   # 全做
    python soul_organize.py --skip-thumbs     # 不做缩略图
    python soul_organize.py --only-html       # 只重建浏览页
"""

import argparse
import html
import json
import os
import re
import shutil
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

# 根目录由命令行参数决定（见下方 main() 里的 --root）；此处先给默认值，
# 便于作为模块导入时也能定位文件。
ROOT = os.path.abspath(os.environ.get('SOUL_ARCHIVE_ROOT', '.'))
JSON_PATH = os.path.join(ROOT, 'soul_export.json')
POSTS_DIR = os.path.join(ROOT, '瞬间')
THUMB_DIR = os.path.join(ROOT, '_缩略图')
HTML_PATH = os.path.join(ROOT, 'soul浏览.html')

IMG_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
VID_EXT = {'.mp4', '.mov', '.webm', '.mkv'}

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


def safe(name, fallback='file', maxlen=40):
    """必须与 soul_media_download.py 里的 safe() 完全一致，
    这样纯文字帖生成的文件夹名才和下载器风格统一。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', '', str(name or '')).strip(' ._')
    return name[:maxlen] or fallback


def load_posts():
    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        d = json.load(f)
    out = []
    for i, p in enumerate(d.get('posts') or [], 1):
        ts = p.get('createTime')
        dt = datetime.fromtimestamp(ts / 1000) if isinstance(ts, (int, float)) and ts > 1e11 else None
        text = (p.get('content') or '').strip()
        out.append({
            'index': i,
            'year': str(dt.year) if dt else '未知年份',
            'date': dt.strftime('%Y%m%d') if dt else '00000000',
            'date_full': dt.strftime('%Y-%m-%d %H:%M') if dt else '',
            'text': text,
            'name': '%04d_%s_%s' % (i, dt.strftime('%Y%m%d') if dt else '00000000',
                                    safe(text[:18], '无正文', 24)),
            'attachments': p.get('attachments') or [],
            'postIdEcpt': p.get('postIdEcpt') or '',
        })
    return d, out


def scan_existing():
    r"""扫描 瞬间\ 现状：返回 {序号: 当前绝对路径}，兼容已按年分层和未分层两种状态。"""
    found = {}
    if not os.path.isdir(POSTS_DIR):
        return found
    for entry in os.listdir(POSTS_DIR):
        full = os.path.join(POSTS_DIR, entry)
        if not os.path.isdir(full):
            continue
        if re.fullmatch(r'\d{4}', entry) and entry >= '1990':
            # 年份目录
            for sub in os.listdir(full):
                subfull = os.path.join(full, sub)
                if os.path.isdir(subfull):
                    m = re.match(r'^(\d{4})_', sub)
                    if m:
                        found[int(m.group(1))] = subfull
        else:
            m = re.match(r'^(\d{4})_', entry)
            if m:
                found[int(m.group(1))] = full
    return found


def step_lay_out(posts):
    """按年份归档 + 给纯文字帖建文件夹。"""
    existing = scan_existing()
    moved = created = stayed = 0

    for p in posts:
        year_dir = os.path.join(POSTS_DIR, p['year'])
        target = os.path.join(year_dir, p['name'])
        cur = existing.get(p['index'])

        if p['attachments']:
            if cur is None:
                print('  ! 找不到 %s 的文件夹，跳过（可能媒体全缺失）' % p['name'])
                continue
            if os.path.normcase(cur) == os.path.normcase(target):
                stayed += 1
            else:
                os.makedirs(year_dir, exist_ok=True)
                if os.path.exists(target):
                    print('  ! 目标已存在，跳过：%s' % target)
                    continue
                shutil.move(cur, target)
                moved += 1
        else:
            # 纯文字帖：建文件夹 + 正文.md
            os.makedirs(target, exist_ok=True)
            md = os.path.join(target, '正文.md')
            if not os.path.exists(md):
                with open(md, 'w', encoding='utf-8') as f:
                    f.write('# %s\n\n' % (p['text'].split('\n')[0][:60] or '（无正文）'))
                    f.write('- 发布时间：%s\n' % p['date_full'])
                    f.write('- 序号：%d\n' % p['index'])
                    f.write('- 帖子 ID：`%s`\n' % p['postIdEcpt'])
                    f.write('- 附件：无（纯文字帖）\n\n---\n\n')
                    f.write(p['text'] + '\n')
                created += 1
            else:
                stayed += 1

    print('  挪位 %d 个，新建纯文字文件夹 %d 个，已在位 %d 个' % (moved, created, stayed))
    return moved + created


def make_thumb(src, dst):
    """生成一张缩略图。图片走 PIL，视频抽第 1 秒的帧。"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    ext = os.path.splitext(src)[1].lower()
    try:
        if ext in IMG_EXT:
            if not HAS_PIL:
                return False
            with Image.open(src) as im:
                im = im.convert('RGB')
                im.thumbnail((420, 420))
                im.save(dst, 'JPEG', quality=82, optimize=True)
            return True
        if ext in VID_EXT:
            if not HAS_CV2:
                return False
            cap = cv2.VideoCapture(src)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            fps = cap.get(cv2.CAP_PROP_FPS) or 25
            cap.set(cv2.CAP_PROP_POS_FRAMES, min(int(fps), max(0, total - 1)))
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = cap.read()
            cap.release()
            if not ok:
                return False
            h, w = frame.shape[:2]
            scale = 420 / max(w, h) if max(w, h) > 420 else 1.0
            if scale < 1.0:
                frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
            # 关键：cv2.imwrite 在 Windows 上不认非 ASCII 路径，
            # 会把路径按本地编码解释、非 ASCII 目录名直接崩坏
            # （反斜杠被相邻字节吃掉，文件落到上一层目录）。
            # 所以先编码成内存字节，再用 Python 自己写文件。
            ok2, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
            if not ok2:
                return False
            with open(dst, 'wb') as fh:
                fh.write(buf.tobytes())
            return os.path.exists(dst) and os.path.getsize(dst) > 0
    except Exception as e:
        print('    ! 缩略图失败 %s : %r' % (os.path.basename(src), e))
    return False


# ROOT 下允许存在的文件，其余 .jpg 视为写错位置的残留
ROOT_KEEP = {'Soul下载一览.jpg'}


def is_mangled(name):
    """识别 cv2 路径 bug 的产物。
    特征：中文目录名被按 GBK 解释过，把名字反向「GBK 编码 → UTF-8 解码」，
    能还原出 _缩略图\\ 就说明它是错位的缩略图，而不是用户自己放的文件。"""
    try:
        return '_缩略图' in name.encode('gbk', errors='ignore').decode('utf-8', errors='ignore')
    except Exception:
        return False


def clean_strays():
    """清掉误写到归档根目录的残留缩略图。只删能确证是 bug 产物的文件。"""
    removed = 0
    for n in os.listdir(ROOT):
        p = os.path.join(ROOT, n)
        if not os.path.isfile(p) or n in ROOT_KEEP:
            continue
        if os.path.splitext(n)[1].lower() not in ('.jpg', '.jpeg', '.png'):
            continue
        if not is_mangled(n):
            print('    （跳过不认识的文件，不删：%s）' % n[:60])
            continue
        print('    清掉错位文件：%s' % n[:60])
        os.remove(p)
        removed += 1
    return removed


def step_thumbs(posts):
    if not HAS_PIL:
        print('  ! 没有 PIL，跳过缩略图')
        return []
    made = skipped = failed = 0
    manifest = []
    for p in posts:
        folder = os.path.join(POSTS_DIR, p['year'], p['name'])
        files = []
        if os.path.isdir(folder):
            for fn in sorted(os.listdir(folder)):
                full = os.path.join(folder, fn)
                if not os.path.isfile(full):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext not in IMG_EXT and ext not in VID_EXT:
                    continue
                thumb = os.path.join(THUMB_DIR, p['name'], os.path.splitext(fn)[0] + '.jpg')
                kind = 'vid' if ext in VID_EXT else 'img'
                if os.path.exists(thumb) and os.path.getsize(thumb) > 0:
                    skipped += 1
                else:
                    if make_thumb(full, thumb):
                        made += 1
                    else:
                        failed += 1
                        thumb = None
                files.append({
                    'f': '瞬间/%s/%s/%s' % (p['year'], p['name'], fn),
                    't': ('_缩略图/%s/%s' % (p['name'], os.path.splitext(fn)[0] + '.jpg'))
                         if thumb else None,
                    'k': kind,
                    'size': os.path.getsize(full),
                })
        manifest.append({'post': p, 'files': files})
    print('  缩略图：新生成 %d，已存在 %d，失败 %d' % (made, skipped, failed))
    return manifest


HTML_TMPL = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Soul 瞬间归档（__COUNT__ 条）</title>
<style>
  :root { --bg:#f6f7fa; --card:#fff; --line:#e6e8ef; --ink:#1e2430; --dim:#7b8494; --brand:#2fb3a8; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.6 "Microsoft YaHei","PingFang SC",system-ui,sans-serif; }
  header { position:sticky; top:0; z-index:20; background:rgba(246,247,250,.94);
           backdrop-filter:blur(8px); border-bottom:1px solid var(--line); padding:14px 20px 10px; }
  h1 { margin:0 0 8px; font-size:19px; font-weight:700; }
  h1 small { color:var(--dim); font-weight:400; font-size:13px; margin-left:8px; }
  .bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
  .years { display:flex; flex-wrap:wrap; gap:6px; }
  .years button { border:1px solid var(--line); background:#fff; color:var(--ink);
                  padding:4px 11px; border-radius:999px; cursor:pointer; font-size:13px; }
  .years button.on { background:var(--brand); border-color:var(--brand); color:#fff; }
  .years button .n { opacity:.65; font-size:11px; margin-left:4px; }
  input[type=search] { flex:1; min-width:180px; padding:7px 12px; border:1px solid var(--line);
                       border-radius:9px; font-size:14px; background:#fff; color:var(--ink); }
  label.chk { display:flex; align-items:center; gap:5px; font-size:13px; color:var(--dim); user-select:none; }
  .stat { color:var(--dim); font-size:13px; }
  main { padding:16px 20px 60px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(238px,1fr)); gap:14px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          overflow:hidden; cursor:pointer; transition:.15s; display:flex; flex-direction:column; }
  .card:hover { transform:translateY(-2px); box-shadow:0 6px 18px rgba(30,36,48,.09); }
  .cover { position:relative; background:#eceef3; aspect-ratio:4/3; overflow:hidden; }
  .cover img { width:100%; height:100%; object-fit:cover; display:block; }
  .cover .noimg { display:flex; align-items:center; justify-content:center; height:100%;
                  color:var(--dim); font-size:13px; }
  .badge { position:absolute; right:7px; bottom:7px; background:rgba(0,0,0,.62); color:#fff;
           font-size:11px; padding:2px 7px; border-radius:6px; }
  .badge.v { background:rgba(47,179,168,.92); }
  .body { padding:10px 12px 12px; }
  .date { color:var(--dim); font-size:12px; margin-bottom:3px; }
  .text { font-size:13.5px; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical;
          overflow:hidden; }
  .empty { grid-column:1/-1; text-align:center; color:var(--dim); padding:60px 0; }
  #lb { position:fixed; inset:0; background:rgba(14,17,22,.93); z-index:100; display:none;
        flex-direction:column; }
  #lb.on { display:flex; }
  #lb .top { padding:12px 16px; color:#e8ebf0; display:flex; gap:12px; align-items:center; }
  #lb .top .t { flex:1; font-size:13px; opacity:.85; }
  #lb .top button { background:rgba(255,255,255,.14); color:#fff; border:0; width:34px; height:34px;
                    border-radius:8px; cursor:pointer; font-size:16px; }
  #lb .stage { flex:1; display:flex; align-items:center; justify-content:center; position:relative;
               padding:0 18px 18px; min-height:0; }
  #lb .stage img, #lb .stage video { max-width:100%; max-height:100%; object-fit:contain;
                                     border-radius:8px; background:#000; }
  #lb .nav { position:absolute; top:50%; transform:translateY(-50%); background:rgba(255,255,255,.16);
             color:#fff; border:0; width:42px; height:64px; border-radius:10px; cursor:pointer;
             font-size:22px; }
  #lb .nav.l { left:22px; } #lb .nav.r { right:22px; }
  #lb .thumbs { display:flex; gap:8px; justify-content:center; padding:0 16px 16px; flex-wrap:wrap; }
  #lb .thumbs img { width:56px; height:56px; object-fit:cover; border-radius:6px; cursor:pointer;
                    opacity:.5; }
  #lb .thumbs img.on { opacity:1; outline:2px solid var(--brand); }
</style>
</head>
<body>
<header>
  <h1>Soul 瞬间归档 <small id="sub"></small></h1>
  <div class="bar">
    <div class="years" id="years"></div>
  </div>
  <div class="bar" style="margin-top:8px">
    <input type="search" id="q" placeholder="搜索正文关键词…">
    <label class="chk"><input type="checkbox" id="onlyvid"> 只看含视频</label>
    <label class="chk"><input type="checkbox" id="onlyimg"> 只看有图</label>
    <span class="stat" id="stat"></span>
  </div>
</header>
<main><div class="grid" id="grid"></div></main>

<div id="lb">
  <div class="top">
    <div class="t" id="lbt"></div>
    <button id="lbc" title="关闭 (Esc)">✕</button>
  </div>
  <div class="stage">
    <button class="nav l" id="lbp">‹</button>
    <div id="lbs"></div>
    <button class="nav r" id="lbn">›</button>
  </div>
  <div class="thumbs" id="lbtabs"></div>
</div>

<script type="application/json" id="data">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const grid = document.getElementById('grid');
const yearsEl = document.getElementById('years');
const qEl = document.getElementById('q');
const onlyvid = document.getElementById('onlyvid');
const onlyimg = document.getElementById('onlyimg');
const statEl = document.getElementById('stat');
const enc = p => p ? encodeURI(p) : '';
let curYear = 'ALL';

document.getElementById('sub').textContent =
  DATA.posts.length + ' 条 · ' + DATA.totalFiles + ' 个媒体文件 · 导出 ' + DATA.exportedAt.slice(0,10);

// 年份按钮
const yc = {};
DATA.posts.forEach(p => { yc[p.year] = (yc[p.year]||0)+1; });
const ys = ['ALL'].concat(Object.keys(yc).sort());
ys.forEach(y => {
  const b = document.createElement('button');
  b.innerHTML = (y==='ALL'?'全部':y) + '<span class="n">' + (y==='ALL'?DATA.posts.length:yc[y]) + '</span>';
  b.onclick = () => { curYear = y; render(); };
  b.dataset.y = y;
  yearsEl.appendChild(b);
});

function filtered() {
  const kw = qEl.value.trim().toLowerCase();
  return DATA.posts.filter(p => {
    if (curYear !== 'ALL' && p.year !== curYear) return false;
    if (onlyvid.checked && !p.nvid) return false;
    if (onlyimg.checked && !p.nimg) return false;
    if (kw && !(p.text.toLowerCase().includes(kw) || p.date.includes(kw))) return false;
    return true;
  });
}

function render() {
  [...yearsEl.children].forEach(b => b.classList.toggle('on', b.dataset.y === curYear));
  const list = filtered();
  statEl.textContent = '显示 ' + list.length + ' / ' + DATA.posts.length + ' 条';
  grid.innerHTML = '';
  if (!list.length) { grid.innerHTML = '<div class="empty">没有匹配的瞬间</div>'; return; }
  const frag = document.createDocumentFragment();
  list.forEach(p => {
    const c = document.createElement('div');
    c.className = 'card';
    const cov = p.cover
      ? '<img loading="lazy" src="' + enc(p.cover) + '" alt="">'
      : '<div class="noimg">纯文字</div>';
    c.innerHTML =
      '<div class="cover">' + cov +
        (p.nvid ? '<span class="badge v">▶ ' + p.nvid + '</span>'
                : (p.files.length ? '<span class="badge">' + p.files.length + ' 图</span>' : '')) +
      '</div>' +
      '<div class="body"><div class="date">' + p.date_full + '</div>' +
      '<div class="text">' + p.textEsc + '</div></div>';
    c.onclick = () => openLb(p);
    frag.appendChild(c);
  });
  grid.appendChild(frag);
}

// ---- 灯箱 ----
const lb = document.getElementById('lb');
const lbs = document.getElementById('lbs');
const lbt = document.getElementById('lbt');
const lbtabs = document.getElementById('lbtabs');
let cur = null, curIdx = 0;

function openLb(p) {
  if (!p.files.length) { alert(p.date_full + '\n\n' + p.text); return; }
  cur = p; curIdx = 0; show(); lb.classList.add('on');
}
function show() {
  const f = cur.files[curIdx];
  lbt.textContent = cur.date_full + '　' + curIdx+1 + '/' + cur.files.length + '　' + cur.text.slice(0,60);
  lbs.innerHTML = f.k === 'vid'
    ? '<video src="' + enc(f.f) + '" controls autoplay></video>'
    : '<img src="' + enc(f.f) + '" alt="">';
  lbtabs.innerHTML = '';
  cur.files.forEach((x, i) => {
    const im = document.createElement('img');
    im.src = enc(x.t || x.f);
    if (i === curIdx) im.classList.add('on');
    im.onclick = e => { e.stopPropagation(); curIdx = i; show(); };
    lbtabs.appendChild(im);
  });
  document.querySelector('#lb .nav.l').style.visibility = curIdx > 0 ? 'visible' : 'hidden';
  document.querySelector('#lb .nav.r').style.visibility = curIdx < cur.files.length-1 ? 'visible' : 'hidden';
}
document.getElementById('lbc').onclick = () => { lb.classList.remove('on'); lbs.innerHTML = ''; };
document.getElementById('lbp').onclick = e => { e.stopPropagation(); if (curIdx>0){curIdx--;show();} };
document.getElementById('lbn').onclick = e => { e.stopPropagation(); if (curIdx<cur.files.length-1){curIdx++;show();} };
lb.onclick = e => { if (e.target === lb) { lb.classList.remove('on'); lbs.innerHTML=''; } };
document.addEventListener('keydown', e => {
  if (!lb.classList.contains('on')) return;
  if (e.key === 'Escape') { lb.classList.remove('on'); lbs.innerHTML = ''; }
  if (e.key === 'ArrowLeft' && curIdx > 0) { curIdx--; show(); }
  if (e.key === 'ArrowRight' && curIdx < cur.files.length-1) { curIdx++; show(); }
});

qEl.oninput = render;
onlyvid.onchange = render;
onlyimg.onchange = render;
render();
</script>
</body>
</html>
'''


def step_html(manifest, data):
    posts = []
    total_files = 0
    for m in manifest:
        p = m['post']
        files = m['files']
        total_files += len(files)
        nvid = sum(1 for f in files if f['k'] == 'vid')
        nimg = sum(1 for f in files if f['k'] == 'img')
        cover = None
        for f in files:
            if f['t']:
                cover = f['t']
                if f['k'] == 'img':
                    break
        posts.append({
            'i': p['index'],
            'year': p['year'],
            'date_full': p['date_full'],
            'date': p['date'],
            'text': p['text'],
            'textEsc': html.escape(p['text'] or '（无正文）').replace('\n', '<br>'),
            'files': files,
            'nvid': nvid,
            'nimg': nimg,
            'cover': cover,
        })
    payload = {
        'exportedAt': data.get('exportedAt', ''),
        'totalFiles': total_files,
        'posts': posts,
    }
    blob = json.dumps(payload, ensure_ascii=False).replace('</', '<\\/')
    out = HTML_TMPL.replace('__DATA__', blob).replace('__COUNT__', str(len(posts)))
    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(out)
    print('  浏览页已写出：%s  (%.1f MB)' % (HTML_PATH, os.path.getsize(HTML_PATH) / 1048576))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default='.', help='归档根目录（含 soul_export.json），默认当前目录')
    ap.add_argument('--skip-thumbs', action='store_true')
    ap.add_argument('--only-html', action='store_true')
    args = ap.parse_args()

    # 用命令行指定的根目录覆盖模块级路径
    global ROOT, JSON_PATH, POSTS_DIR, THUMB_DIR, HTML_PATH
    ROOT = os.path.abspath(args.root)
    JSON_PATH = os.path.join(ROOT, 'soul_export.json')
    POSTS_DIR = os.path.join(ROOT, '瞬间')
    THUMB_DIR = os.path.join(ROOT, '_缩略图')
    HTML_PATH = os.path.join(ROOT, 'soul浏览.html')

    print('=== Soul 归档整理 ===')
    print('  根目录: %s' % ROOT)
    data, posts = load_posts()
    print('  JSON 读到 %d 条瞬间' % len(posts))

    if not args.only_html:
        print('\n[1/3] 按年份归档 + 纯文字帖补文件夹')
        step_lay_out(posts)

    print('\n[2/3] 生成缩略图')
    n_stray = clean_strays()
    if n_stray:
        print('  清掉错位残留 %d 个' % n_stray)
    if args.skip_thumbs and not args.only_html:
        manifest = None
    else:
        manifest = step_thumbs(posts)

    print('\n[3/3] 生成离线浏览页')
    if manifest is None:
        # 不重新生成缩略图时，从磁盘现状重建 manifest
        manifest = []
        for p in posts:
            folder = os.path.join(POSTS_DIR, p['year'], p['name'])
            files = []
            if os.path.isdir(folder):
                for fn in sorted(os.listdir(folder)):
                    ext = os.path.splitext(fn)[1].lower()
                    if ext not in IMG_EXT and ext not in VID_EXT:
                        continue
                    t = os.path.join(THUMB_DIR, p['name'], os.path.splitext(fn)[0] + '.jpg')
                    files.append({
                        'f': '瞬间/%s/%s/%s' % (p['year'], p['name'], fn),
                        't': ('_缩略图/%s/%s' % (p['name'], os.path.splitext(fn)[0] + '.jpg'))
                             if os.path.exists(t) else None,
                        'k': 'vid' if ext in VID_EXT else 'img',
                        'size': os.path.getsize(os.path.join(folder, fn)),
                    })
            manifest.append({'post': p, 'files': files})
    step_html(manifest, data)

    # 把本脚本装进 tools\，方便以后重跑
    tools = os.path.join(ROOT, 'tools')
    os.makedirs(tools, exist_ok=True)
    me = os.path.abspath(__file__)
    dst = os.path.join(tools, 'soul_organize.py')
    if os.path.normcase(me) != os.path.normcase(dst):
        shutil.copy2(me, dst)
        print('\n  整理脚本已装入：%s' % dst)

    print('\n完成。')


if __name__ == '__main__':
    main()
