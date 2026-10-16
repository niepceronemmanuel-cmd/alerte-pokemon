#!/usr/bin/env python3
"""
Surveillance de stock Pokémon 30 ans.

Pour chaque enseigne de config.yaml :
  1. ouvre la page de recherche,
  2. repère les liens produits qui correspondent au filtre (Pokémon + 30 ans),
  3. ouvre chaque fiche produit et lit sa disponibilité,
  4. envoie une notification (téléphone via ntfy et/ou email) quand :
     - un produit passe en stock (ou en précommande),
     - un nouveau produit 30 ans apparaît dans la recherche.

Usage :
  python stock_watcher.py            -> une vérification
  python stock_watcher.py --test     -> envoie une notification de test
  python stock_watcher.py --boucle 3 -> vérifie toutes les 3 minutes (sur ton PC)
"""
import argparse
import json
import os
import random
import re
import smtplib
import sys
import time
import unicodedata
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.yaml"
STATE_FILE = ROOT / "state.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Valeurs schema.org (normalisées, sans espaces)
DISPO = ("instock", "preorder", "presale", "limitedavailability", "onlineonly")
RUPTURE = ("outofstock", "soldout", "discontinued", "instoreonly", "backorder")

TEXTES_RUPTURE = ["rupture de stock", "indisponible", "epuise", "plus disponible",
                  "non disponible", "victime de son succes", "bientot disponible",
                  "currently unavailable", "actuellement indisponible"]
TEXTES_DISPO = ["ajouter au panier", "add to basket", "add to cart", "precommander"]

MARQUEURS_BLOCAGE = ["captcha", "datadome", "robot check", "access denied",
                     "acces refuse", "are you a robot", "verify you are human",
                     "cf-chl", "validatecaptcha", "pardon our interruption"]


# ---------------------------------------------------------------- utilitaires
def norm(s):
    """minuscules, sans accents, espaces simplifiés (30ᵉ -> 30e, Pokémon -> pokemon)"""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).lower().strip()


def clean_url(url):
    """URL sans paramètres de suivi, pour éviter les doublons."""
    parts = urlsplit(url)
    path = parts.path
    asin = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", path)
    if asin:  # Amazon : /dp/ASIN suffit
        path = f"/dp/{asin.group(1)}"
    return urlunsplit((parts.scheme, parts.netloc.lower(), path.rstrip("/"), "", ""))


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ---------------------------------------------------------------- téléchargement
_pw = None
_browser = None


def fetch(url, js=False):
    """Renvoie (code HTTP, html). js=True passe par un vrai navigateur (Playwright)."""
    if js:
        return fetch_js(url)
    r = requests.get(url, headers=HEADERS, timeout=30)
    return r.status_code, r.text


def fetch_js(url):
    global _pw, _browser
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch(headless=True)
    ctx = _browser.new_context(user_agent=UA, locale="fr-FR",
                               viewport={"width": 1366, "height": 900})
    page = ctx.new_page()
    try:
        resp = page.goto(url, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)  # laisse le JavaScript afficher les produits
        return (resp.status if resp else 0), page.content()
    finally:
        ctx.close()


def close_browser():
    global _pw, _browser
    if _browser:
        _browser.close()
        _pw.stop()
        _browser = _pw = None


def est_bloque(status, html):
    if status in (401, 403, 429, 503):
        return True
    bas = norm(html[:60000])
    # les marqueurs ne comptent que sur une page courte (une vraie page produit est longue)
    return len(html) < 40000 and any(m in bas for m in MARQUEURS_BLOCAGE)


# ---------------------------------------------------------------- disponibilité
def dispo_schema(soup):
    """Lit la disponibilité dans les données structurées schema.org de la page."""
    valeurs = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "null")
        except Exception:
            continue
        pile = [data]
        while pile:
            x = pile.pop()
            if isinstance(x, dict):
                for k, v in x.items():
                    if k.lower() == "availability" and isinstance(v, str):
                        valeurs.append(v)
                    else:
                        pile.append(v)
            elif isinstance(x, list):
                pile.extend(x)
    for t in soup.select('[itemprop="availability"]'):
        valeurs.append(t.get("href") or t.get("content") or t.get_text())

    vals = [norm(v).replace(" ", "") for v in valeurs]
    if any(d in v for v in vals for d in DISPO):
        return "dispo"
    if any(r in v for v in vals for r in RUPTURE):
        return "rupture"
    return None


def dispo_texte(soup, textes_dispo=None, textes_rupture=None):
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    texte = norm(soup.get_text(" "))
    if any(norm(t) in texte for t in (textes_rupture or TEXTES_RUPTURE)):
        return "rupture"
    if any(norm(t) in texte for t in (textes_dispo or TEXTES_DISPO)):
        return "dispo"
    return None


def verifier_produit(url, js=False, textes_dispo=None, textes_rupture=None):
    """Renvoie 'dispo', 'rupture', 'bloque' ou None (inconnu)."""
    status, html = fetch(url, js)
    if est_bloque(status, html):
        return "bloque"
    if status >= 400:
        return None
    soup = BeautifulSoup(html, "html.parser")
    if textes_dispo or textes_rupture:  # textes perso prioritaires
        return dispo_texte(soup, textes_dispo, textes_rupture)
    return dispo_schema(soup) or dispo_texte(soup)


# ---------------------------------------------------------------- recherche
def correspond(texte, filtre):
    t = norm(texte)
    if any(norm(x) in t for x in filtre.get("exclure", [])):
        return False
    if not all(norm(x) in t for x in filtre.get("doit_contenir", [])):
        return False
    un_de = filtre.get("un_de", [])
    return not un_de or any(norm(x) in t for x in un_de)


def liens_produits(html, base_url, filtre):
    """{url: titre} des liens de la page qui correspondent au filtre."""
    soup = BeautifulSoup(html, "html.parser")
    domaine = urlsplit(base_url).netloc.lower().removeprefix("www.")
    textes = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        url = clean_url(urljoin(base_url, href))
        if domaine not in urlsplit(url).netloc:
            continue
        morceaux = [a.get_text(" "), a.get("title", ""), a.get("aria-label", "")]
        morceaux += [img.get("alt", "") for img in a.find_all("img")]
        textes.setdefault(url, []).extend(m for m in morceaux if m and m.strip())
    resultat = {}
    for url, morceaux in textes.items():
        tout = " ".join(morceaux)
        if correspond(tout, filtre):
            titre = max(morceaux, key=len).strip()
            resultat[url] = re.sub(r"\s+", " ", titre)[:120]
    return resultat


# ---------------------------------------------------------------- notifications
def notifier(titre, message, url=None, urgent=True):
    log(f"🔔 {titre} | {message} | {url or ''}")
    envoye = False

    topic = os.getenv("NTFY_TOPIC")
    if topic:
        try:
            payload = {"topic": topic, "title": titre, "message": message,
                       "priority": 5 if urgent else 3,
                       "tags": ["rotating_light"] if urgent else ["information_source"]}
            if url:
                payload["click"] = url
                payload["actions"] = [{"action": "view", "label": "Ouvrir", "url": url}]
            serveur = os.getenv("NTFY_SERVER", "https://ntfy.sh")
            requests.post(serveur, json=payload, timeout=15).raise_for_status()
            envoye = True
        except Exception as e:
            log(f"   ⚠️ ntfy a échoué : {e}")

    if os.getenv("EMAIL_USER") and os.getenv("EMAIL_PASSWORD"):
        try:
            msg = EmailMessage()
            msg["Subject"] = titre
            msg["From"] = os.getenv("EMAIL_USER")
            msg["To"] = os.getenv("EMAIL_TO") or os.getenv("EMAIL_USER")
            msg.set_content(f"{message}\n\n{url or ''}")
            hote = os.getenv("EMAIL_SMTP", "smtp.gmail.com")
            with smtplib.SMTP_SSL(hote, int(os.getenv("EMAIL_PORT", "465")), timeout=20) as s:
                s.login(os.getenv("EMAIL_USER"), os.getenv("EMAIL_PASSWORD"))
                s.send_message(msg)
            envoye = True
        except Exception as e:
            log(f"   ⚠️ email a échoué : {e}")

    if not envoye:
        log("   (aucun canal configuré : définis NTFY_TOPIC et/ou EMAIL_USER + EMAIL_PASSWORD)")


# ---------------------------------------------------------------- boucle principale
def charger_etat():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def sauver_etat(etat):
    STATE_FILE.write_text(json.dumps(etat, ensure_ascii=False, indent=1, sort_keys=True),
                          encoding="utf-8")


def pause():
    time.sleep(random.uniform(1.5, 3.5))  # reste poli avec les sites


def traiter_produit(nom_site, url, titre, opts, etat):
    statut = verifier_produit(url, opts.get("js", False),
                              opts.get("texte_dispo"), opts.get("texte_rupture"))
    avant = etat["produits"].get(url)
    log(f"   {statut or 'inconnu':8} {titre[:70]}")
    if statut in (None, "bloque"):
        return statut
    if statut == "dispo" and avant != "dispo":
        notifier(f"✅ EN STOCK – {nom_site}", titre, url, urgent=True)
    etat["produits"][url] = statut
    return statut


def verifier_tout(config, etat):
    etat.setdefault("produits", {})
    etat.setdefault("recherches", {})
    etat.setdefault("bloques", {})
    filtre_global = config.get("filtre", {})
    max_par_site = config.get("max_produits_par_site", 8)

    for site in config.get("recherches", []):
        nom, url = site["nom"], site["url"]
        filtre = {**filtre_global, **site.get("filtre", {})}
        log(f"🔎 {nom}")
        try:
            status, html = fetch(url, site.get("js", False))
        except Exception as e:
            log(f"   erreur : {e}")
            continue

        bloque = est_bloque(status, html)
        if bloque != etat["bloques"].get(nom, False):
            etat["bloques"][nom] = bloque
            if bloque:
                notifier(f"⚠️ {nom} bloque la surveillance",
                         "Le site renvoie une protection anti-robot. Essaie js: true, "
                         "ou lance l'outil depuis ton PC.", url, urgent=False)
        if bloque:
            log(f"   bloqué (code {status})")
            continue

        trouves = liens_produits(html, url, filtre)
        if not trouves:
            log("   0 produit trouvé (si ça persiste : essaie js: true pour ce site)")
        premiere_fois = nom not in etat["recherches"]
        connus = set(etat["recherches"].get(nom, []))

        nouveaux = [u for u in trouves if u not in connus]
        for u in nouveaux:
            if not premiere_fois:
                notifier(f"🆕 Nouveau produit – {nom}", trouves[u], u, urgent=False)
        etat["recherches"][nom] = sorted(connus | set(trouves))

        # nouveaux d'abord, puis les autres
        a_verifier = nouveaux + [u for u in trouves if u not in nouveaux]
        for u in a_verifier[:max_par_site]:
            pause()
            try:
                traiter_produit(nom, u, trouves[u], site, etat)
            except Exception as e:
                log(f"   erreur fiche : {e}")
        pause()

    for p in config.get("produits", []) or []:
        log(f"📦 {p['nom']}")
        try:
            traiter_produit(p.get("site", p["nom"]), p["url"], p["nom"], p, etat)
        except Exception as e:
            log(f"   erreur : {e}")
        pause()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="envoie une notification de test")
    ap.add_argument("--boucle", type=float, metavar="MINUTES",
                    help="relance la vérification toutes les N minutes")
    args = ap.parse_args()

    if args.test:
        notifier("🧪 Test surveillance Pokémon", "Les notifications fonctionnent !",
                 "https://ntfy.sh")
        return

    config = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    while True:
        etat = charger_etat()
        try:
            verifier_tout(config, etat)
        finally:
            sauver_etat(etat)
            close_browser()
        if not args.boucle:
            break
        log(f"⏳ prochaine vérification dans {args.boucle} min")
        time.sleep(args.boucle * 60)


if __name__ == "__main__":
    sys.exit(main())
