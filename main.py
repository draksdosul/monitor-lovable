"""
Backend - Monitor Lovable Ads (v4)
API hospedada no Railway

Detecção de anúncios via UTM/fbclid no URLScan
em vez da API do Facebook (que exige verificação da Meta).
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import time
from urllib.parse import urlparse
import os

app = Flask(__name__)
CORS(app)

URLSCAN_API_KEY = os.environ.get("URLSCAN_API_KEY", "")

# Domínios padrão de plataformas para ignorar
DOMINIOS_PLATAFORMA = [
    "lovable.app",
    "lovableproject.com",
    "lovable.dev",
    "vercel.app",
    "netlify.app",
    "github.io",
    "pages.dev",
    "web.app",
    "firebaseapp.com",
    "herokuapp.com",
    "railway.app",
    "render.com",
]

# Parâmetros que indicam tráfego pago do Facebook
FB_PARAMS = ["fbclid", "utm_source", "utm_campaign", "utm_medium"]

LIMITE_POR_PAGINA = 15


def extrair_dominio(url: str) -> str:
    try:
        parsed = urlparse(url if "://" in url else "https://" + url)
        return parsed.netloc.replace("www.", "")
    except:
        return ""


def dominio_e_plataforma(dominio: str) -> bool:
    for plataforma in DOMINIOS_PLATAFORMA:
        if dominio.endswith(plataforma):
            return True
    return False


def extrair_dominio_da_query(query: str) -> str:
    """
    Extrai o domínio da query SOMENTE se a query for uma busca por domínio.
    Queries com operadores como filename:, hash:, etc. retornam vazio (não filtra).
    """
    operadores = ["filename:", "hash:", "ip:", "asn:", "tag:", "page.title:", "page.status:", "page.url:"]
    for op in operadores:
        if op in query.lower():
            return ""

    import re
    query_limpa = query.strip().lower()

    match = re.search(r"page\.domain:([^\s]+)", query_limpa)
    if match:
        return match.group(1)

    if "." in query_limpa and " " not in query_limpa:
        return extrair_dominio(query_limpa) or query_limpa

    return ""


def buscar_urlscan(query: str, search_after: str = None, tamanho: int = 50) -> dict:
    """Busca no URLScan com a query fornecida."""
    url = "https://urlscan.io/api/v1/search/"
    headers = {"Content-Type": "application/json"}
    if URLSCAN_API_KEY:
        headers["API-Key"] = URLSCAN_API_KEY

    params = {"q": query, "size": tamanho}
    if search_after:
        params["search_after"] = search_after

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return {
            "results": data.get("results", []),
            "total": data.get("total", 0),
        }
    except Exception as e:
        return {"results": [], "total": 0, "erro": str(e)}


def verificar_anuncio_facebook(dominio: str) -> dict:
    """
    Verifica se o domínio tem registros no URLScan com parâmetros
    de tráfego pago do Facebook (fbclid, utm_source, utm_campaign).
    Faz duas queries para maximizar a detecção.
    """

    # Query 1: fbclid (mais específico — quase certeza de clique em anúncio)
    query_fbclid = f"page.domain:{dominio} page.url:fbclid"
    dados_fbclid = buscar_urlscan(query_fbclid, tamanho=3)

    if dados_fbclid.get("results"):
        r = dados_fbclid["results"][0]
        return {
            "anunciando": True,
            "indicador": "fbclid",
            "total_registros": dados_fbclid.get("total", 1),
            "ultimo_scan": r.get("task", {}).get("time", ""),
            "url_exemplo": r.get("page", {}).get("url", ""),
            "link_biblioteca": f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=BR&q={dominio}",
        }

    # Query 2: utm_source com facebook (também forte indicador)
    query_utm = f"page.domain:{dominio} page.url:utm_source"
    dados_utm = buscar_urlscan(query_utm, tamanho=5)

    if dados_utm.get("results"):
        # Verifica se algum resultado tem utm_source relacionado ao Facebook
        for r in dados_utm["results"]:
            url_pagina = r.get("page", {}).get("url", "").lower()
            if any(fb in url_pagina for fb in ["facebook", "fb&", "fb=", "=fb", "utm_source=fb"]):
                return {
                    "anunciando": True,
                    "indicador": "utm_facebook",
                    "total_registros": dados_utm.get("total", 1),
                    "ultimo_scan": r.get("task", {}).get("time", ""),
                    "url_exemplo": r.get("page", {}).get("url", ""),
                    "link_biblioteca": f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=BR&q={dominio}",
                }

        # Tem UTMs mas não confirmou Facebook — marca como "possível"
        r = dados_utm["results"][0]
        return {
            "anunciando": None,  # None = possível, não confirmado
            "indicador": "utm_generico",
            "total_registros": dados_utm.get("total", 1),
            "ultimo_scan": r.get("task", {}).get("time", ""),
            "url_exemplo": r.get("page", {}).get("url", ""),
            "link_biblioteca": f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=BR&q={dominio}",
        }

    return {"anunciando": False}


def processar_resultados(resultados_urlscan: list, query: str) -> tuple:
    vistos = set()
    filtrados = []
    ultimo_sort = None

    dominio_query = extrair_dominio_da_query(query)

    for r in resultados_urlscan:
        page = r.get("page", {})
        url = page.get("url", "")
        dominio = extrair_dominio(url)

        if not dominio:
            continue
        if dominio in vistos:
            continue
        if dominio_e_plataforma(dominio):
            continue
        if dominio_query and dominio_query in dominio.lower():
            continue

        vistos.add(dominio)
        ultimo_sort = r.get("sort", [])
        filtrados.append({
            "dominio": dominio,
            "url": url,
            "pais": page.get("country", ""),
            "data_scan": r.get("task", {}).get("time", ""),
            "urlscan_link": f"https://urlscan.io/result/{r.get('_id', '')}/",
        })

        if len(filtrados) >= LIMITE_POR_PAGINA:
            break

    # Verifica anúncios para cada domínio
    resultados_finais = []
    for site in filtrados:
        fb = verificar_anuncio_facebook(site["dominio"])
        resultados_finais.append({**site, **fb})
        time.sleep(0.3)  # Respeita rate limit do URLScan

    proximo_cursor = ",".join(str(s) for s in ultimo_sort) if ultimo_sort else None
    return resultados_finais, proximo_cursor


@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "versao": "v4",
        "mensagem": "Monitor Lovable Ads - API rodando!",
        "deteccao": "URLScan UTM/fbclid"
    })


@app.route("/buscar", methods=["GET"])
def buscar():
    """
    Parâmetros:
      - q: query de busca (ex: 'filename:gptengineer')
      - search_after: cursor para paginação (opcional)
    """
    query = request.args.get("q", "").strip()
    search_after = request.args.get("search_after", None)

    if not query:
        return jsonify({"erro": "Parâmetro 'q' é obrigatório"}), 400

    dados_urlscan = buscar_urlscan(query, search_after)
    resultados_brutos = dados_urlscan.get("results", [])

    if not resultados_brutos:
        return jsonify({
            "query": query,
            "total_urlscan": 0,
            "total_retornados": 0,
            "total_anunciando": 0,
            "resultados": [],
            "proximo_cursor": None,
        })

    resultados, proximo_cursor = processar_resultados(resultados_brutos, query)
    anunciando = [r for r in resultados if r.get("anunciando") is True]
    possiveis = [r for r in resultados if r.get("anunciando") is None]

    return jsonify({
        "query": query,
        "total_urlscan": dados_urlscan.get("total", 0),
        "total_retornados": len(resultados),
        "total_anunciando": len(anunciando),
        "total_possiveis": len(possiveis),
        "resultados": resultados,
        "proximo_cursor": proximo_cursor,
    })


@app.route("/checar", methods=["POST"])
def checar_url():
    """Checa um domínio específico informado pelo usuário."""
    data = request.json or {}
    url = data.get("url", "")
    dominio = extrair_dominio(url) if "/" in url else url.strip()
    fb = verificar_anuncio_facebook(dominio)
    return jsonify({"dominio": dominio, **fb})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
