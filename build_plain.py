# -*- coding: utf-8 -*-
"""科普解读站构建脚本：读取 plain_store.json，渲染 index.html。"""
import json, os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, 'plain_store.json')
TEMPLATE = os.path.join(HERE, 'template.html')
OUT = os.path.join(HERE, 'index.html')
DOMAINS = ['AI认知算法', 'LLM', 'AI Agent', '世界模型', '虚拟仿真社会', '具身智能']
WEEK = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

def load_store():
    if os.path.exists(STORE):
        try:
            return json.load(open(STORE, encoding='utf-8'))
        except Exception:
            pass
    return {'meta': {}, 'science': {}, 'news': {}}

def main():
    store = load_store()
    science = store.get('science', {})
    news = store.get('news', {})

    sci_list, news_list = [], []
    for day, arr in science.items():
        for r in arr:
            r = dict(r); r['date'] = day
            try: r['weekday'] = WEEK[datetime.strptime(day, '%Y-%m-%d').weekday()]
            except Exception: r['weekday'] = ''
            sci_list.append(r)
    for day, arr in news.items():
        for r in arr:
            r = dict(r); r['date'] = day
            try: r['weekday'] = WEEK[datetime.strptime(day, '%Y-%m-%d').weekday()]
            except Exception: r['weekday'] = ''
            news_list.append(r)

    # 时间轴（日期并集，降序），count = 当日科普+解读
    from collections import defaultdict
    cnt = defaultdict(int)
    for r in sci_list: cnt[r['date']] += 1
    for r in news_list: cnt[r['date']] += 1
    timeline = []
    for day in sorted(cnt, reverse=True):
        timeline.append({'date': day,
                         'weekday': (sci_list+news_list and
                             next((r['weekday'] for r in (sci_list+news_list) if r['date']==day), '')),
                         'count': cnt[day]})

    all_dates = sorted(cnt, reverse=True)
    payload = {
        'science': sci_list, 'news': news_list, 'timeline': timeline,
        'domains': DOMAINS,
        'first_date': all_dates[-1] if all_dates else '',
        'last_date': all_dates[0] if all_dates else '',
        'days': len(all_dates),
        'science_count': len(sci_list), 'news_count': len(news_list),
    }
    html = open(TEMPLATE, encoding='utf-8').read()
    html = html.replace('/*__DATA__*/', json.dumps(payload, ensure_ascii=False))
    open(OUT, 'w', encoding='utf-8').write(html)
    print(f'Built 科普站：{len(sci_list)} 条科普 / {len(news_list)} 篇解读，覆盖 {len(all_dates)} 天（{payload["first_date"]} -> {payload["last_date"]}）')

if __name__ == '__main__':
    main()
