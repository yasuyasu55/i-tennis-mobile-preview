"""Read-only live PDF diagnostics; no tournament JSON writes."""
import sys, json, hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"pipeline"),str(ROOT/"data-source")]
from fetcher import PoliteFetcher, FetchError
from pdftext import pdf_to_text, PdfError
from parsers import okazaki_tennis as ok, hamamatsu_tennis as hm
f=PoliteFetcher("I-Tennis-Mobile-DataBot/1.0 (+https://github.com/yasuyasu55/i-tennis-mobile-preview)",delay=3,timeout=20,max_requests=16)
result={"sources":[]}
for key,url,parser in [("okazaki",ok.PAGE_URL,ok.parse_page),("hamamatsu",hm.PAGE_URL,hm.parse_tournament_page)]:
    out={"source":key,"pdfs":[]}
    try:
        page=f.get(url,3000000)
        events=parser(page.body)["events"]
        selected=[]
        for e in events:
            u=e.get("guideline_url")
            if not u or u in [x["url"] for x in selected]: continue
            # Hamamatsu: only the unresolved multi-event sports-festival guideline.
            if key=="hamamatsu" and "shispo" not in u: continue
            # Okazaki: upcoming tournaments first; inspect up to four guideline PDFs.
            if key=="okazaki":
                ends=[p["normalized"] for p in e["periods"] if p.get("normalized")]
                if ends and max(ends)<"2026-10-10": continue
            selected.append({"title":e["title"],"url":u,"periods":e["periods"]})
        for item in selected[:4]:
            try:
                pdf=f.get(item["url"],4000000)
                txt=pdf_to_text(pdf.body)
                item.update(status=pdf.status,sha256=hashlib.sha256(pdf.body).hexdigest(),extractor=pdf_to_text.last_extractor,text=txt[:24000])
            except (FetchError,PdfError) as ex: item["error"]=str(ex)
            out["pdfs"].append(item)
    except FetchError as ex: out["error"]=str(ex)
    result["sources"].append(out)
result["requests"]=f.log
Path("details_out").mkdir(exist_ok=True)
Path("details_out/pdf-diagnostics.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(result,ensure_ascii=False,indent=2))
