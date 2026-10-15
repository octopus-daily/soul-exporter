/**
 * Soul App 网页版（home.soulapp.cn）内容导出 · 浏览器控制台脚本
 * ==================================================================
 * 原理
 * ----
 * home.soulapp.cn 是纯前端 SPA，数据全靠 HTTP JSON 接口取。
 * 这些接口：同源（都在 home.soulapp.cn 下）、纯 cookie 鉴权、无签名。
 * 所以只要你在这个域名上处于登录状态，控制台里就能直接调，
 * 不需要抓包、不需要逆向签名、不需要装任何东西。
 *
 * 用到的接口（从打包产物 index-Damj4Bc9.js 等分片中提取）
 * ---------------------------------------------------------
 *   GET /pc/profile/content/tabs     -> 内容分类标签（瞬间/视频…）
 *   GET /pc/profile/content/posts    -> 瞬间列表（分页）
 *   GET /pc/profile/content/byType   -> 按类型取内容（如视频）
 *   GET /pc/v3/post/detail           -> 单条瞬间详情
 *   GET /pc/profile/info             -> 用户资料
 *   GET /pc/profile/metrics          -> 关注/粉丝/获赞数
 *
 * 必要请求头（前端固定值，必须带，否则服务端拒）
 * ------------------------------------------------
 *   os: PC   app-id: 10000010   app-version/version: 1.0.0
 *   channel-id: official   device-id: <localStorage.soul_device_id>
 *
 * 使用方法
 * --------
 * 1. 浏览器登录 https://home.soulapp.cn/ ，进「我的」页面
 * 2. F12 → Console，把本文件全部内容粘贴进去，回车
 * 3. 跑完自动下载 soul_export.json
 *
 * 可选开关（改下面 CONFIG 即可）
 * ------------------------------
 *   WITH_DETAIL : 是否逐条再拉一次详情（更全，但 485 条约多花 6~7 分钟）
 *   WITH_VIDEO  : 是否额外导出「视频」标签页
 *
 * 安全说明
 * --------
 * 脚本只在你自己的浏览器里跑，只读你自己账号的数据，
 * 不读取、不上传任何 cookie 或密码，产物只落到你自己电脑的下载目录。
 */

const CONFIG = {
  WITH_DETAIL: false,   // 逐条拉详情
  WITH_VIDEO: true,     // 额外导出视频标签页
  PAGE_SIZE: 15,        // 官方前端就是 15
  SLEEP_MS: 800,        // 每页间隔，温柔一点
  MAX_PAGES: 300,       // 安全上限
};

(async () => {
  const ORIGIN = location.origin;
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // ---------- 1. 身份与请求头 ----------
  const deviceId =
    localStorage.getItem('soul_device_id') ||
    (crypto.randomUUID?.() || Math.random().toString(36).slice(2));
  const userIdEcpt = localStorage.getItem('soul_userIdEcpt') || '';

  const HEADERS = {
    'Content-Type': 'application/json',
    os: 'PC',
    'app-id': '10000010',
    'app-version': '1.0.0',
    version: '1.0.0',
    'channel-id': 'official',
    'device-id': deviceId,
  };

  console.log('%c[Soul 导出] 开始', 'color:#0aa;font-weight:bold');
  console.log('  origin       =', ORIGIN);
  console.log('  userIdEcpt   =', userIdEcpt || '(未在 localStorage 找到，将依赖服务端默认「我」)');
  console.log('  deviceId     =', deviceId);

  // ---------- 2. 通用请求 ----------
  // 注意：Soul 这套接口用 code === 10001 表示成功（打包代码里 code!==10001 即抛错）
  async function api(path, params) {
    let url = ORIGIN + path;
    if (params) {
      const usp = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined && v !== null && v !== '') usp.set(k, String(v));
      }
      const qs = usp.toString();
      if (qs) url += '?' + qs;
    }
    const res = await fetch(url, { headers: HEADERS, credentials: 'include' });
    const text = await res.text();
    let json;
    try {
      json = JSON.parse(text);
    } catch {
      throw new Error(`非 JSON 响应 (HTTP ${res.status}): ${text.slice(0, 200)}`);
    }
    if (json.code !== 10001) {
      const err = new Error(`接口 ${path} 返回 code=${json.code} msg=${json.message || ''}`);
      err.payload = json;
      throw err;
    }
    return json.data;
  }

  // ---------- 3. 探测：先看一眼数据结构 ----------
  let tabs = null;
  try {
    tabs = await api('/pc/profile/content/tabs', { targetUserIdEcpt: userIdEcpt });
    console.log('%c[1/5] 内容标签 tabs =', 'color:#0aa');
    console.dir(tabs, { depth: 6 });
  } catch (e) {
    console.warn('拿 tabs 失败（不致命）:', e.message);
  }

  // ---------- 4. 分页拉「瞬间」 ----------
  // 前端原始逻辑：第一页 firstVisitFlag=1；
  // 之后用上一页最后一条的 postIdEcpt 作 lastIdEcpt、includeLastId=0、
  // 再带上 firstStartTime（= 上一页响应的 lastStartTime / earliestPostTime）。
  async function fetchAllPosts({ label, extraParams = {}, getPage, mapCursor }) {
    const all = [];
    let lastIdEcpt, firstStartTime, first = true, total;

    for (let page = 1; page <= CONFIG.MAX_PAGES; page++) {
      const params = {
        targetUserIdEcpt: userIdEcpt,
        pageSize: CONFIG.PAGE_SIZE,
        orderType: 'DESC',
        firstVisitFlag: first ? '1' : '0',
        ...(first ? {} : { lastIdEcpt, includeLastId: '0' }),
        ...(firstStartTime ? { firstStartTime } : {}),
        ...extraParams,
      };

      let data;
      try {
        data = await getPage(params);
      } catch (e) {
        console.error(`[${label}] 第 ${page} 页失败：`, e.message);
        if (e.payload) console.dir(e.payload, { depth: 4 });
        break;
      }

      if (page === 1) {
        console.log(`%c[${label}] 第 1 页响应字段 =`, 'color:#0aa', Object.keys(data || {}));
        total = data?.count;
        console.log(`[${label}] 服务端报总数 count =`, total);
        const sample = (data?.postList || [])[0];
        if (sample) {
          console.log(`[${label}] 单条瞬间的字段 =`, Object.keys(sample));
          console.dir(sample, { depth: 5 });
        }
      }

      const list = data?.postList || [];
      if (!list.length) {
        console.log(`[${label}] 第 ${page} 页为空，结束`);
        break;
      }
      all.push(...list);
      console.log(`[${label}] 第 ${page} 页 +${list.length} 条，累计 ${all.length}${total ? '/' + total : ''}`);

      // 游标推进
      const c = mapCursor(data, list);
      if (!c || !c.lastIdEcpt) {
        console.log(`[${label}] 游标为空，结束`);
        break;
      }
      lastIdEcpt = c.lastIdEcpt;
      firstStartTime = c.firstStartTime || firstStartTime;
      first = false;
      if (total && all.length >= total) {
        console.log(`[${label}] 已达总数，结束`);
        break;
      }
      await sleep(CONFIG.SLEEP_MS);
    }
    return all;
  }

  // 游标规则直接照搬前端：lastIdEcpt = 本页最后一条的 postIdEcpt
  const mapCursor = (data, list) => ({
    lastIdEcpt: list[list.length - 1]?.postIdEcpt,
    firstStartTime: data?.lastStartTime || data?.earliestPostTime,
  });

  const posts = await fetchAllPosts({
    label: '瞬间',
    getPage: p => api('/pc/profile/content/posts', p),
    mapCursor,
  });

  // ---------- 5. 视频标签页（可选） ----------
  let videoPosts = [];
  if (CONFIG.WITH_VIDEO) {
    try {
      videoPosts = await fetchAllPosts({
        label: '视频',
        extraParams: { postType: 'VIDEO' },
        getPage: p => api('/pc/profile/content/byType', p),
        mapCursor,
      });
    } catch (e) {
      console.warn('视频页失败（不致命）:', e.message);
    }
  }

  // ---------- 6. 逐条详情（可选） ----------
  const details = {};
  if (CONFIG.WITH_DETAIL) {
    console.log('%c[4/5] 逐条拉详情，条数 = ' + posts.length, 'color:#0aa');
    for (let i = 0; i < posts.length; i++) {
      const id = posts[i].postIdEcpt;
      if (!id) continue;
      try {
        details[id] = await api('/pc/v3/post/detail', { postIdEcpt: id });
      } catch (e) {
        console.warn(`详情失败 ${id}: ${e.message}`);
      }
      if (i % 20 === 0) console.log(`  详情 ${i + 1}/${posts.length}`);
      await sleep(CONFIG.SLEEP_MS);
    }
  }

  // ---------- 7. 资料 + 汇总 + 下载 ----------
  let profile = null, metrics = null;
  try { profile = await api('/pc/profile/info', { targetUserIdEcpt: userIdEcpt }); } catch {}
  try { metrics = await api('/pc/profile/metrics', { targetUserIdEcpt: userIdEcpt }); } catch {}

  // 把每条瞬间里的媒体直链抽出来，按条归类，
  // 方便之后用 soul_media_download.py 批量下图/下视频。
  const MEDIA_RE = /^https?:\/\/[^\s"]+\.(jpg|jpeg|png|webp|gif|mp4|mov|m3u8)(\?[^\s"]*)?$/i;
  function extractMedia(obj) {
    const out = [];
    const seen = new WeakSet();
    (function walk(o, path) {
      if (!o || typeof o !== 'object' || seen.has(o)) return;
      seen.add(o);
      for (const [k, v] of Object.entries(o)) {
        if (typeof v === 'string' && MEDIA_RE.test(v)) {
          out.push({ field: `${path}.${k}`.replace(/^\./, ''), url: v });
        } else if (v && typeof v === 'object') {
          walk(v, `${path}.${k}`);
        }
      }
    })(obj, '');
    // 同一个 URL 可能出现多次（原图/缩略图字段），去重但保留最先出现的字段名
    const byUrl = new Map();
    for (const m of out) if (!byUrl.has(m.url)) byUrl.set(m.url, m);
    return [...byUrl.values()];
  }

  const mediaManifest = [
    ...posts.map((p, i) => ({
      label: '瞬间', index: i + 1, postIdEcpt: p.postIdEcpt,
      postTime: p.postTime ?? p.createTime ?? p.showTime ?? null,
      media: extractMedia(p),
    })),
    ...videoPosts.map((p, i) => ({
      label: '视频', index: i + 1, postIdEcpt: p.postIdEcpt,
      postTime: p.postTime ?? p.createTime ?? p.showTime ?? null,
      media: extractMedia(p),
    })),
  ].filter(m => m.media.length);

  const mediaUrls = mediaManifest.flatMap(m =>
    m.media.map(x => ({ label: m.label, postIdEcpt: m.postIdEcpt, ...x })));

  const payload = {
    exportedAt: new Date().toISOString(),
    origin: ORIGIN,
    userIdEcpt,
    tabs,
    profile,
    metrics,
    counts: { posts: posts.length, videoPosts: videoPosts.length, mediaUrls: mediaUrls.length },
    posts,
    videoPosts,
    details: CONFIG.WITH_DETAIL ? details : undefined,
    mediaManifest,
    mediaUrls,
  };

  console.log('%c[5/5] 汇总', 'color:#0aa;font-weight:bold');
  console.table(payload.counts);

  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'soul_export.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);

  window.__soulExport = payload; // 留在内存里，方便你再折腾
  console.log('✅ 已触发下载 soul_export.json。数据也留在 window.__soulExport');
})();
