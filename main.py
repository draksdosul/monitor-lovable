"""
Backend - Monitor Lovable Ads
API que será hospedada no Railway
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import time
from urllib.parse import urlparse
import os

app = Flask(__name__)
CORS(app)  # Permite que o frontend do Lovable acesse esta API

# Pega as variáveis de ambiente configuradas no Railway
FB_ACCESS_TOKEN = os.environ.get("FB_ACCESS_TOKEN", "")
URLSCAN_API_KEY = os.environ.get("URLSCAN_API_KEY", "")

URLSCAN_QUERIES = [
    "page.domain:lovable.app",
    "page.domain:lovableproject.com",
    'filename:gptengineer',
]

def extrair_dominio(url):
    try:
        parsed = urlparse(url)
        return parsed.netloc.replace("www.", "")
    except:
        return url

def buscar_sites_urlscan(query):
    url = "https://urlscan.io/api/v1/search/"
    headers = {"Content-Type": "application/json"}
    if URLSCAN_API_KEY:
        headers["API-Key"] = URLSCAN_API_KEY

    params = {"q": query, "size": 50}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except:
        return []

def verificar_anuncio_facebook(dominio):
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
        resp = requests.get(url, params=params, timeout=15)
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


@app.route("/")
def home():
    return jsonify({"status": "online", "mensagem": "Monitor Lovable Ads - API rodando!"})


@app.route("/buscar", methods=["GET"])
def buscar():
    """Endpoint principal: busca sites Lovable e verifica anúncios no Brasil"""
    
    todos = {}
    for query in URLSCAN_QUERIES:
        resultados = buscar_sites_urlscan(query)
        for r in resultados:
            page = r.get("page", {})
            url = page.get("url", "")
            dominio = extrair_dominio(url)
            if dominio and dominio not in todos:
                todos[dominio] = {
                    "dominio": dominio,
                    "url": url,
                    "pais": page.get("country", ""),
                    "data_scan": r.get("task", {}).get("time", ""),
                    "urlscan_link": f"https://urlscan.io/result/{r.get('_id', '')}/",
                }
        time.sleep(1)

    sites = list(todos.values())
    resultados_finais = []

    for site in sites:
        dominio = site["dominio"]
        fb = verificar_anuncio_facebook(dominio)
        resultados_finais.append({**site, **fb})
        time.sleep(0.5)

    anunciando = [r for r in resultados_finais if r.get("anunciando")]

    return jsonify({
        "total_encontrados": len(sites),
        "total_anunciando": len(anunciando),
        "resultados": resultados_finais,
        "anunciando": anunciando,
    })


@app.route("/checar", methods=["POST"])
def checar_url():
    """Checa uma URL específica informada pelo usuário"""
    data = request.json
    url = data.get("url", "")
    dominio = extrair_dominio(url)
    fb = verificar_anuncio_facebook(dominio)
    return jsonify({"dominio": dominio, **fb})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
