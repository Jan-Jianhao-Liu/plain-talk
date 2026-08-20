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

SCIENCE_TOPICS = [
    "什么是 AI Agent？（区别于普通对话大模型，能自主规划、记忆、调用工具完成长任务）",
    "Agent 三大核心模块：记忆、规划、工具调用",
]

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

def parse_json_array(text):
    text = strip_fence(text)
    try: return json.loads(text)
    except Exception:
        s = text.find('['); e = text.rfind(']')
        if s>=0 and e>0:
            try: return json.loads(text[s:e+1])
            except Exception: return []
    return []

def gen_science(today):
    prompt = (
        "你是面向零基础入门学习者的 AI 科普作者。请严格按顺序生成以下 %d 个「AI Agent 入门科普」条目，"
        "用简体中文，返回 JSON 数组（不要代码块、不要多余解释），每个元素格式：\n"
        '{"title":"【入门科普】xxx","level":"入门","body":"200-400字，少公式多比喻，用生活化例子讲清概念","diagram":"一句话示意图说明，没有则空字符串"}\n'
        "条目主题依次为：\n" + "\n".join(f"{i+1}. {t}" for i,t in enumerate(SCIENCE_TOPICS))
    )
    msgs = [{'role':'system','content':'你擅长用通俗比喻讲解 AI 概念，杜绝生僻术语。'},
            {'role':'user','content':prompt}]
    out = ollama(msgs)
    arr = parse_json_array(out)
    res = []
    for i, it in enumerate(arr[:len(SCIENCE_TOPICS)]):
        if not isinstance(it, dict): continue
        res.append({'id': f'sci-{today}-{i+1}', 'domain': 'AI Agent',
                    'title': it.get('title','') or f'【入门科普】{SCIENCE_TOPICS[i][:12]}',
                    'level': it.get('level','入门'), 'body': it.get('body',''),
                    'diagram': it.get('diagram',''), 'link': ''})
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

def pick_news_material(today, limit=5):
    """每日 5 条大众新闻，**严格当日**（publishedAt 必须等于 today）；当日不足则少放，不凑旧闻。
    优先 AI HOT 资讯池（跨领域去重），不足补档案库当日社媒/资讯。"""
    items = [it for it in fetch_aihot_news(60) if (it.get('ts') or '').startswith(today)]
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

def gen_news(today, material):
    if not material:
        return []
    items = []
    for i, p in enumerate(material, 1):
        items.append(f"{i}. 标题：{p.get('title', '')}\n   简介：{(p.get('summary') or '')[:260]}")
    prompt = (
        "你是面向普通读者的科技新闻编辑。对下面每一条 AI 行业/产品新闻，写一个吸引人的短标题 + 150-300字通俗解读，"
        "解读讲清三件事：这件事是什么、有什么影响、对普通人/从业者意味着什么。用简体中文，去术语化。\n"
        "返回 JSON 数组（不要代码块、不要多余解释），每个元素：\n"
        '{"title":"一句话新闻标题","interpret":"150-300字通俗解读"}\n'
        "严格按输入顺序，每条对应一个元素。\n新闻列表：\n" + "\n".join(items)
    )
    msgs = [{'role': 'system', 'content': '你擅长把科技新闻讲给普通人听，杜绝生僻术语。'},
            {'role': 'user', 'content': prompt}]
    out = ollama(msgs)
    arr = parse_json_array(out)
    res = []
    for i, p in enumerate(material):
        it = arr[i] if i < len(arr) else {}
        if not isinstance(it, dict):
            it = {}
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
    if science.get(today) and news.get(today):
        print(f'[{today}] 内容已存在，跳过生成。')
        return
    print(f'[{today}] 生成科普站内容（Ollama {MODEL}）...')
    # 预热
    ollama([{'role':'system','content':'warm'},{'role':'user','content':'hi'}])
    sci = gen_science(today)
    mat = pick_news_material(today)
    nws = []
    try:
        nws = gen_news(today, mat)
    except Exception as e:
        print(f'  解读生成失败（保留已生成的科普）：{e}', flush=True)
    if sci: science[today] = sci
    if nws: news[today] = nws
    store['meta']['last_run'] = datetime.now(SH).isoformat()
    if 'first_run' not in store['meta']: store['meta']['first_run'] = store['meta']['last_run']
    save_store(store)
    print(f'  科普 {len(sci)} 条，解读 {len(nws)} 条（联动档案库 {len(mat)} 篇论文）')

if __name__ == '__main__':
    main()
