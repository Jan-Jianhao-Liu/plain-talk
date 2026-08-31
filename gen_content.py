# -*- coding: utf-8 -*-
"""
科普解读站内容生成：用本机 Ollama(qwen3.5:4b) 生成当日内容，追加进 plain_store.json。
素材联动：读取上一级 archive_store.json 的近期论文，作为「延伸阅读」跳转源，实现两站联动。
幂等：若当日已生成则跳过。
"""
import json, os, time, urllib.request, urllib.parse, re
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, 'plain_store.json')
ARCHIVE_STORE = os.path.join(os.path.dirname(HERE), 'archive_store.json')
AIHOT = 'https://aihot.virxact.com/api/v1/items'
UA = 'aihot-skill/1.2.1 (+https://aihot.virxact.com/aihot-skill/)'
OLLAMA = 'http://127.0.0.1:11434/api/chat'
MODEL = 'qwen3.5:4b'
SH = timezone(timedelta(hours=8))
WEEK = ['周一','周二','周三','周四','周五','周六','周日']

SCIENCE_POOL = [
    "什么是 AI Agent？（区别于普通对话大模型，能自主规划、记忆、调用工具完成长任务）",
    "Agent 三大核心模块：记忆、规划、工具调用",
    "什么是大模型（LLM）？它怎么\"理解\"语言",
    "什么是指令（Prompt）？写好提示词的核心技巧",
    "什么是 RAG（检索增强生成）？让 AI 先查资料再回答",
    "什么是向量数据库？AI 如何\"记住\"语义相似度",
    "什么是微调（Fine-tuning）？让通用模型学会专长",
    "什么是 Embedding？把文字变成可计算的数字向量",
    "什么是多模态 AI？让模型同时看懂图、听懂话、读文档",
    "什么是幻觉（Hallucination）？AI 为什么会一本正经胡说",
    "什么是对齐（Alignment）？让 AI 行为符合人类价值观",
    "什么是推理模型？AI 的\"慢思考\"与思维链（CoT）",
    "什么是 MCP（模型上下文协议）？Agent 连接工具的统一标准",
    "什么是 AI 工作流（Workflow）？把多个步骤串成自动化",
    "什么是知识图谱？用关系网络组织知识",
    "什么是强化学习？AI 如何从反馈中自我改进",
    "什么是 Transformer？现代大模型的底层架构",
    "如何评测 AI？常用指标与基准测试怎么看",
    "什么是边缘 AI？把模型跑在手机和摄像头里",
    "什么是具身智能？给 AI 一个身体去真实行动",
    "什么是世界模型？AI 在脑中模拟物理世界",
    "什么是智能体记忆？短期记忆与长期记忆的工程实现",
]

def pick_science_topics(today, k=2):
    """按日期确定性轮换 k 个不重复入门主题，避免每天重复、且与历史错开。"""
    from datetime import date as _date
    d = datetime.strptime(today, '%Y-%m-%d').date()
    n = (d - _date(2026, 1, 1)).days
    L = len(SCIENCE_POOL)
    a = (n * 2) % L
    return [SCIENCE_POOL[(a + i) % L] for i in range(k)]

def now_day():
    return datetime.now(SH).strftime('%Y-%m-%d')

def load_store():
    if os.path.exists(STORE):
        try: return json.load(open(STORE, encoding='utf-8'))
        except Exception: pass
    return {'meta': {}, 'science': {}, 'news': {}}

def save_store(s):
    json.dump(s, open(STORE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

def ollama(messages):
    body = json.dumps({'model': MODEL, 'think': False, 'stream': False, 'messages': messages}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={'Content-Type': 'application/json'})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                return json.load(r).get('message', {}).get('content', '').strip()
        except Exception as e:
            last = e
            print(f'  ollama retry ({e})', flush=True)
            time.sleep(3)
    raise last if last else RuntimeError('ollama failed')

def strip_fence(text):
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text, re.I)
    if m: return m.group(1).strip()
    return text.strip()

def _repair_inner_quotes(s):
    """修复模型在中文串值里混用 ASCII 双引号导致的 JSON 截断。
    例：'"…“最烦人的 CAPTCHA"，目的是…"' —— 收尾的直角引号并非真正的串结束符。
    判据：字符串内的 '"' 若后面紧跟的非空白字符不是 , : } ] 之一，则视为内部引号，替换为 ”。"""
    out = []; in_str = False; esc = False
    for i, ch in enumerate(s):
        if esc:
            out.append(ch); esc = False; continue
        if ch == '\\':
            out.append(ch); esc = True; continue
        if ch == '"':
            if not in_str:
                in_str = True; out.append(ch); continue
            j = i + 1
            while j < len(s) and s[j] in ' \t\r\n':
                j += 1
            if j >= len(s) or s[j] in ',:}]':
                in_str = False; out.append(ch)
            else:
                out.append('\u201d')
            continue
        out.append(ch)
    return ''.join(out)

def _salvage_objects(text, keys=('title', 'interpret')):
    """最后兜底：按「下一个键名/右花括号」为锚点抽取字段，容忍串内脏引号。"""
    k1, k2 = keys
    pat = re.compile(
        r'"%s"\s*:\s*"(?P<a>.*?)"\s*,\s*"%s"\s*:\s*"(?P<b>.*?)"\s*\n?\s*[}\]]' % (k1, k2),
        re.S)
    return [{k1: m.group('a').strip(), k2: m.group('b').strip()} for m in pat.finditer(text)]

def parse_json_array(text):
    text = strip_fence(text)
    cands = [text]
    s = text.find('['); e = text.rfind(']')
    if s >= 0 and e > s:
        cands.append(text[s:e+1])
    for c in cands:
        for variant in (c, _repair_inner_quotes(c)):
            try:
                v = json.loads(variant)
                if isinstance(v, list):
                    return v
            except Exception:
                pass
    for c in cands:
        got = _salvage_objects(c)
        if got:
            print(f'  JSON 解析降级：正则抢救出 {len(got)} 条', flush=True)
            return got
    return []

def gen_science(today):
    topics = pick_science_topics(today)
    prompt = (
        "你是面向零基础入门学习者的 AI 科普作者。请严格按顺序生成以下 %d 个「AI 入门科普」条目，"
        "用简体中文，返回 JSON 数组（不要代码块、不要多余解释），每个元素格式：\n"
        '{"title":"【入门科普】xxx","level":"入门","body":"200-400字，少公式多比喻，用生活化例子讲清概念","diagram":"一句话示意图说明，没有则空字符串"}\n'
        "条目主题依次为：\n" + "\n".join(f"{i+1}. {t}" for i, t in enumerate(topics))
    )
    msgs = [{'role': 'system', 'content': '你擅长用通俗比喻讲解 AI 概念，杜绝生僻术语。'},
            {'role': 'user', 'content': prompt}]
    out = ollama(msgs)
    arr = parse_json_array(out)
    res = []
    for i, it in enumerate(arr[:len(topics)]):
        if not isinstance(it, dict):
            continue
        res.append({'id': f'sci-{today}-{i+1}', 'domain': 'AI Agent',
                    'title': it.get('title', '') or f'【入门科普】{topics[i][:12]}',
                    'level': it.get('level', '入门'), 'body': it.get('body', ''),
                    'diagram': it.get('diagram', ''), 'link': ''})
    return res

# 大众新闻素材：AI HOT 资讯池（非论文），按关键词启发式打领域
DOMAIN_HINTS = [
    ('AI Agent', ['智能体', 'agent', '代理', 'harness', '多智能体', '工作流']),
    ('具身智能', ['具身', '机器人', '人形', 'embodied', '机械臂', '四足', '无人']),
    ('世界模型', ['世界模型', 'world model', '物理ai', '物理世界', '预测模型']),
    ('虚拟仿真社会', ['仿真', '虚拟', '数字孪生', '模拟社会', '元宇宙', '数字人']),
    ('AI认知算法', ['认知', '推理', '对齐', '安全', '思维', '意识', '心理', '记忆']),
    ('LLM', ['大模型', '语言模型', 'openai', 'deepseek', '豆包', '智谱', 'gpt', 'claude',
             'gemini', 'llm', '模型', 'api', 'token']),
]

def domain_of(title, summary=''):
    t = ((title or '') + ' ' + (summary or '')[:120]).lower()
    for dom, kws in DOMAIN_HINTS:
        if any(k in t for k in kws):
            return dom
    return 'LLM'

def fetch_aihot_news(limit=60):
    """拉 AI HOT 资讯池（非论文），按时间倒序。"""
    params = {'mode': 'all', 'window': '7d', 'limit': str(limit)}
    url = AIHOT + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception as e:
        print(f'  aihot news fetch failed: {e}', flush=True)
        return []
    out = []
    for it in data.get('items', []):
        cat = (it.get('category') or '').lower()
        links = it.get('links', {}) or {}
        orig = links.get('original') or links.get('aihot') or ''
        if 'paper' in cat or 'arxiv' in orig:
            continue
        title = (it.get('title') or it.get('originalTitle') or '').strip()
        if not title:
            continue
        src = it.get('source')
        src_name = src.get('name', 'AI 资讯') if isinstance(src, dict) else str(src or 'AI 资讯')
        out.append({
            'title': title,
            'summary': (it.get('summary') or '').strip(),
            'ts': it.get('publishedAt') or it.get('discoveredAt') or '',
            'url': orig,
            'source': src_name,
        })
    out.sort(key=lambda x: x['ts'], reverse=True)
    return out

def pick_news_material(today, limit=5, exclude_titles=None):
    """每日 5 条大众新闻。优先严格当日（publishedAt == today）；
    但本任务在凌晨 04:00 执行，资讯池当日条目通常尚未产生，此时回退到池中
    **最新可用日期**（上限 3 天内）的一批素材，保证解读板块每日有新内容。
    已入库的标题会被排除，避免跨日重复。不足则少放，不凑更旧的闻。"""
    exclude = {(t or '').strip() for t in (exclude_titles or ())}
    pool = [it for it in fetch_aihot_news(60)
            if (it.get('title') or '').strip() not in exclude]
    items = [it for it in pool if (it.get('ts') or '').startswith(today)]
    if not items and pool:
        floor = (datetime.strptime(today, '%Y-%m-%d') - timedelta(days=3)).strftime('%Y-%m-%d')
        dates = sorted({(it.get('ts') or '')[:10] for it in pool
                        if (it.get('ts') or '')[:10] >= floor}, reverse=True)
        if dates:
            items = [it for it in pool if (it.get('ts') or '').startswith(dates[0])]
            print(f'  当日资讯池为空，回退取 {dates[0]} 的 {len(items)} 条素材', flush=True)
    picked = []; seen = set()
    for it in items:
        d = domain_of(it['title'], it['summary'])
        if d in seen:
            continue
        seen.add(d); picked.append(it)
        if len(picked) >= limit:
            return picked
    for it in items:
        if len(picked) >= limit:
            break
        if it in picked:
            continue
        picked.append(it)
    if len(picked) < limit and os.path.exists(ARCHIVE_STORE):
        try:
            arch = json.load(open(ARCHIVE_STORE, encoding='utf-8'))
            extras = [p for p in arch.get('papers', {}).values()
                      if p.get('kind') != 'paper' and (p.get('date') or '') == today]
            extras.sort(key=lambda r: r.get('ts', ''), reverse=True)
            for p in extras:
                if len(picked) >= limit:
                    break
                d = domain_of(p.get('title', ''), p.get('summary', ''))
                if d in seen:
                    continue
                seen.add(d)
                picked.append({
                    'title': p.get('title', ''), 'summary': p.get('summary', ''),
                    'ts': p.get('ts', ''), 'url': p.get('link', ''), 'source': p.get('sourceShort', 'AI 资讯'),
                })
        except Exception:
            pass
    return picked[:limit]

def parse_json_object(text):
    t = strip_fence(text)
    cands = [t]
    s = t.find('{'); e = t.rfind('}')
    if s >= 0 and e > s:
        cands.append(t[s:e+1])
    for c in cands:
        for variant in (c, _repair_inner_quotes(c)):
            try:
                v = json.loads(variant)
                if isinstance(v, dict):
                    return v
            except Exception:
                pass
    m = re.search(r'"interpret"\s*:\s*"([\s\S]+?)"\s*\n?\s*}', t)
    if m:
        tm = re.search(r'"title"\s*:\s*"([\s\S]*?)"\s*,\s*"interpret"', t)
        return {'title': (tm.group(1).strip() if tm else ''), 'interpret': m.group(1).strip()}
    return {}

def gen_one_news(p):
    """逐条生成：单条输出短、易解析，一条失败不影响其余（批量数组对 4b 模型过脆）。"""
    prompt = (
        "你是面向普通读者的科技新闻编辑。为下面这条 AI 行业/产品新闻写一个吸引人的短标题 + 150-300字通俗解读。"
        "解读讲清三件事：这件事是什么、有什么影响、对普通人/从业者意味着什么。用简体中文，去术语化。\n"
        "只返回一个 JSON 对象（不要代码块、不要多余解释），字符串内不要出现英文双引号：\n"
        '{"title":"一句话新闻标题","interpret":"150-300字通俗解读"}\n'
        f"新闻标题：{p.get('title', '')}\n新闻简介：{(p.get('summary') or '')[:400]}"
    )
    out = ollama([{'role': 'system', 'content': '你擅长把科技新闻讲给普通人听，杜绝生僻术语。'},
                  {'role': 'user', 'content': prompt}])
    obj = parse_json_object(out)
    if not obj.get('interpret'):
        txt = re.sub(r'^[\s\S]*?"interpret"\s*:\s*"?', '', strip_fence(out)).strip().rstrip('"}').strip()
        if len(txt) >= 60:
            obj = {'title': obj.get('title', ''), 'interpret': txt}
    if not isinstance(obj, dict):
        return {}
    # 模型有时直接输出「标题：… / 解读：…」纯文本，清掉标签并回收标题
    t2, body = _split_plain(obj.get('interpret', '') or '')
    obj['interpret'] = body
    if t2 and not (obj.get('title') or '').strip():
        obj['title'] = t2
    return obj

def _split_plain(txt):
    title = ''
    m = re.search(r'^\s*(?:短?标题)\s*[:：]\s*(.+)$', txt, re.M)
    if m:
        title = m.group(1).strip().strip('"“”')
        txt = (txt[:m.start()] + '\n' + txt[m.end():])
    txt = re.sub(r'^\s*(?:通俗)?解读\s*[:：]\s*', '', txt.strip(), flags=re.M)
    return title, txt.strip()

def gen_news(today, material):
    if not material:
        return []
    res = []
    for i, p in enumerate(material):
        try:
            it = gen_one_news(p)
        except Exception as e:
            print(f'  第 {i+1} 条解读生成失败：{e}', flush=True)
            it = {}
        if not it.get('interpret'):
            print(f'  第 {i+1} 条解读为空，已跳过该条', flush=True)
            continue
        h = 0
        for ch in (p.get('source', '') or ''):
            h = (h * 31 + ord(ch)) & 0xffffffff
        res.append({
            'id': f'news-{today}-{i+1}',
            'domain': domain_of(p.get('title', ''), p.get('summary', '')),
            'title': it.get('title', '') or p.get('title', ''),
            'source': p.get('source', 'AI 资讯'),
            'sourceUrl': p.get('url', ''),
            'interpret': it.get('interpret', ''),
            'hue': 200 + (h % 30),
        })
    return res

def main():
    today = now_day()
    store = load_store()
    science = store.setdefault('science', {})
    news = store.setdefault('news', {})
    # 保留历史用于板块累积展示：科普近 30 天、新闻近 7 天（不再清空历史）。
    # 仓库导入桶（如 "repo-import"，非日期键）永久保留，不被日期裁剪误删。
    cut_sci = (datetime.strptime(today, '%Y-%m-%d') - timedelta(days=30)).strftime('%Y-%m-%d')
    cut_news = (datetime.strptime(today, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')
    _DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    for _d in list(science.keys()):
        if not _DATE_RE.match(str(_d)):
            continue  # 非日期桶（仓库导入）永久保留
        if _d < cut_sci:
            del science[_d]
    for _d in list(news.keys()):
        if not _DATE_RE.match(str(_d)):
            continue
        if _d < cut_news:
            del news[_d]
    have_sci = bool(science.get(today))
    have_news = bool(news.get(today))
    if have_sci and have_news:
        print(f'[{today}] 今日科普与解读均已存在，跳过生成。')
        return
    print(f'[{today}] 生成科普站内容（Ollama {MODEL}）...')
    # 预热
    ollama([{'role':'system','content':'warm'},{'role':'user','content':'hi'}])
    sci = [] if have_sci else gen_science(today)
    mat = []; nws = []
    if not have_news:
        seen_titles = {it.get('title', '') for day in news.values() for it in day}
        mat = pick_news_material(today, exclude_titles=seen_titles)
        try:
            nws = gen_news(today, mat)
        except Exception as e:
            print(f'  解读生成失败（保留已生成的科普）：{e}', flush=True)
    if sci: science[today] = sci
    if nws: news[today] = nws
    store['meta']['last_run'] = datetime.now(SH).isoformat()
    if 'first_run' not in store['meta']: store['meta']['first_run'] = store['meta']['last_run']
    save_store(store)
    print(f'  科普 {len(sci)} 条，解读 {len(nws)} 条（新闻素材 {len(mat)} 条）')

if __name__ == '__main__':
    main()
