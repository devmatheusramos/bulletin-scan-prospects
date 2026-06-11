"""
refinar_prospects.py
====================
Busca igrejas católicas em tempo real via Overpass (OpenStreetMap),
faz scraping dos sites para detectar bulletins e redes sociais,
e classifica cada lead com regras customizaveis.

Uso:
    Na raiz do projeto:
        docker build -t bulletin-refiner scraper/
        docker run --rm -v "%CD%:/project" bulletin-refiner

    Ou duplo clique em run.bat

Saidas:
    app/prospects_data.js   -> carregado pelo mapa_prospects.html automaticamente
    data/prospects_refined.csv -> CSV completo para analise
"""

import csv, json, re, time, sys
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ═══════════════════════════════════════════════════════════════
# DIOCESES — bbox [sul, oeste, norte, leste]
# Adicione ou remova dioceses livremente
# ═══════════════════════════════════════════════════════════════
DIOCESES = [
    {"name": "Archdiocese of Miami",             "bbox": [24.4, -81.9, 25.97, -80.0]},
    {"name": "Diocese of Fort Lauderdale",       "bbox": [25.97, -80.55, 26.38, -80.0]},
    {"name": "Diocese of Palm Beach",            "bbox": [26.38, -81.0, 27.7, -79.9]},
    {"name": "Diocese of Orlando",               "bbox": [27.7, -82.1, 29.5, -80.5]},
    {"name": "Diocese of Venice",                "bbox": [25.8, -82.5, 27.7, -80.5]},
    {"name": "Archdiocese of New York",          "bbox": [40.5, -74.3, 41.1, -73.7]},
    # Descomente para adicionar mais:
    # {"name": "Diocese of Rockville Centre",    "bbox": [40.5, -74.0, 41.1, -71.8]},
    # {"name": "Archdiocese of Boston",          "bbox": [42.0, -71.5, 42.7, -70.6]},
    # {"name": "Archdiocese of Chicago",         "bbox": [41.5, -88.3, 42.5, -87.5]},
    # {"name": "Archdiocese of Los Angeles",     "bbox": [33.5, -118.7, 34.8, -117.5]},
    # {"name": "Archdiocese of Sao Paulo",       "bbox": [-24.0, -47.0, -23.2, -46.2]},
]

# ═══════════════════════════════════════════════════════════════
# EXCLUSOES — igrejas que sao clientes Atimo (nao aparecem)
# ═══════════════════════════════════════════════════════════════
ATIMO_CLIENTES = [
    "st. katharine drexel", "immaculate conception", "san lazaro",
    "prince of peace", "st. matthew", "our lady of the lakes",
    "st. pius x", "st. timothy", "our lady of lourdes",
    "st. mary magdalen", "st. gregory", "st. martha",
    "st. john neumann", "saint louis", "our lady of guadalupe",
    "santuario nacional", "archbishop coleman",
    "sts. peter", "san isidro", "atimo",
]

# ═══════════════════════════════════════════════════════════════
# FILTROS GLOBAIS
# ═══════════════════════════════════════════════════════════════
EXIGIR_WEBSITE  = False   # True = remove igrejas sem site
EXIGIR_CONTATO  = False   # True = remove igrejas sem telefone E sem email
CHUNK_SIZE      = 1.2     # graus — tamanho de cada fatia da bbox
SCRAPE_WORKERS  = 6       # quantas igrejas scrapear em paralelo
SCRAPE_TIMEOUT  = 12      # segundos por site

# ═══════════════════════════════════════════════════════════════
# SCORING CUSTOMIZADO
# Cada regra: (descricao, funcao_condicao, pontos)
# ═══════════════════════════════════════════════════════════════
SCORING_RULES = [
    ("tem website",          lambda c: bool(c["website"]),                        1.0),
    ("tem email direto",     lambda c: bool(c["email"]),                          1.0),
    ("tem telefone",         lambda c: bool(c["phone"]),                          0.5),
    ("bulletin PDF no site", lambda c: c["bulletin_found"],                       3.0),
    ("tem Facebook",         lambda c: c["socials"]["facebook"],                  0.5),
    ("tem Instagram",        lambda c: c["socials"]["instagram"],                 0.5),
    ("tem YouTube",          lambda c: c["socials"]["youtube"],                   0.5),
    ("diocese FL",           lambda c: "florida" in c["diocese"].lower()
                                    or c["diocese"] in [
                                        "Archdiocese of Miami",
                                        "Diocese of Fort Lauderdale",
                                        "Diocese of Orlando",
                                        "Diocese of Venice",
                                        "Diocese of Palm Beach",
                                    ],                                            0.5),
    ("sem social nem email", lambda c: not c["socials"]["facebook"]
                                   and not c["socials"]["instagram"]
                                   and not c["email"],                           -0.5),
]
SCORE_MAX = 7.0

def classificar_lead(c):
    """Edite aqui os thresholds de hot/warm/cold."""
    s = c["score"]
    if c["bulletin_found"] and s >= 3.5:
        return "hot"
    if c["bulletin_found"] and s >= 2.0:
        return "warm"
    if s >= 2.5:
        return "warm"
    if s >= 1.0 and c["website"]:
        return "cold"
    return "none"

# ═══════════════════════════════════════════════════════════════
# HTTP SESSION
# ═══════════════════════════════════════════════════════════════
def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://",  HTTPAdapter(max_retries=retry))
    return s

SESSION = make_session()
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

# ═══════════════════════════════════════════════════════════════
# OVERPASS FETCH
# ═══════════════════════════════════════════════════════════════
def fetch_overpass(s, w, n, e, attempt=0):
    q = (
        f"[out:json][timeout:60];"
        f"("
        f'node["amenity"="place_of_worship"]["religion"="christian"]["denomination"="catholic"]({s},{w},{n},{e});'
        f'way["amenity"="place_of_worship"]["religion"="christian"]["denomination"="catholic"]({s},{w},{n},{e});'
        f'node["amenity"="place_of_worship"]["name"~"Catholic",i]({s},{w},{n},{e});'
        f'way["amenity"="place_of_worship"]["name"~"Catholic",i]({s},{w},{n},{e});'
        f");"
        f"out center tags;"
    )
    ep = OVERPASS_ENDPOINTS[attempt % len(OVERPASS_ENDPOINTS)]
    try:
        r = SESSION.post(ep, data={"data": q}, timeout=70)
        r.raise_for_status()
        return r.json().get("elements", [])
    except Exception as e:
        if attempt < 5:
            print(f"  ⚠ Overpass retry {attempt+1}: {e}")
            time.sleep(3 + attempt * 2)
            return fetch_overpass(s, w, n, e, attempt + 1)
        print(f"  ✗ Overpass falhou após {attempt+1} tentativas")
        return []

# ═══════════════════════════════════════════════════════════════
# DIOCESE DE UM PONTO
# ═══════════════════════════════════════════════════════════════
def get_diocese(lat, lng):
    for d in DIOCESES:
        s, w, n, e = d["bbox"]
        if s <= lat <= n and w <= lng <= e:
            return d["name"]
    return "Other"

# ═══════════════════════════════════════════════════════════════
# WEBSITE SCRAPING + BULLETIN DETECTION
# ═══════════════════════════════════════════════════════════════
BULLETIN_HREF_RE = re.compile(
    r'href=["\']([^"\']*(?:bulletin|boletim|boletin|weekly)[^"\']*\.(?:pdf|php)[^"\']*'
    r'|[^"\']*\d{8}[^"\']*\.pdf)["\']', re.I)
BULLETIN_PDF_RE  = re.compile(r'href=["\']([^"\']+\.pdf)["\']', re.I)
BULLETIN_KW_RE   = re.compile(r'bulletin|boletim|boletin|parish.?newsletter|weekly.?flyer', re.I)
FACEBOOK_RE      = re.compile(r'facebook\.com/(?!sharer|share|plugins|dialog)([a-zA-Z0-9_.]{3,})')
INSTAGRAM_RE     = re.compile(r'instagram\.com/([a-zA-Z0-9_.]{2,})')
YOUTUBE_RE       = re.compile(r'youtube\.com/(channel|c|@|user)/')
TWITTER_RE       = re.compile(r'(?:twitter|x)\.com/([a-zA-Z0-9_]{2,})')
EMAIL_RE         = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

def scrape_website(c):
    if not c["website"] or c.get("enriched"):
        return c
    try:
        r = SESSION.get(c["website"], timeout=SCRAPE_TIMEOUT, allow_redirects=True)
        h = r.text

        c["socials"]["facebook"]  = bool(FACEBOOK_RE.search(h))
        c["socials"]["instagram"] = bool(INSTAGRAM_RE.search(h))
        c["socials"]["youtube"]   = bool(YOUTUBE_RE.search(h))
        c["socials"]["twitter"]   = bool(TWITTER_RE.search(h))

        has_bulletin_href = bool(BULLETIN_HREF_RE.search(h))
        has_pdf           = bool(BULLETIN_PDF_RE.search(h))
        has_kw            = bool(BULLETIN_KW_RE.search(h))
        c["bulletin_found"] = has_bulletin_href or (has_pdf and has_kw)

        if c["bulletin_found"]:
            for m in BULLETIN_PDF_RE.finditer(h):
                if re.search(r'\d{6,8}|bulletin|boletim', m.group(1), re.I):
                    c["bulletin_url"] = urljoin(c["website"], m.group(1))
                    break

        if not c["email"]:
            emails = [e for e in EMAIL_RE.findall(h)
                      if not re.search(r'\.(png|jpg|gif|css|js)$|sentry|@2x|example', e, re.I)]
            if emails:
                c["email"] = emails[0]

    except Exception:
        pass
    c["enriched"] = True
    return c

# ═══════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════
def calcular_score(c):
    total, detail = 0.0, []
    for desc, fn, pts in SCORING_RULES:
        try:
            if fn(c):
                total += pts
                detail.append(f"+{pts} {desc}")
        except Exception:
            pass
    c["score"]        = round(min(max(total, 0), SCORE_MAX), 2)
    c["score_detail"] = detail
    c["lead"]         = classificar_lead(c)
    return c

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    seen   = set()
    churches = []

    print("=" * 60)
    print("BULLETIN SCAN — Scraper de Prospects")
    print(f"Data: {date.today()}  |  Dioceses: {len(DIOCESES)}")
    print("=" * 60)

    # ── PASSO 1: buscar todas as dioceses via Overpass ──────────
    for di, d in enumerate(DIOCESES):
        name = d["name"]
        s, w, n, e = d["bbox"]
        # dividir bbox em chunks para evitar timeout
        chunks = []
        la = s
        while la < n:
            lo = w
            while lo < e:
                chunks.append((la, lo, min(la + CHUNK_SIZE, n), min(lo + CHUNK_SIZE, e)))
                lo += CHUNK_SIZE
            la += CHUNK_SIZE

        print(f"\n[{di+1}/{len(DIOCESES)}] {name}  ({len(chunks)} chunks)")
        found = 0
        for ci, (cs, cw, cn, ce) in enumerate(chunks):
            sys.stdout.write(f"  chunk {ci+1}/{len(chunks)}... ")
            sys.stdout.flush()
            els = fetch_overpass(cs, cw, cn, ce)
            sys.stdout.write(f"{len(els)} elementos\n")

            for el in els:
                t    = el.get("tags", {})
                nome = (t.get("name") or t.get("name:en") or "").strip()
                if not nome or nome.lower() in seen:
                    continue
                if any(exc in nome.lower() for exc in ATIMO_CLIENTES):
                    continue

                lat = el.get("lat") or (el.get("center") or {}).get("lat")
                lng = el.get("lon") or (el.get("center") or {}).get("lon")
                if not lat or not lng:
                    continue

                seen.add(nome.lower())
                phone   = (t.get("phone") or t.get("contact:phone") or "").strip() or None
                website = (t.get("website") or t.get("contact:website") or t.get("url") or "").strip() or None
                email   = (t.get("email") or t.get("contact:email") or "").strip() or None
                addr    = ", ".join(filter(None, [
                    t.get("addr:housenumber"), t.get("addr:street"),
                    t.get("addr:city"), t.get("addr:state"), t.get("addr:postcode")
                ]))
                c = {
                    "name":          nome,
                    "diocese":       get_diocese(lat, lng) or name,
                    "address":       addr,
                    "phone":         phone,
                    "website":       website,
                    "email":         email,
                    "bulletin_found": False,
                    "bulletin_url":  None,
                    "socials":       {"facebook": False, "instagram": False, "youtube": False, "twitter": False},
                    "lat":           lat,
                    "lng":           lng,
                    "enriched":      False,
                    "score":         0,
                    "lead":          "none",
                    "score_detail":  [],
                }

                # filtros opcionais
                if EXIGIR_WEBSITE  and not website:  continue
                if EXIGIR_CONTATO  and not phone and not email:  continue

                churches.append(c)
                found += 1

            if ci < len(chunks) - 1:
                time.sleep(1.0)  # respeitar rate limit Overpass

        print(f"  → {found} igrejas novas encontradas")

    print(f"\nTotal coletado: {len(churches)} igrejas")

    # ── PASSO 2: scraping dos sites (paralelo) ──────────────────
    com_site = [c for c in churches if c["website"]]
    sem_site  = len(churches) - len(com_site)
    print(f"\nScraping de {len(com_site)} sites  ({sem_site} sem site)...")

    done = 0
    with ThreadPoolExecutor(max_workers=SCRAPE_WORKERS) as ex:
        futures = {ex.submit(scrape_website, c): c for c in com_site}
        for fut in as_completed(futures):
            done += 1
            c = futures[fut]
            try:
                fut.result()
            except Exception as e:
                pass
            if done % 20 == 0 or done == len(com_site):
                bull = sum(1 for x in churches if x["bulletin_found"])
                sys.stdout.write(f"\r  {done}/{len(com_site)} scrapeados  |  bulletins: {bull}   ")
                sys.stdout.flush()

    print()

    # ── PASSO 3: scoring e classificacao ────────────────────────
    for c in churches:
        calcular_score(c)

    counts = {"hot": 0, "warm": 0, "cold": 0, "none": 0}
    for c in churches:
        counts[c["lead"]] += 1

    print(f"\nRESULTADO FINAL:")
    print(f"  🔥 Hot:  {counts['hot']}")
    print(f"  🟡 Warm: {counts['warm']}")
    print(f"  🔵 Cold: {counts['cold']}")
    print(f"  ⚫ None: {counts['none']}")
    print(f"  Total acionaveis: {counts['hot']+counts['warm']+counts['cold']}")

    # ── PASSO 4: gerar JS para o mapa ───────────────────────────
    js_data = [{
        "name":          c["name"],
        "diocese":       c["diocese"],
        "lead":          c["lead"],
        "score":         c["score"],
        "bulletinFound": c["bulletin_found"],
        "bulletinUrl":   c["bulletin_url"],
        "address":       c["address"],
        "phone":         c["phone"],
        "email":         c["email"],
        "website":       c["website"],
        "socials":       c["socials"],
        "lat":           c["lat"],
        "lng":           c["lng"],
        "enriched":      True,
    } for c in churches]

    js = (
        f"// Gerado em {date.today()} | {len(js_data)} igrejas\n"
        f"// Hot:{counts['hot']} Warm:{counts['warm']} Cold:{counts['cold']}\n"
        f"var PRELOADED_DATA = {json.dumps(js_data, ensure_ascii=False, separators=(',',':'))};\n"
    )
    with open("/project/app/prospects_data.js", "w", encoding="utf-8") as f:
        f.write(js)
    print(f"\n✓ app/prospects_data.js gerado  ({len(js)//1024}KB)")

    # ── PASSO 5: gerar CSV refinado ─────────────────────────────
    lead_label = {"hot": "Hot Lead", "warm": "Warm Lead", "cold": "Cold Lead", "none": "Not Suitable"}
    cols = ["Name","Diocese","Lead","Score","Score Detail","Bulletin PDF","Bulletin URL",
            "Address","Phone","Email","Website","Facebook","Instagram","YouTube","Twitter","Lat","Lng"]
    with open("/project/data/prospects_refined.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for c in sorted(churches, key=lambda x: ({"hot":0,"warm":1,"cold":2,"none":3}[x["lead"]], -x["score"])):
            w.writerow([
                c["name"], c["diocese"], lead_label[c["lead"]], c["score"],
                " | ".join(c["score_detail"]),
                "Yes" if c["bulletin_found"] else "No",
                c["bulletin_url"] or "",
                c["address"], c["phone"] or "", c["email"] or "", c["website"] or "",
                "Yes" if c["socials"]["facebook"]  else "No",
                "Yes" if c["socials"]["instagram"] else "No",
                "Yes" if c["socials"]["youtube"]   else "No",
                "Yes" if c["socials"]["twitter"]   else "No",
                c["lat"], c["lng"],
            ])
    print(f"✓ prospects_refined.csv gerado")
    print(f"\nAbra mapa_prospects.html no browser para ver os dados atualizados.")

if __name__ == "__main__":
    main()
