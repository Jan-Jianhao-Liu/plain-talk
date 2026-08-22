# -*- coding: utf-8 -*-
"""
科普站公开数据导出：从 plain_store.json 生成 data.json（供小程序 wx.request 直连）。
字段与小程序 science/news 集合一致，并按日期倒序展平。
站点的 index.html 内联数据仅供网页端；data.json 是给小程序的稳定 JSON 接口。
"""
import json, os, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, 'plain_store.json')
OUT = os.path.join(HERE, 'data.json')


def flatten(store, key):
    out = []
    for day in sorted(store.get(key, {}).keys(), reverse=True):
        for it in store[key][day]:
            it2 = dict(it)
            it2['date'] = day
            out.append(it2)
    return out


def main():
    store = json.load(open(STORE, encoding='utf-8'))
    science = flatten(store, 'science')
    news = flatten(store, 'news')
    out = {
        'updated': datetime.datetime.now().isoformat(),
        'science': science,
        'news': news,
    }
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'wrote data.json: science={len(science)} news={len(news)}')


if __name__ == '__main__':
    main()
