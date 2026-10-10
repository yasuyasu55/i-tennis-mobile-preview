"""Read-only investigation. Never updates tournament records."""
import json, sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"pipeline"))
from fetcher import PoliteFetcher, FetchError
f=PoliteFetcher("I-Tennis-Mobile-DataBot/1.0 (+https://github.com/yasuyasu55/i-tennis-mobile-preview)",delay=3,timeout=25,max_requests=12)
report={"source":"新城テニス協会","pages":[],"requests":[]}
for url in ["https://shinshiro-tennis.com/","https://shinshiro-tennis.com/e1382200.html","https://shinshiro-tennis.com/e1376566.html","https://shinshiro-tennis.com/e1383946.html"]:
    item={"url":url}
    try:
        result=f.get(url,3000000)
        soup=BeautifulSoup(result.body,"html.parser")
        item.update(status=result.status,content_type=result.content_type,bytes=len(result.body),title=soup.title.get_text(" ",strip=True) if soup.title else "")
        content=soup.select_one(".article") or soup.select_one(".entry") or soup.select_one("#content") or soup.body or soup
        for el in content.select("script,style"): el.decompose()
        item["text"]=content.get_text("\n",strip=True)[:18000]
        links=[]
        for a in soup.select("a[href]"):
            dest=urljoin(url,a["href"])
            label=a.get_text(" ",strip=True)
            if dest.lower().endswith(".pdf") or any(w in label for w in ("年間","大会","要項","予定","2026")):
                links.append({"label":label,"url":dest})
        item["links"]=links[:80]
    except FetchError as e:
        item.update(error_stage=e.stage,error=str(e),status=e.status)
    report["pages"].append(item)
report["requests"]=f.log
Path("probe_out").mkdir(exist_ok=True)
Path("probe_out/shinshiro.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(report,ensure_ascii=False,indent=2))
