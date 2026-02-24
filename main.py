"""
Backend - Monitor Lovable Ads (v3)
API hospedada no Railway
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import time
from urllib.parse import urlparse
import os
import re

app = Flask(__name__)
CORS(app)

FB_ACCESS_TOKEN = os.environ.get("FB_ACCESS_TOKEN", "")
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
    Exemplos:
      - "utmify.com.br"           → "utmify.com.br"
      - "page.domain:lovable.app" → "lovable.app"
      - "filename:gptengineer"    → "" (não é domínio, não filtra)
    """
    # Se a query tem operadores do URLScan (filename:, hash:, etc.), não é um domínio
    operadores = ["filename:", "hash:", "ip:", "asn:", "tag:", "page.title:", "page.status:"]
    for op in operadores:
        if op in query.lower():
            return ""  # Não filtra por domínio

    # Se parece um domínio (tem ponto e não tem espaços)
    query_limpa = query.strip().lower()
    if "." in query_limpa and " " not in query_limpa:
        # Extrai o domínio do operador page.domain: se presente
        match = re.search(r"page\.domain:([^\s]+)", query_limpa)
        if match:
            return match.group(1)
        return extrair_dominio(query_limpa) or query_limpa

    return ""


def buscar_urlscan(query: str, search_after: str = None) -> dict:
    url = "https://urlscan.io/api/v1/search/"
    headers = {"Content-Type": "application/json"}
    if URLSCAN_API_KEY:
        headers["API-Key"] = URLSCAN_API_KEY

    params = {
        "q": query,
        "size": 50,
    }

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
    if not FB_ACCESS_TOKEN:
        return {"anunciando": False, "erro": "Token do Facebook não configurado"}

    url = "https://graph.facebook.com/v19.0/ads_archive"
    params = {
        "access_token": FB_ACCESS_TOKEN,
        "ad_reached_countries": "BR",
        "search_terms": dominio,
        "ad_active_status": "ACTIVE",
        "fields": "id,ad_delivery_start_time,page_name,page_id",
        "limit": 5,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        ads = data.get("data", [])
        if ads:
            return {
                "anunciando": True,
                "total_anuncios": len(ads),
                "pagina_fb": ads[0].get("page_name", ""),
                "inicio_veiculacao": ads[0].get("ad_delivery_start_time", ""),
                "link_biblioteca": f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=BR&q={dominio}",
            }
        return {"anunciando": False}
    except Exception as e:
        return {"anunciando": False, "erro": str(e)}


def processar_resultados(resultados_urlscan: list, query: str) -> tuple:
    vistos = set()
    filtrados = []
    ultimo_sort = None

    # Extrai domínio da query para filtrar (só se for busca por domínio)
    dominio_query = extrair_dominio_da_query(query)

    for r in resultados_urlscan:
        page = r.get("page", {})
        url = page.get("url", "")
        dominio = extrair_dominio(url)

        if not dominio:
            continue

        # Ignora duplicatas
        if dominio in vistos:
            continue

        # Ignora domínios de plataformas padrão
        if dominio_e_plataforma(dominio):
            continue

        # Ignora domínio igual ao pesquisado (só quando a query é um domínio)
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

    # Verifica Facebook para cada domínio
    resultados_finais = []
    for site in filtrados:
        fb = verificar_anuncio_facebook(site["dominio"])
        resultados_finais.append({**site, **fb})
        time.sleep(0.3)

    proximo_cursor = ",".join(str(s) for s in ultimo_sort) if ultimo_sort else None
    return resultados_finais, proximo_cursor


@app.route("/")
def home():
    return jsonify({"status": "online", "versao": "v3", "mensagem": "Monitor Lovable Ads - API rodando!"})


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
    anunciando = [r for r in resultados if r.get("anunciando")]

    return jsonify({
        "query": query,
        "total_urlscan": dados_urlscan.get("total", 0),
        "total_retornados": len(resultados),
        "total_anunciando": len(anunciando),
        "resultados": resultados,
        "proximo_cursor": proximo_cursor,
    })


@app.route("/checar", methods=["POST"])
def checar_url():
    data = request.json or {}
    url = data.get("url", "")
    dominio = extrair_dominio(url) if "/" in url else url.strip()
    fb = verificar_anuncio_facebook(dominio)
    return jsonify({"dominio": dominio, **fb})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
