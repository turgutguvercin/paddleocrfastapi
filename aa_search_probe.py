from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

OUT = Path("aa_search_output")
OUT.mkdir(exist_ok=True)
BASE = "https://www.aa.com.tr"
SEARCH = f"{BASE}/tr/enerjiterminali/search/"
QUERY = "Günlük elektrik üretim ve tüketim verileri"
S = requests.Session()
S.headers.update({"User-Agent":"Mozilla/5.0 Chrome/124 Safari/537.36","Accept-Language":"tr-TR,tr;q=0.9"})

variants=[]
for n in [1,2,3,5,10,20,30,40,50]:
    variants += [
        (f"s_page_{n}", {"s":QUERY,"page":n}),
        (f"s_p_{n}", {"s":QUERY,"p":n}),
        (f"s_sayfa_{n}", {"s":QUERY,"sayfa":n}),
        (f"s_paged_{n}", {"s":QUERY,"paged":n}),
    ]
variants.insert(0,("s_base",{"s":QUERY}))
report={}
for name,params in variants:
    try:
        r=S.get(SEARCH,params=params,timeout=90)
        r.raise_for_status()
        soup=BeautifulSoup(r.text,"html.parser")
        links=[]
        for a in soup.find_all("a",href=True):
            href=urljoin(r.url,a["href"])
            label=" ".join(a.get_text(" ",strip=True).split())
            if "gunluk-elektrik-uretim-ve-tuketim-verileri" in href:
                links.append({"href":href.split('?')[0],"label":label})
        tabs=[]
        for tab in soup.select('.newsfeedTab'):
            tabs.append({"tabcount":tab.get('tabcount'),"articles":len(tab.find_all('article'))})
        pagers=[]
        for el in soup.select('.PagerUl li'):
            pagers.append({"text":el.get_text(' ',strip=True),"linkcount":el.get('linkcount'),"class":el.get('class')})
        report[name]={
            "url":r.url,"status":r.status_code,"len":len(r.text),
            "title":soup.title.get_text(' ',strip=True) if soup.title else None,
            "daily_links":list({x['href']:x for x in links}.values()),
            "tabs":tabs,"pagers":pagers,
            "date_samples":re.findall(r"\b\d{2}\.\d{2}\.20\d{2}\b",soup.get_text(' ',strip=True))[:100],
            "forms":[{"action":f.get('action'),"method":f.get('method'),"inputs":[x.get('name') for x in f.find_all('input')]} for f in soup.find_all('form')],
        }
        (OUT/f"{name}.html").write_text(r.text,encoding='utf-8')
    except Exception as exc:
        report[name]={"error":repr(exc)}
(OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:{'url':v.get('url'),'len':v.get('len'),'daily':len(v.get('daily_links',[])),'tabs':v.get('tabs'),'pagers':v.get('pagers'),'dates':v.get('date_samples',[])[:4],'error':v.get('error')} for k,v in report.items()},ensure_ascii=False,indent=2))
