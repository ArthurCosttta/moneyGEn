from __future__ import annotations

import concurrent.futures
import hashlib
import html as html_lib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import FlowError
from .product_spy import (
    extract_page_copy_summary,
    fetch_shopify_store_products,
    parse_compact_number,
    parse_price_brl,
    search_aliexpress,
)

STOPWORDS = {
    "de", "da", "do", "das", "dos", "para", "com", "sem", "uma", "uns", "umas",
    "que", "como", "the", "and", "for", "with", "this", "that", "from", "em",
    "no", "na", "nos", "nas", "por", "mais", "melhor", "oferta", "video", "vídeo",
    "shorts", "reels", "ad", "ads", "official", "oficial", "review",
}


def _slug(text: str, limit: int = 48) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", (text or "item").strip()).strip("-_")
    return cleaned[:limit] or "item"


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9À-ÿ]{3,}", (text or "").lower())
    return {w for w in words if w not in STOPWORDS}


def _domain_of(url: str | None) -> str:
    if not url or not isinstance(url, str):
        return ""
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        return host.removeprefix("www.")
    except ValueError:
        return ""


def _classify_angle(text: str) -> str:
    low = (text or "").lower()
    if any(k in low for k in ("unboxing", "abri", "chegou", "recebi")):
        return "Unboxing"
    if any(k in low for k in ("antes", "depois", "transform", "resultado")):
        return "Antes e Depois"
    if any(k in low for k in ("desconto", "frete", "promo", "hoje", "off", "r$")):
        return "Oferta / Preço"
    if any(k in low for k in ("como", "dica", "segredo", "tutorial", "passo")):
        return "Educativo / Tutorial"
    if any(k in low for k in ("teste", "review", "vale a pena", "funciona")):
        return "Teste / Review"
    return "Demonstração"


def _extract_ad_nodes(obj: Any, out: list[dict] | None = None, depth: int = 0) -> list[dict]:
    if out is None:
        out = []
    if depth > 50 or not isinstance(obj, (dict, list)):
        return out
    if isinstance(obj, list):
        for item in obj:
            _extract_ad_nodes(item, out, depth + 1)
        return out
    if "ad_archive_id" in obj:
        out.append(obj)
        return out
    for val in obj.values():
        _extract_ad_nodes(val, out, depth + 1)
    return out


def parse_meta_ad_library_html(raw_html: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    now_ts = datetime.now(timezone.utc).timestamp()

    for match in re.finditer(r"<script[^>]*>([\s\S]*?)</script>", raw_html, re.IGNORECASE):
        chunk = match.group(1)
        if "ad_archive_id" not in chunk:
            continue
        try:
            parsed = json.loads(chunk)
        except ValueError:
            continue
        for node in _extract_ad_nodes(parsed):
            ad_id = str(node.get("ad_archive_id") or "").strip()
            if not ad_id or ad_id in seen:
                continue
            seen.add(ad_id)
            snap = node.get("snapshot") if isinstance(node.get("snapshot"), dict) else {}
            videos = snap.get("videos") if isinstance(snap.get("videos"), list) else []
            images = snap.get("images") if isinstance(snap.get("images"), list) else []
            cards = snap.get("cards") if isinstance(snap.get("cards"), list) else []

            video_url = None
            thumb = None
            media_type = "image"
            card_link = ""
            if videos:
                media_type = "video"
                first_v = videos[0] if isinstance(videos[0], dict) else {}
                video_url = first_v.get("video_hd_url") or first_v.get("video_sd_url")
                thumb = first_v.get("video_preview_image_url")
            elif len(cards) > 1:
                media_type = "carousel"
                first_c = cards[0] if isinstance(cards[0], dict) else {}
                thumb = first_c.get("resized_image_url") or first_c.get("original_image_url")
                video_url = first_c.get("video_hd_url") or first_c.get("video_sd_url")
                card_link = str(first_c.get("link_url") or "").strip()
            elif cards:
                first_c = cards[0] if isinstance(cards[0], dict) else {}
                thumb = first_c.get("resized_image_url") or first_c.get("original_image_url")
                video_url = first_c.get("video_hd_url") or first_c.get("video_sd_url")
                card_link = str(first_c.get("link_url") or "").strip()
                if video_url:
                    media_type = "video"
            elif images:
                first_i = images[0] if isinstance(images[0], dict) else {}
                thumb = first_i.get("resized_image_url") or first_i.get("original_image_url") or first_i.get("url")

            body_obj = snap.get("body") if isinstance(snap.get("body"), dict) else {}
            text = str(body_obj.get("text") or snap.get("title") or "").strip()
            if not text and isinstance(node.get("ad_creative_bodies"), list) and node["ad_creative_bodies"]:
                text = str(node["ad_creative_bodies"][0] or "")
            if not text and cards and isinstance(cards[0], dict):
                text = str(cards[0].get("body") or cards[0].get("title") or "").strip()

            advertiser = str(node.get("page_name") or snap.get("page_name") or "Anunciante Meta").strip()
            page_id = str(node.get("page_id") or snap.get("page_id") or "").strip()
            link_url = str(snap.get("link_url") or card_link or "").strip()
            start_ts = node.get("start_date")
            days_active = None
            if isinstance(start_ts, (int, float)) and start_ts > 0:
                days_active = max(0, int((now_ts - float(start_ts)) // 86400))

            try:
                collation = int(node.get("collation_count") or 1)
            except (TypeError, ValueError):
                collation = 1

            items.append({
                "id": f"meta_{ad_id}",
                "source": "meta_ads",
                "source_label": "Meta Ads",
                "title": str(snap.get("title") or advertiser or f"Ad {ad_id}")[:140],
                "author": advertiser,
                "page_id": page_id,
                "url": f"https://www.facebook.com/ads/library/?id={ad_id}",
                "landing_url": link_url,
                "domain": _domain_of(link_url),
                "media_type": media_type,
                "thumbnail": thumb or "",
                "video_url": video_url or "",
                "text": text[:600],
                "angle": _classify_angle(f"{snap.get('title') or ''} {text}"),
                "days_active": days_active,
                "ad_count": max(1, collation),
                "views": None,
                "likes": None,
                "is_short": False,
                "downloadable": bool(video_url or thumb),
            })
    return items


class SpyService:
    """Deterministic multi-source video & offer researcher + landing page cloner."""

    def __init__(self, root: Path, data_dir: Path, repository, *, editor=None, gpt_chat=None, flow_runner=None) -> None:
        self.root = Path(root).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.repository = repository
        self.editor = editor
        self.gpt_chat = gpt_chat
        self.flow_runner = flow_runner
        self.spy_dir = self.data_dir / "spy"
        self.media_dir = self.spy_dir / "media"
        self.pages_dir = self.spy_dir / "pages"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.pages_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sys_python() -> Path | None:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            for py_dir in sorted((Path(local) / "Programs" / "Python").glob("Python3*"), reverse=True):
                cand = py_dir / "python.exe"
                if cand.is_file():
                    return cand.resolve()
        found = shutil.which("python")
        if found and ".runtime" not in Path(found).parts:
            return Path(found).resolve()
        return Path(sys.executable).resolve()

    def _playwright_python(self) -> Path | None:
        rt_py = self.root / ".runtime" / "tools" / "gflow-cli" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        for cand in (Path(sys.executable).resolve(), rt_py.resolve(), self._sys_python()):
            if cand and cand.is_file():
                try:
                    r = subprocess.run(
                        [str(cand), "-c", "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('playwright') else 1)"],
                        capture_output=True,
                        timeout=6,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                    if r.returncode == 0:
                        return cand
                except (OSError, subprocess.SubprocessError):
                    continue
        return None

    @staticmethod
    def _single_file_cmd() -> list[str] | None:
        appdata = os.environ.get("APPDATA")
        if appdata:
            cmd = Path(appdata) / "npm" / "single-file.cmd"
            if cmd.is_file():
                return [str(cmd)]
        found = shutil.which("single-file")
        return [found] if found else None

    @staticmethod
    def _chrome_exe() -> Path | None:
        for cand in (
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        ):
            if cand.is_file():
                return cand
        return None

    def _py_has(self, mod: str) -> bool:
        py = self._sys_python()
        if not py:
            return False
        try:
            r = subprocess.run(
                [str(py), "-c", f"import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('{mod}') else 1)"],
                capture_output=True,
                timeout=6,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def capabilities(self) -> dict[str, Any]:
        return {
            "yt_dlp": self._py_has("yt_dlp"),
            "instaloader": self._py_has("instaloader"),
            "pytrends": self._py_has("pytrends"),
            "single_file": self._single_file_cmd() is not None,
            "chrome": self._chrome_exe() is not None,
        }

    def verified_media_path(self, bucket: str, filename: str) -> Path:
        if bucket not in ("media", "pages"):
            raise FlowError("INVALID_ARTIFACT", "Pasta spy inválida.", status=404)
        if not filename or "/" in filename or "\\" in filename or ".." in filename:
            raise FlowError("INVALID_ARTIFACT", "Nome de arquivo inválido.", status=404)
        base = self.media_dir if bucket == "media" else self.pages_dir
        target = (base / filename).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            raise FlowError("ARTIFACT_NOT_FOUND", "Arquivo do Spy não encontrado.", status=404)
        return target

    def _run_py_snippet(self, code: str, args: list[str], *, timeout: int = 35) -> dict[str, Any]:
        py = self._sys_python()
        if not py:
            return {"items": [], "error": "Python local indisponível."}
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        try:
            r = subprocess.run(
                [str(py), "-c", code, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired:
            return {"items": [], "error": "Tempo limite atingido na busca."}
        out = (r.stdout or "").strip()
        if r.returncode != 0 or not out:
            return {"items": [], "error": (r.stderr or "Sem resposta.")[-300:]}
        try:
            return json.loads(out.splitlines()[-1])
        except ValueError:
            return {"items": [], "error": "Resposta inválida do pesquisador."}

    def _search_youtube(self, query: str, limit: int, *, shorts_only: bool = True) -> dict[str, Any]:
        code = r"""
import json, sys, urllib.parse, yt_dlp
q, lim, shorts_only = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "1"
opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
is_url = q.startswith(("http://", "https://"))
seen = set()
items = []
q_enc = urllib.parse.quote_plus(f"{q} shorts")
queries = [q] if is_url else [
    f"https://www.youtube.com/results?search_query={q_enc}&sp=CAMSAhAB",
    f"ytsearch{max(18, lim * 2)}:{q} #shorts",
]
if not is_url and not shorts_only:
    queries.append(f"ytsearch{lim}:{q}")
with yt_dlp.YoutubeDL(opts) as ydl:
    for target in queries:
        try:
            info = ydl.extract_info(target, download=False)
        except Exception:
            continue
        entries = info.get("entries") if isinstance(info, dict) and "entries" in info else ([info] if isinstance(info, dict) else [])
        for e in (entries or []):
            if not isinstance(e, dict): continue
            vid = str(e.get("id") or "")
            url = e.get("url") or e.get("webpage_url") or (f"https://www.youtube.com/shorts/{vid}" if vid else "")
            if not url or url in seen:
                continue
            dur = e.get("duration")
            title = str(e.get("title") or "Short YouTube").strip()
            is_short = bool("/shorts/" in url or "#shorts" in title.lower() or (isinstance(dur, (int, float)) and 0 < dur <= 90))
            if shorts_only and not is_url and not is_short:
                continue
            seen.add(url)
            thumbs = e.get("thumbnails") or []
            thumb = e.get("thumbnail") or (thumbs[-1].get("url") if thumbs and isinstance(thumbs[-1], dict) else "")
            ch = str(e.get("channel") or e.get("uploader") or "YouTube").strip()
            items.append({
                "id": f"yt_{vid or len(items)}",
                "source": "youtube",
                "source_label": "YouTube Shorts" if is_short else "YouTube",
                "title": title[:140],
                "author": ch,
                "url": url,
                "landing_url": "",
                "domain": "",
                "media_type": "video",
                "thumbnail": thumb,
                "video_url": url,
                "text": str(e.get("description") or title)[:400],
                "duration": dur,
                "views": e.get("view_count"),
                "likes": e.get("like_count"),
                "days_active": None,
                "ad_count": 1,
                "is_short": is_short,
                "downloadable": bool(url),
            })
            if len(items) >= lim:
                break
        if len(items) >= lim:
            break
print(json.dumps({"items": items[:lim]}, ensure_ascii=False))
"""
        res = self._run_py_snippet(code, [query, str(max(12, limit)), "1" if shorts_only else "0"], timeout=38)
        for it in res.get("items") or []:
            it["angle"] = _classify_angle(f"{it.get('title','')} {it.get('text','')}")
        return res

    def _search_tiktok(self, query: str, limit: int) -> dict[str, Any]:
        if query.startswith(("http://", "https://")) and "tiktok.com" in query.lower():
            return self._search_youtube(query, 1, shorts_only=False)
        items: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        # 1. Playwright direct TikTok search / @author scan extracting real hydration stats
        pw_py = self._playwright_python()
        chrome = self._chrome_exe()
        if pw_py:
            clean_q = query.strip()
            tt_url = (
                f"https://www.tiktok.com/@{urllib.parse.quote(clean_q.lstrip('@').split()[0])}"
                if clean_q.startswith("@")
                else f"https://www.tiktok.com/search/video?q={urllib.parse.quote(clean_q)}"
            )
            pw_tt = r"""
import json, re, sys
from playwright.sync_api import sync_playwright
url, chrome_path, lim = sys.argv[1], sys.argv[2], int(sys.argv[3])
items = []
try:
    with sync_playwright() as p:
        kw = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}
        if chrome_path:
            kw["executable_path"] = chrome_path
        browser = p.chromium.launch(**kw)
        try:
            ctx = browser.new_context(locale="pt-BR", user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=18000)
            page.wait_for_timeout(2000)
            raw = page.content()
            m = re.search(r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>([\s\S]*?)</script>', raw)
            if m:
                data = json.loads(m.group(1))
                def walk(o):
                    if isinstance(o, dict):
                        if "video" in o and "stats" in o and "id" in o and isinstance(o.get("author"), dict):
                            items.append(o)
                            return
                        for v in o.values(): walk(v)
                    elif isinstance(o, list):
                        for v in o: walk(v)
                walk(data)
        finally:
            browser.close()
except Exception:
    pass
out = []
for row in items[:lim]:
    auth = (row.get("author") or {}).get("uniqueId") or "tiktok"
    vid = str(row.get("id") or "")
    stats = row.get("stats") or {}
    vid_obj = row.get("video") or {}
    vurl = f"https://www.tiktok.com/@{auth}/video/{vid}"
    out.append({
        "id": f"tt_{vid}",
        "source": "tiktok",
        "source_label": "TikTok",
        "title": str(row.get("desc") or f"TikTok @{auth}")[:140],
        "author": f"@{auth}",
        "url": vurl,
        "landing_url": vurl,
        "domain": "tiktok.com",
        "media_type": "video",
        "thumbnail": vid_obj.get("cover") or vid_obj.get("originCover") or "",
        "video_url": vurl,
        "text": str(row.get("desc") or "")[:400],
        "views": stats.get("playCount"),
        "likes": stats.get("diggCount"),
        "days_active": None,
        "ad_count": 1,
        "is_short": True,
        "downloadable": True,
    })
print(json.dumps({"items": out}, ensure_ascii=False))
"""
            try:
                env = os.environ.copy()
                env["PYTHONUTF8"] = "1"
                r = subprocess.run(
                    [str(pw_py), "-c", pw_tt, tt_url, str(chrome) if chrome else "", str(limit)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=28, env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if r.returncode == 0 and r.stdout.strip():
                    for it in json.loads(r.stdout.strip().splitlines()[-1]).get("items") or []:
                        if it.get("url") and it["url"] not in seen_urls:
                            seen_urls.add(it["url"])
                            it["angle"] = _classify_angle(f"{it.get('title','')} {it.get('text','')}")
                            items.append(it)
            except Exception:
                pass

        # 2. Multi-tool DDG rollout finding real TikTok video links (+ yt-dlp metadata later)
        if len(items) < limit:
            web_code = r"""
import json, re, sys
from ddgs import DDGS
q, lim = sys.argv[1], int(sys.argv[2])
out = []
seen = set()
for dork in (f"site:tiktok.com/@ {q}", f"tiktok.com/video {q}"):
    try:
        with DDGS() as d:
            for r in d.text(dork, max_results=lim):
                href = str(r.get("href") or "")
                if "tiktok.com" in href and href not in seen and "/aclick" not in href:
                    seen.add(href)
                    m = re.search(r"tiktok\.com/(@[^/?#]+)", href)
                    out.append({"title": r.get("title") or "TikTok", "url": href, "text": r.get("body") or "", "author": m.group(1) if m else "TikTok"})
    except Exception:
        pass
print(json.dumps({"web": out[:lim]}, ensure_ascii=False))
"""
            web_res = self._run_py_snippet(web_code, [query, str(min(14, limit))], timeout=16)
            for idx, row in enumerate(web_res.get("web") or []):
                u = row.get("url") or ""
                if not u or u in seen_urls:
                    continue
                seen_urls.add(u)
                items.append({
                    "id": f"tt_web_{idx}_{hashlib.sha1(u.encode()).hexdigest()[:6]}",
                    "source": "tiktok",
                    "source_label": "TikTok",
                    "title": str(row.get("title") or "TikTok")[:140],
                    "author": str(row.get("author") or "TikTok"),
                    "url": u,
                    "landing_url": u,
                    "domain": "tiktok.com",
                    "media_type": "video",
                    "thumbnail": "",
                    "video_url": u if "/video/" in u else "",
                    "text": str(row.get("text") or "")[:400],
                    "views": None,
                    "likes": None,
                    "days_active": None,
                    "ad_count": 1,
                    "is_short": True,
                    "downloadable": "/video/" in u,
                    "angle": _classify_angle(f"{row.get('title','')} {row.get('text','')}"),
                })

        # 3. Viral Short fallback tagged TikTok if needed so popular searches never starve
        if len(items) < 6 and not query.startswith("@"):
            res = self._search_youtube(f"{query} tiktok viral", min(limit, 8), shorts_only=True)
            for it in res.get("items") or []:
                if it.get("url") in seen_urls:
                    continue
                it["source"] = "tiktok"
                it["source_label"] = "TikTok / Viral"
                it["id"] = f"tt_{it['id']}"
                items.append(it)

        return {"items": items[:limit], "error": None if items else "Nenhum vídeo encontrado no TikTok para este termo."}

    def _search_instagram(self, query: str, limit: int) -> dict[str, Any]:
        clean = query.strip()
        if clean.startswith("@"):
            code = r"""
import json, sys
from datetime import datetime, timezone
import instaloader
q, lim = sys.argv[1].strip(), int(sys.argv[2])
L = instaloader.Instaloader(download_pictures=False, download_videos=False, quiet=True)
items = []
err = None
try:
    prof = instaloader.Profile.from_username(L.context, q[1:].split()[0])
    posts = prof.get_posts()
    now_ts = datetime.now(timezone.utc)
    for idx, p in enumerate(posts):
        if idx >= lim:
            break
        mtype = "video" if p.is_video else ("carousel" if p.typename == "GraphSidecar" else "image")
        cap = (p.caption or "").strip()
        days = max(0, (now_ts - p.date_utc.replace(tzinfo=timezone.utc)).days) if p.date_utc else None
        items.append({
            "id": f"ig_{p.shortcode}",
            "source": "instagram",
            "source_label": "Instagram",
            "title": (cap.splitlines()[0] if cap else f"Post @{p.owner_username}")[:140],
            "author": f"@{p.owner_username}",
            "url": f"https://www.instagram.com/p/{p.shortcode}/",
            "landing_url": "",
            "domain": "instagram.com",
            "media_type": mtype,
            "thumbnail": p.url or "",
            "video_url": (p.video_url or "") if p.is_video else "",
            "text": cap[:500],
            "views": p.video_view_count if p.is_video else None,
            "likes": p.likes,
            "days_active": days,
            "ad_count": 1,
            "is_short": p.is_video,
            "downloadable": bool(p.video_url or p.url),
        })
except Exception as exc:
    err = str(exc)[:260]
print(json.dumps({"items": items, "error": err}, ensure_ascii=False))
"""
            res = self._run_py_snippet(code, [clean, str(limit)], timeout=25)
            for it in res.get("items") or []:
                it["angle"] = _classify_angle(f"{it.get('title','')} {it.get('text','')}")
            return res

        items: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        # 1. Hashtag Top Posts via Instaloader (real likes/views when allowed)
        tag = re.sub(r"[^a-zA-Z0-9À-ÿ]", "", clean.lower().split()[0])
        if tag:
            hash_code = r"""
import json, sys
import instaloader
tag, lim = sys.argv[1], int(sys.argv[2])
out = []
try:
    L = instaloader.Instaloader(download_pictures=False, download_videos=False, quiet=True)
    h = instaloader.Hashtag.from_name(L.context, tag)
    for idx, p in enumerate(h.get_top_posts()):
        if idx >= lim: break
        cap = (p.caption or "").strip()
        u = f"https://www.instagram.com/p/{p.shortcode}/"
        out.append({
            "id": f"ig_{p.shortcode}",
            "source": "instagram",
            "source_label": "Instagram Reels" if p.is_video else "Instagram",
            "title": (cap.splitlines()[0] if cap else f"#{tag}")[:140],
            "author": f"@{p.owner_username}" if getattr(p, "owner_username", None) else f"#{tag}",
            "url": u,
            "landing_url": "",
            "domain": "instagram.com",
            "media_type": "video" if p.is_video else "image",
            "thumbnail": p.url or "",
            "video_url": (p.video_url or "") if p.is_video else "",
            "text": cap[:500],
            "views": p.video_view_count if p.is_video else None,
            "likes": p.likes,
            "days_active": None,
            "ad_count": 1,
            "is_short": bool(p.is_video),
            "downloadable": bool(p.video_url or p.url),
        })
except Exception:
    pass
print(json.dumps({"items": out}, ensure_ascii=False))
"""
            h_res = self._run_py_snippet(hash_code, [tag, str(min(8, limit))], timeout=16)
            for it in h_res.get("items") or []:
                if it.get("url") and it["url"] not in seen_urls:
                    seen_urls.add(it["url"])
                    it["angle"] = _classify_angle(f"{it.get('title','')} {it.get('text','')}")
                    items.append(it)

        # 2. Multi-query DDGS rollout for Reels / posts
        ddgs_code = r"""
import json, re, sys
from ddgs import DDGS
q, lim = sys.argv[1], int(sys.argv[2])
out = []
seen = set()
for dork in (f"site:instagram.com/reel {q}", f"site:instagram.com/p {q}"):
    try:
        with DDGS() as d:
            for r in d.text(dork, max_results=lim):
                href = str(r.get("href") or "")
                if "instagram.com" in href and href not in seen:
                    seen.add(href)
                    m = re.search(r"instagram\.com/([^/?#]+)", href)
                    handle = f"@{m.group(1)}" if m and m.group(1) not in ("reel", "reels", "p", "explore") else "Instagram"
                    out.append({"title": r.get("title") or "Instagram Reel", "url": href, "text": r.get("body") or "", "author": handle})
    except Exception:
        pass
print(json.dumps({"items": out[:lim]}, ensure_ascii=False))
"""
        dd = self._run_py_snippet(ddgs_code, [clean, str(min(limit, 12))], timeout=16)
        for idx, row in enumerate(dd.get("items") or []):
            u = row.get("url") or ""
            if not u or u in seen_urls:
                continue
            seen_urls.add(u)
            items.append({
                "id": f"ig_web_{idx}",
                "source": "instagram",
                "source_label": "Instagram Reels",
                "title": str(row.get("title") or "Instagram")[:140],
                "author": str(row.get("author") or "Instagram"),
                "url": u,
                "landing_url": u,
                "domain": "instagram.com",
                "media_type": "video" if "/reel" in u else "image",
                "thumbnail": "",
                "video_url": u if ("/reel" in u or "/p/" in u) else "",
                "text": str(row.get("text") or "")[:400],
                "views": None,
                "likes": None,
                "days_active": None,
                "ad_count": 1,
                "is_short": True,
                "downloadable": bool("/reel/" in u or "/p/" in u),
                "angle": _classify_angle(f"{row.get('title','')} {row.get('text','')}"),
            })
        if len(items) < 6:
            yt_ig = self._search_youtube(f"{clean} reels", min(6, limit), shorts_only=True)
            for it in yt_ig.get("items") or []:
                if it.get("url") in seen_urls:
                    continue
                it["source"] = "instagram"
                it["source_label"] = "Instagram Reels"
                it["id"] = f"ig_{it['id']}"
                items.append(it)
        return {"items": items[:limit], "error": None if items else "Nenhum Reel encontrado para este termo."}

    def _search_meta_ads(self, query: str, country: str, limit: int, *, competitor_page_id: str | None = None) -> dict[str, Any]:
        geo = (country or "BR").upper()[:2]
        if competitor_page_id and str(competitor_page_id).strip().isdigit():
            params = urllib.parse.urlencode({
                "active_status": "active",
                "ad_type": "all",
                "country": geo,
                "view_all_page_id": str(competitor_page_id).strip(),
                "search_type": "page",
            })
        else:
            params = urllib.parse.urlencode({
                "active_status": "active",
                "ad_type": "all",
                "country": geo,
                "q": query,
                "search_type": "keyword_unordered",
            })
        url = f"https://www.facebook.com/ads/library/?{params}"
        raw_html = ""
        err = None
        if limit <= 12 and not competitor_page_id:
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
                    },
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw_html = resp.read().decode("utf-8", errors="replace")
            except Exception:
                raw_html = ""

        items = parse_meta_ad_library_html(raw_html) if raw_html else []
        if len(items) < min(limit, 18):
            pw_py = self._playwright_python()
            chrome = self._chrome_exe()
            if pw_py:
                pw_code = r"""
import json, sys
from playwright.sync_api import sync_playwright
url, chrome_path, target_lim = sys.argv[1], sys.argv[2], int(sys.argv[3])
html = ""
err = None
try:
    with sync_playwright() as p:
        kw = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}
        if chrome_path:
            kw["executable_path"] = chrome_path
        browser = p.chromium.launch(**kw)
        try:
            page = browser.new_context(locale="pt-BR").new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=22000)
            page.wait_for_timeout(1800)
            # Scroll down to load more creatives when more are requested
            scrolls = 3 if target_lim >= 18 else 1
            for _ in range(scrolls):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1200)
            html = page.content()
        finally:
            browser.close()
except Exception as exc:
    err = str(exc)[:200]
print(json.dumps({"html": html, "error": err}, ensure_ascii=False))
"""
                try:
                    env = os.environ.copy()
                    env["PYTHONUTF8"] = "1"
                    r = subprocess.run(
                        [str(pw_py), "-c", pw_code, url, str(chrome) if chrome else "", str(limit)],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=38,
                        env=env,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        payload = json.loads(r.stdout.strip().splitlines()[-1])
                        if payload.get("html"):
                            pw_items = parse_meta_ad_library_html(payload["html"])
                            if pw_items:
                                items = pw_items
                        elif payload.get("error"):
                            err = f"Meta Ad Library indisponível: {payload['error']}"
                except Exception as exc:
                    err = f"Meta Ad Library indisponível: {str(exc)[:180]}"
            elif not items:
                err = "Meta Ad Library indisponível: Playwright não encontrado neste ambiente."

        if not items and not err:
            err = "Nenhum anúncio ativo retornado para este termo nesta consulta."
        return {"items": items[:limit], "error": err if not items else None, "search_url": url}

    def _search_aliexpress(self, query: str, limit: int) -> dict[str, Any]:
        return search_aliexpress(query, limit=limit)

    def _search_amazon(self, query: str, limit: int) -> dict[str, Any]:
        pw_py = self._playwright_python()
        chrome = self._chrome_exe()
        url = f"https://www.amazon.com.br/s?k={urllib.parse.quote_plus(query)}&s=exact-aware-popularity-rank"
        if not pw_py:
            return {"items": [], "error": "Amazon BR requer Playwright.", "search_url": url}
        pw_code = r"""
import json, sys
from playwright.sync_api import sync_playwright
url, chrome_path, lim = sys.argv[1], sys.argv[2], int(sys.argv[3])
rows = []
err = None
try:
    with sync_playwright() as p:
        kw = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}
        if chrome_path: kw["executable_path"] = chrome_path
        b = p.chromium.launch(**kw)
        try:
            page = b.new_context(locale="pt-BR", user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36").new_page()
            js_extract = (
                "(lim) => Array.from(document.querySelectorAll('div[data-asin]'))"
                ".filter(el => el.getAttribute('data-asin') && el.querySelector('h2'))"
                ".slice(0, lim)"
                ".map(el => ({"
                "asin: el.getAttribute('data-asin'),"
                "title: (el.querySelector('h2')?.innerText || '').trim(),"
                "price: (el.querySelector('.a-price .a-offscreen')?.innerText || '').trim(),"
                "rating: (el.querySelector('.a-icon-alt')?.innerText || '').trim(),"
                "img: el.querySelector('img.s-image')?.src || '',"
                "txt: (el.innerText || '').replace(/\\s+/g, ' ').trim()"
                "}))"
            )
            page.goto(url, wait_until="domcontentloaded", timeout=18000)
            page.wait_for_timeout(1200)
            rows = page.evaluate(js_extract, lim)
            if len(rows) < lim:
                page.goto(url + "&page=2", wait_until="domcontentloaded", timeout=18000)
                page.wait_for_timeout(1000)
                seen = {r.get("asin") for r in rows}
                for r2 in page.evaluate(js_extract, lim):
                    if r2.get("asin") not in seen:
                        seen.add(r2.get("asin"))
                        rows.append(r2)
                        if len(rows) >= lim:
                            break
        finally:
            b.close()
except Exception as exc:
    err = str(exc)[:180]
print(json.dumps({"rows": rows, "error": err}, ensure_ascii=False))
"""
        try:
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            r = subprocess.run(
                [str(pw_py), "-c", pw_code, url, str(chrome) if chrome else "", str(limit)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=42, env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            payload = json.loads((r.stdout or "{}").strip().splitlines()[-1]) if r.stdout.strip() else {}
        except Exception as exc:
            return {"items": [], "error": f"Amazon BR indisponível: {str(exc)[:160]}", "search_url": url}

        items: list[dict[str, Any]] = []
        for row in payload.get("rows") or []:
            asin = str(row.get("asin") or "").strip()
            title = str(row.get("title") or "").strip()
            if not asin or not title:
                continue
            txt = str(row.get("txt") or "")
            bought_m = re.search(r"(mais de\s+[\d.,]+(?:\s*mil)?\s+compras[^.]*|[\d.,]+\s*(?:mil\s+)?compras?\s+no\s+m[eê]s)", txt, re.IGNORECASE)
            sold_text = bought_m.group(1) if bought_m else ""
            sold_count = parse_compact_number(sold_text)
            price_text = str(row.get("price") or "").strip()
            price_val = parse_price_brl(price_text)
            prod_url = f"https://www.amazon.com.br/dp/{asin}"
            items.append({
                "id": f"amazon:{asin}",
                "source": "amazon",
                "source_label": "Amazon BR",
                "media_type": "product",
                "title": title[:160],
                "text": " · ".join(p for p in (price_text, sold_text, str(row.get("rating") or "")) if p),
                "author": "Amazon.com.br",
                "url": prod_url,
                "landing_url": prod_url,
                "domain": "amazon.com.br",
                "thumbnail": str(row.get("img") or ""),
                "price_text": price_text,
                "price_value": price_val,
                "sold_text": sold_text,
                "sold_count": sold_count,
                "est_revenue_min": round(price_val * sold_count, 2) if (price_val and sold_count) else None,
                "likes": sold_count,
                "views": sold_count * 10 if sold_count else None,
                "active_ads": None,
                "downloadable": False,
            })
        return {"items": items, "error": payload.get("error") if not items else None, "search_url": url}

    def _search_shopify(self, query: str, limit: int, *, domain: str | None = None) -> dict[str, Any]:
        if domain:
            items = fetch_shopify_store_products(domain, query=query, limit=limit)
            return {"items": items, "error": None if items else f"Nenhum produto em /products.json de {domain}."}
        dd_code = r"""
import json, sys, urllib.parse
from ddgs import DDGS
q = sys.argv[1]
domains = []
seen = set()
try:
    with DDGS() as d:
        for dork in (f'"myshopify.com" {q}', f'"powered by shopify" {q}', f'/products/ {q}'):
            for r in d.text(dork, max_results=6):
                href = str(r.get("href") or "")
                host = urllib.parse.urlsplit(href).netloc.lower()
                if host and host not in seen:
                    seen.add(host)
                    domains.append(f"https://{host}")
except Exception:
    pass
print(json.dumps({"domains": domains[:6]}, ensure_ascii=False))
"""
        dd = self._run_py_snippet(dd_code, [query], timeout=16)
        items: list[dict[str, Any]] = []
        for dom in dd.get("domains") or []:
            items.extend(fetch_shopify_store_products(dom, query=query, limit=6))
            if len(items) >= limit:
                break
        return {"items": items[:limit], "error": None if items else "Nenhuma loja Shopify pública encontrada para este termo."}

    def _search_marketplace_ddg(self, source: str, label: str, site_filter: str, base_domain: str, query: str, limit: int) -> dict[str, Any]:
        code = r"""
import json, re, sys
from ddgs import DDGS
site_f, q, lim = sys.argv[1], sys.argv[2], int(sys.argv[3])
out = []
seen = set()
try:
    with DDGS() as d:
        for r in d.text(f"site:{site_f} {q}", max_results=lim):
            href = str(r.get("href") or "")
            if href and href not in seen and "/aclick" not in href:
                seen.add(href)
                out.append({"title": r.get("title") or "", "url": href, "body": r.get("body") or ""})
except Exception as exc:
    pass
print(json.dumps({"rows": out}, ensure_ascii=False))
"""
        res = self._run_py_snippet(code, [site_filter, query, str(limit)], timeout=16)
        items: list[dict[str, Any]] = []
        for idx, row in enumerate(res.get("rows") or []):
            u = str(row.get("url") or "")
            title = str(row.get("title") or label).strip()
            body = str(row.get("body") or "").strip()
            pm = re.search(r"R\$\s*\d[\d.,]*", f"{title} {body}")
            price_text = pm.group(0) if pm else ""
            sm = re.search(r"(\+?\d[\d.,]*\s*(?:mil|k)?\s*vendid[oa]s?)", body, re.IGNORECASE)
            sold_text = sm.group(1) if sm else ""
            sold_count = parse_compact_number(sold_text)
            items.append({
                "id": f"{source}_{idx}_{hashlib.sha1(u.encode()).hexdigest()[:6]}",
                "source": source,
                "source_label": label,
                "media_type": "product",
                "title": title[:160],
                "text": body[:320],
                "author": label,
                "url": u,
                "landing_url": u,
                "domain": _domain_of(u) or base_domain,
                "thumbnail": "",
                "price_text": price_text,
                "price_value": parse_price_brl(price_text),
                "sold_text": sold_text,
                "sold_count": sold_count,
                "likes": sold_count if sold_count else None,
                "views": None,
                "active_ads": None,
                "downloadable": False,
            })
        return {"items": items, "error": None if items else f"{label} sem resultados públicos no momento."}

    def _search_mercadolivre(self, query: str, limit: int) -> dict[str, Any]:
        # Optional official ML OAuth token gives direct sold_quantity & thumbnails
        ml_token = (self.repository.get_setting("meli_access_token") if self.repository else "") or ""
        if ml_token.strip():
            api_url = f"https://api.mercadolibre.com/sites/MLB/search?q={urllib.parse.quote_plus(query)}&sort=sold_quantity_desc&limit={min(limit, 30)}"
            try:
                req = urllib.request.Request(api_url, headers={"Authorization": f"Bearer {ml_token.strip()}", "User-Agent": "FlowLocal/3.0"})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                items = []
                for row in (data.get("results") or [])[:limit]:
                    sold = int(row.get("sold_quantity") or 0)
                    price = row.get("price")
                    price_text = f"R$ {price:.2f}".replace(".", ",") if isinstance(price, (int, float)) else ""
                    perm = str(row.get("permalink") or "")
                    items.append({
                        "id": f"ml_{row.get('id')}",
                        "source": "mercadolivre",
                        "source_label": "Mercado Livre",
                        "media_type": "product",
                        "title": str(row.get("title") or "")[:160],
                        "text": f"{price_text} · {sold} vendido(s)" if price_text else f"{sold} vendido(s)",
                        "author": str((row.get("seller") or {}).get("nickname") or "Mercado Livre"),
                        "url": perm,
                        "landing_url": perm,
                        "domain": "mercadolivre.com.br",
                        "thumbnail": str(row.get("thumbnail") or "").replace("http://", "https://"),
                        "price_text": price_text,
                        "price_value": float(price) if isinstance(price, (int, float)) else None,
                        "sold_text": f"{sold} vendidos" if sold else "",
                        "sold_count": sold,
                        "likes": sold,
                        "views": sold * 10 if sold else None,
                        "downloadable": False,
                    })
                if items:
                    return {"items": items, "error": None, "search_url": api_url}
            except Exception:
                pass
        return self._search_marketplace_ddg("mercadolivre", "Mercado Livre", "mercadolivre.com.br", "mercadolivre.com.br", query, limit)

    def _search_shopee(self, query: str, limit: int) -> dict[str, Any]:
        return self._search_marketplace_ddg("shopee", "Shopee", "shopee.com.br", "shopee.com.br", query, limit)

    CATEGORY_MAP = {
        "ecommerce": {
            "beleza": ("Beleza & Skincare", "serum skincare led"),
            "cozinha": ("Casa & Cozinha", "organizador cozinha"),
            "tech": ("Tech & Gadgets", "mini projetor gadget led"),
            "pet": ("Pet", "escova pet comedouro"),
            "fitness": ("Fitness & Saúde", "garrafa termica postura"),
            "moda": ("Moda & Acessórios", "bolsa organizadora relogio"),
            "natal": ("Natal & Sazonais", "decoracao natal led"),
        },
        "youtube_dark": {
            "curiosidades": ("Curiosidades & Fatos", "curiosidades bizarras shorts"),
            "historia": ("História & Mistérios", "misterios historia oculta shorts"),
            "financas": ("Finanças & Negócios", "educacao financeira riqueza shorts"),
            "motivacao": ("Estoicismo & Foco", "estoicismo disciplina shorts"),
            "terror": ("Terror & Suspense", "historias reais suspense shorts"),
            "ia": ("IA & Tecnologia", "inteligencia artificial ferramentas shorts"),
        },
    }

    def trending_products(
        self,
        country: str = "BR",
        keywords: str | None = None,
        *,
        track: str = "all",
        categories: str | None = None,
    ) -> dict[str, Any]:
        geo = (country or "BR").upper()[:2]
        ml_items: list[str] = []
        google_trends: list[str] = []

        if keywords is not None and self.repository is not None:
            self.repository.set_setting("spy_monitor_keywords", str(keywords).strip())
        saved_kw = (self.repository.get_setting("spy_monitor_keywords") if self.repository else "") or ""
        kw_list = [k.strip() for k in re.split(r"[,;\n]+", (keywords if keywords is not None else saved_kw) or "") if k.strip()]
        if not kw_list:
            kw_list = ["natal", "skincare", "organizador"]

        req_track = (track or "all").strip().lower()
        selected_cats = [c.strip().lower() for c in re.split(r"[,;\s]+", (categories or "")) if c.strip()]

        ecom_queries: list[tuple[str, str]] = []
        dark_queries: list[tuple[str, str]] = []
        for cat_id in selected_cats:
            if cat_id in self.CATEGORY_MAP["ecommerce"]:
                lbl, q = self.CATEGORY_MAP["ecommerce"][cat_id]
                ecom_queries.append((lbl, q))
            if cat_id in self.CATEGORY_MAP["youtube_dark"]:
                lbl, q = self.CATEGORY_MAP["youtube_dark"][cat_id]
                dark_queries.append((lbl, q))

        if not ecom_queries and req_track in ("all", "ecommerce"):
            ecom_queries = [(kw, kw) for kw in kw_list[:4]]
        if not dark_queries and req_track in ("all", "youtube_dark"):
            dark_queries = [self.CATEGORY_MAP["youtube_dark"]["curiosidades"], self.CATEGORY_MAP["youtube_dark"]["historia"]]

        # 1. Mercado Livre Tendências BR
        try:
            req = urllib.request.Request(
                "https://tendencias.mercadolivre.com.br/",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36", "Accept-Language": "pt-BR,pt;q=0.9"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            matches = re.findall(r'<(?:a|span)[^>]*class="[^"]*trends[^"]*"[^>]*>([^<]+)<|href="https://lista\.mercadolivre\.com\.br/[^"]+"[^>]*>([^<]{3,60})<', raw)
            for a, b in matches:
                t = html_lib.unescape(re.sub(r"\s+", " ", (a or b or ""))).strip()
                if t and len(t) > 2 and t not in ml_items:
                    ml_items.append(t)
        except Exception:
            pass

        # 2. Google Trends RSS BR
        try:
            with urllib.request.urlopen(f"https://trends.google.com/trending/rss?geo={geo}", timeout=8) as resp:
                xml_root = ET.fromstring(resp.read())
            for el in xml_root.findall(".//item/title")[:14]:
                txt = (el.text or "").strip()
                if txt and txt not in google_trends:
                    google_trends.append(txt)
        except Exception:
            pass

        # 3. Fan out across selected E-commerce & YouTube Dark categories in parallel
        hot_products: list[dict[str, Any]] = []
        hot_videos: list[dict[str, Any]] = []
        seen_prod_ids: set[str] = set()
        seen_vid_ids: set[str] = set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            ali_futs = [(lbl, pool.submit(search_aliexpress, q, limit=16)) for lbl, q in ecom_queries[:4]]
            amz_futs = [(lbl, pool.submit(self._search_amazon, q, 12)) for lbl, q in ecom_queries[:2]]
            dark_futs = [(lbl, pool.submit(self._search_youtube, q, 12, shorts_only=True)) for lbl, q in dark_queries[:3]]

            for lbl, fut in ali_futs + amz_futs:
                try:
                    res = fut.result(timeout=26)
                    for it in res.get("items") or []:
                        if it.get("id") in seen_prod_ids:
                            continue
                        seen_prod_ids.add(it["id"])
                        it["category_label"] = lbl
                        hot_products.append(it)
                except Exception:
                    pass

            for lbl, fut in dark_futs:
                try:
                    res = fut.result(timeout=26)
                    for it in res.get("items") or []:
                        if it.get("id") in seen_vid_ids:
                            continue
                        seen_vid_ids.add(it["id"])
                        it["category_label"] = lbl
                        hot_videos.append(it)
                except Exception:
                    pass

        self._enrich_video_likes(hot_videos[:16])
        hot_products.sort(key=lambda x: (x.get("sold_count") or 0, x.get("est_revenue_min") or 0), reverse=True)
        hot_videos.sort(key=lambda x: (x.get("views") or 0, x.get("likes") or 0), reverse=True)

        return {
            "country": geo,
            "track": req_track,
            "categories": selected_cats,
            "category_catalog": {
                k: [{"id": cid, "label": info[0]} for cid, info in grp.items()]
                for k, grp in self.CATEGORY_MAP.items()
            },
            "keywords": kw_list,
            "products": ml_items[:28],
            "searches": google_trends[:20],
            "hot_products": hot_products[:80],
            "hot_videos": hot_videos[:36],
        }

    def _search_trends(self, query: str, country: str) -> dict[str, Any]:
        geo = (country or "BR").upper()[:2]
        out: dict[str, Any] = {"score": None, "delta_pct": None, "rising": [], "top": [], "error": None}

        # 1. Fast Google Suggest related buyer searches (never 429s)
        try:
            sug_url = f"https://suggestqueries.google.com/complete/search?client=firefox&hl=pt-BR&gl={geo}&q={urllib.parse.quote(query)}"
            req = urllib.request.Request(sug_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                out["rising"] = [{"query": str(x), "value": "em alta"} for x in data[1][:8] if str(x).strip()]
        except Exception:
            pass

        # 2. Optional pytrends enrichment (ignored cleanly if Google returns 429)
        code = r"""
import json, sys
from pytrends.request import TrendReq
q, geo = sys.argv[1], (sys.argv[2] or "BR").upper()[:2]
out = {"score": None, "delta_pct": None, "rising": []}
try:
    pt = TrendReq(hl="pt-BR", tz=180, timeout=(5, 10))
    pt.build_payload([q], timeframe="today 3-m", geo=geo)
    df = pt.interest_over_time()
    if not df.empty and q in df.columns:
        vals = [int(v) for v in df[q].tolist()]
        if vals:
            out["score"] = vals[-1]
            prev = sum(vals[:max(1, len(vals)//2)]) / max(1, len(vals)//2)
            recent = sum(vals[len(vals)//2:]) / max(1, len(vals) - len(vals)//2)
            if prev > 0:
                out["delta_pct"] = round(((recent - prev) / prev) * 100, 1)
    rel = pt.related_queries().get(q) or {}
    if rel.get("rising") is not None and not rel["rising"].empty:
        out["rising"] = rel["rising"].head(6).to_dict("records")
except Exception:
    pass
print(json.dumps(out, ensure_ascii=False))
"""
        pt_res = self._run_py_snippet(code, [query, geo], timeout=14)
        if pt_res.get("score") is not None:
            out["score"] = pt_res["score"]
            out["delta_pct"] = pt_res.get("delta_pct")
        if pt_res.get("rising"):
            out["rising"] = pt_res["rising"]
        if not out["rising"] and out["score"] is None:
            out["error"] = "Sem dados extras no momento"
        return out

    def resolve_stream(self, url: str) -> dict[str, Any]:
        target = (url or "").strip()
        if not target.startswith(("http://", "https://")):
            raise FlowError("SCHEMA_VALIDATION", "URL de vídeo inválida.")
        if re.search(r"\.(?:mp4|mov|webm)(?:\?|$)", target, re.IGNORECASE) or "fbcdn.net" in target:
            return {"stream_url": target}
        code = r"""
import json, sys, yt_dlp
opts = {"quiet": True, "no_warnings": True, "format": "b[ext=mp4]/b", "noplaylist": True}
with yt_dlp.YoutubeDL(opts) as ydl:
    info = ydl.extract_info(sys.argv[1], download=False)
    stream = info.get("url")
    if not stream:
        for f in reversed(info.get("formats") or []):
            if f.get("ext") == "mp4" and f.get("vcodec") != "none" and f.get("acodec") != "none" and f.get("url"):
                stream = f["url"]
                break
print(json.dumps({"stream_url": stream or ""}, ensure_ascii=False))
"""
        res = self._run_py_snippet(code, [target], timeout=22)
        if not res.get("stream_url"):
            raise FlowError("LOCAL_FAILURE", "Não foi possível carregar o player direto; use Baixar vídeo.")
        return {"stream_url": res["stream_url"]}

    @staticmethod
    def _cross_reference(items: list[dict[str, Any]], trends: dict[str, Any], query: str) -> dict[str, Any]:
        q_tokens = _tokens(query)
        author_sources: dict[str, set[str]] = {}
        author_counts: dict[str, int] = {}
        domain_sources: dict[str, set[str]] = {}
        domain_counts: dict[str, int] = {}
        token_sources: dict[str, set[str]] = {}

        for it in items:
            src = it.get("source") or ""
            auth = re.sub(r"[^a-z0-9]", "", (it.get("author") or "").lower().lstrip("@"))
            dom = it.get("domain") or ""
            if auth:
                author_sources.setdefault(auth, set()).add(src)
                author_counts[auth] = author_counts.get(auth, 0) + 1
            if dom:
                domain_sources.setdefault(dom, set()).add(src)
                domain_counts[dom] = domain_counts.get(dom, 0) + 1
            for tok in (_tokens(f"{it.get('title','')} {it.get('text','')}") - q_tokens):
                token_sources.setdefault(tok, set()).add(src)

        delta = trends.get("delta_pct")
        for it in items:
            src = it.get("source") or ""
            auth = re.sub(r"[^a-z0-9]", "", (it.get("author") or "").lower().lstrip("@"))
            dom = it.get("domain") or ""
            matched_sources = {src}
            if auth:
                matched_sources |= author_sources.get(auth, set())
            if dom:
                matched_sources |= domain_sources.get(dom, set())
            item_toks = _tokens(f"{it.get('title','')} {it.get('text','')}") - q_tokens
            shared_multi = [t for t in item_toks if len(token_sources.get(t, set())) >= 2]
            if shared_multi:
                for t in shared_multi[:3]:
                    matched_sources |= token_sources[t]

            score = 0
            reasons: list[str] = []
            cross_n = len(matched_sources)
            score += cross_n * 25
            if cross_n >= 2:
                reasons.append(f"Em {cross_n} fontes")

            days = it.get("days_active")
            if isinstance(days, int):
                if days >= 30:
                    score += 30
                    reasons.append(f"{days}d ativo")
                elif days >= 14:
                    score += 20
                    reasons.append(f"{days}d ativo")
                elif days >= 7:
                    score += 10
                    reasons.append(f"{days}d ativo")

            rep_count = max(int(it.get("ad_count") or 1), author_counts.get(auth, 1), domain_counts.get(dom, 1))
            it["ad_count"] = rep_count
            if rep_count >= 2:
                score += min(25, rep_count * 5)
                reasons.append(f"{rep_count} anúncios/criativos")

            views = it.get("views") or 0
            likes = it.get("likes") or 0
            if views >= 50_000 or likes >= 1_000:
                score += 15
                reasons.append("Alto alcance")
            elif views >= 5_000 or likes >= 100:
                score += 7

            if isinstance(delta, (int, float)) and delta > 10:
                score += 10
                reasons.append(f"Tendência +{delta}%")

            it["cross_sources"] = sorted(matched_sources)
            it["cross_count"] = cross_n
            it["validation_score"] = score
            it["reasons"] = reasons

        items.sort(key=lambda x: (x["validation_score"], x.get("days_active") or 0, x.get("views") or 0), reverse=True)

        clusters = []
        author_page_ids: dict[str, str] = {}
        author_raw_name: dict[str, str] = {}
        for it in items:
            raw_auth = str(it.get("author") or "").strip()
            norm_auth = re.sub(r"[^a-z0-9]", "", raw_auth.lower().lstrip("@"))
            if norm_auth and raw_auth and norm_auth not in author_raw_name:
                author_raw_name[norm_auth] = raw_auth
            if norm_auth and it.get("page_id") and norm_auth not in author_page_ids:
                author_page_ids[norm_auth] = str(it["page_id"])
        for dom, cnt in sorted(domain_counts.items(), key=lambda kv: kv[1], reverse=True)[:6]:
            if dom:
                clusters.append({"label": dom, "kind": "domain", "count": cnt, "sources": sorted(domain_sources[dom])})
        for auth, cnt in sorted(author_counts.items(), key=lambda kv: kv[1], reverse=True)[:6]:
            if cnt >= 2 and auth:
                clusters.append({
                    "label": author_raw_name.get(auth, auth),
                    "kind": "author",
                    "count": cnt,
                    "page_id": author_page_ids.get(auth, ""),
                    "sources": sorted(author_sources[auth]),
                })

        return {"items": items, "clusters": clusters}

    def search(self, body: dict[str, Any]) -> dict[str, Any]:
        query = str(body.get("query") or "").strip()
        competitor_page_id = str(body.get("competitor_page_id") or "").strip() or None
        competitor_author = str(body.get("competitor_author") or "").strip() or None
        competitor_domain = str(body.get("competitor_domain") or "").strip() or None
        if not query and (competitor_page_id or competitor_author or competitor_domain):
            query = competitor_author or competitor_domain or str(competitor_page_id)
        if not query or len(query) > 300:
            raise FlowError("SCHEMA_VALIDATION", "Digite um produto, termo, @perfil ou URL para pesquisar.", pointer="/query")

        country = str(body.get("country") or "BR").strip().upper()[:2] or "BR"
        mode = str(body.get("mode") or "all").strip().lower()
        default_lim = 72 if mode == "products" else 24
        limit = max(3, min(120, int(body.get("limit") or default_lim)))
        req_sources = body.get("sources")
        if not isinstance(req_sources, list) or not req_sources:
            if mode == "products":
                req_sources = ["aliexpress", "amazon", "mercadolivre", "shopify", "shopee"]
            else:
                req_sources = ["meta_ads", "tiktok", "instagram", "youtube", "trends"]

        all_items: list[dict[str, Any]] = []
        source_status: dict[str, Any] = {}
        trends_info: dict[str, Any] = {}

        effective_social_q = competitor_author or query
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures: dict[str, concurrent.futures.Future] = {}
            if "youtube" in req_sources:
                futures["youtube"] = pool.submit(self._search_youtube, effective_social_q, limit, shorts_only=True)
            if "tiktok" in req_sources:
                tt_q = competitor_author if (competitor_author and competitor_author.startswith("@")) else effective_social_q
                futures["tiktok"] = pool.submit(self._search_tiktok, tt_q, limit)
            if "instagram" in req_sources:
                ig_q = competitor_author if (competitor_author and competitor_author.startswith("@")) else effective_social_q
                futures["instagram"] = pool.submit(self._search_instagram, ig_q, limit)
            if "meta_ads" in req_sources:
                meta_lim = max(limit, 30) if competitor_page_id else limit
                futures["meta_ads"] = pool.submit(
                    self._search_meta_ads,
                    query,
                    country,
                    meta_lim,
                    competitor_page_id=competitor_page_id,
                )
            if "aliexpress" in req_sources:
                futures["aliexpress"] = pool.submit(self._search_aliexpress, query, limit)
            if "amazon" in req_sources:
                futures["amazon"] = pool.submit(self._search_amazon, query, limit)
            if "shopify" in req_sources:
                futures["shopify"] = pool.submit(self._search_shopify, query, limit, domain=competitor_domain)
            if "mercadolivre" in req_sources:
                futures["mercadolivre"] = pool.submit(self._search_mercadolivre, query, limit)
            if "shopee" in req_sources:
                futures["shopee"] = pool.submit(self._search_shopee, query, limit)
            if "trends" in req_sources:
                futures["trends"] = pool.submit(self._search_trends, query, country)

            for name, fut in futures.items():
                try:
                    res = fut.result(timeout=45)
                except Exception as exc:
                    res = {"items": [], "error": str(exc)[:200]}
                if name == "trends":
                    trends_info = res
                    source_status[name] = {"ok": not res.get("error"), "error": res.get("error")}
                else:
                    found = res.get("items") or []
                    all_items.extend(found)
                    source_status[name] = {
                        "ok": bool(found) or not res.get("error"),
                        "count": len(found),
                        "error": res.get("error"),
                        "search_url": res.get("search_url"),
                    }

        crossed = self._cross_reference(all_items, trends_info, query)
        self._enrich_video_likes(crossed["items"])
        for it in crossed["items"]:
            likes = it.get("likes") or 0
            views = it.get("views") or 0
            sold = it.get("sold_count") or 0
            if (likes >= 10_000 or views >= 100_000 or sold >= 500) and "Viral / Validado" not in (it.get("reasons") or []):
                it["validation_score"] = int(it.get("validation_score") or 0) + (20 if likes >= 50_000 else 12)
                it.setdefault("reasons", []).append("Viral / Validado")
        crossed["items"].sort(
            key=lambda x: (x.get("validation_score") or 0, x.get("likes") or 0, x.get("sold_count") or 0, x.get("views") or 0),
            reverse=True,
        )
        return {
            "query": query,
            "country": country,
            "competitor": {
                "page_id": competitor_page_id,
                "author": competitor_author,
                "domain": competitor_domain,
            } if (competitor_page_id or competitor_author or competitor_domain) else None,
            "total": len(crossed["items"]),
            "items": crossed["items"],
            "clusters": crossed["clusters"],
            "trends": trends_info,
            "sources": source_status,
        }

    @staticmethod
    def _enrich_video_likes(items: list[dict[str, Any]]) -> None:
        targets = [
            it for it in items
            if it.get("likes") is None
            and isinstance(it.get("url"), str)
            and any(h in it["url"] for h in ("youtube.com/watch", "youtube.com/shorts/", "tiktok.com/"))
        ]
        if not targets:
            return

        def fetch_one(it: dict[str, Any]) -> None:
            try:
                url = it["url"]
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
                    },
                )
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    html = resp.read(2_000_000).decode("utf-8", errors="replace")
                if "tiktok.com" in url:
                    m_like = re.search(r'"diggCount"\s*:\s*(\d+)', html)
                    m_view = re.search(r'"playCount"\s*:\s*(\d+)', html)
                    if m_like:
                        it["likes"] = int(m_like.group(1))
                    if m_view and not it.get("views"):
                        it["views"] = int(m_view.group(1))
                else:
                    m_like = (
                        re.search(r'"likeCount"\s*:\s*"?([\d.,]+)"?', html)
                        or re.search(r'like this video along with ([\d.,]+) other people', html)
                        or re.search(r'"accessibilityText"\s*:\s*"([^"]+?)\s+(?:likes?|gostei)"', html, re.IGNORECASE)
                    )
                    if m_like:
                        it["likes"] = parse_compact_number(m_like.group(1))
                    if it.get("views") is None:
                        m_view = re.search(r'"viewCount"\s*:\s*"(\d+)"', html)
                        if m_view:
                            it["views"] = int(m_view.group(1))
            except Exception:
                pass

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futs = [pool.submit(fetch_one, it) for it in targets[:24]]
            for f in futs:
                try:
                    f.result(timeout=6)
                except Exception:
                    pass

    def download_media(self, body: dict[str, Any]) -> dict[str, Any]:
        video_url = str(body.get("video_url") or body.get("url") or "").strip()
        thumb_url = str(body.get("thumbnail") or "").strip()
        title = _slug(str(body.get("title") or "criativo"), 40)
        media_type = str(body.get("media_type") or "video").strip()
        target_url = video_url or thumb_url
        if not target_url.startswith(("http://", "https://")):
            raise FlowError("SCHEMA_VALIDATION", "URL de mídia inválida para download.", pointer="/url")

        short_id = hashlib.sha1(target_url.encode("utf-8")).hexdigest()[:8]
        # Direct image or HTTP mp4 download first; yt-dlp handles watch URLs (YouTube/TikTok/IG)
        is_direct_file = bool(re.search(r"\.(?:mp4|mov|webm|jpg|jpeg|png|webp)(?:\?|$)", target_url, re.IGNORECASE))
        if media_type != "video" or (is_direct_file and "youtube.com" not in target_url and "youtu.be" not in target_url):
            ext = ".mp4" if media_type == "video" else ".jpg"
            m_ext = re.search(r"\.(mp4|mov|webm|jpg|jpeg|png|webp)(?:\?|$)", target_url, re.IGNORECASE)
            if m_ext:
                ext = "." + m_ext.group(1).lower()
            out_path = self.media_dir / f"{title}-{short_id}{ext}"
            if not out_path.is_file():
                req = urllib.request.Request(target_url, headers={"User-Agent": "Mozilla/5.0"})
                try:
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        out_path.write_bytes(resp.read())
                except Exception as exc:
                    raise FlowError("LOCAL_FAILURE", f"Falha ao baixar mídia: {exc}", status=502) from exc
        else:
            py = self._sys_python()
            if not py:
                raise FlowError("DEPENDENCY_UNAVAILABLE", "Python local não encontrado para yt-dlp.", status=503)
            out_tmpl = str(self.media_dir / f"{title}-{short_id}.%(ext)s")
            ffmpeg_bin = shutil.which("ffmpeg")
            if not ffmpeg_bin:
                local = os.environ.get("LOCALAPPDATA")
                if local:
                    cand = next((Path(local) / "Microsoft" / "WinGet" / "Packages").glob("*FFmpeg*/**/bin/ffmpeg.exe"), None)
                    if cand and cand.is_file():
                        ffmpeg_bin = str(cand)
            code = r"""
import json, shutil, sys, yt_dlp
url, tmpl, ff_loc = sys.argv[1], sys.argv[2], sys.argv[3]
opts = {
    "quiet": True,
    "no_warnings": True,
    "outtmpl": tmpl,
    "format": "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=720]+ba/b[height<=720][ext=mp4]/b",
    "merge_output_format": "mp4",
    "noplaylist": True,
    "retries": 2,
    "fragment_retries": 2,
}
for rt, exe in (("deno", "deno"), ("node", "node"), ("bun", "bun"), ("quickjs", "qjs")):
    p = shutil.which(exe)
    if p:
        opts["js_runtimes"] = {rt: {"path": p}}
        break
if ff_loc:
    opts["ffmpeg_location"] = ff_loc
with yt_dlp.YoutubeDL(opts) as ydl:
    info = ydl.extract_info(url, download=True)
    path = ydl.prepare_filename(info)
print(json.dumps({"path": path}, ensure_ascii=False))
"""
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            r = subprocess.run(
                [str(py), "-c", code, target_url, out_tmpl, str(Path(ffmpeg_bin).parent) if ffmpeg_bin else ""],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if r.returncode != 0 or not r.stdout.strip():
                raise FlowError("LOCAL_FAILURE", "Não foi possível baixar o vídeo via yt-dlp.", provider_message=(r.stderr or r.stdout)[-800:])
            raw_path = Path(json.loads(r.stdout.strip().splitlines()[-1])["path"])
            out_path = raw_path if raw_path.is_file() else next(self.media_dir.glob(f"{title}-{short_id}.*"), raw_path)
            if not out_path.is_file():
                raise FlowError("ARTIFACT_NOT_FOUND", "Arquivo baixado não encontrado no disco.")

        return {
            "name": out_path.name,
            "local_path": str(out_path.resolve()),
            "media_url": f"/media/spy/media/{out_path.name}",
            "media_type": "video" if out_path.suffix.lower() in (".mp4", ".mov", ".webm") else "image",
            "size_bytes": out_path.stat().st_size,
        }

    def download_and_transcribe(self, body: dict[str, Any]) -> dict[str, Any]:
        """Download (or reuse) a creative video via vdown/yt-dlp patterns and run local Whisper transcription."""
        local_name = str(body.get("local_filename") or "").strip()
        if local_name:
            path = self.verified_media_path("media", local_name)
            dl = {
                "name": path.name,
                "local_path": str(path),
                "media_url": f"/media/spy/media/{path.name}",
                "media_type": "video",
                "size_bytes": path.stat().st_size,
            }
        else:
            dl = self.download_media({**body, "media_type": "video"})
            path = Path(dl["local_path"])

        if dl["media_type"] != "video":
            raise FlowError("SCHEMA_VALIDATION", "Este criativo é imagem; selecione um vídeo para transcrever.")
        if self.editor is None:
            raise FlowError("DEPENDENCY_UNAVAILABLE", "Serviço de transcrição Whisper indisponível.", status=503)

        model_size = str(body.get("model_size") or "small").strip()
        try:
            tr = self.editor._transcribe(path, model_size=model_size)
        except Exception:
            tr = self.editor._transcribe(path, model_size="base")

        segments = tr.get("segments") or []
        full_text = "\n".join(str(s.get("text") or "").strip() for s in segments if str(s.get("text") or "").strip()).strip()
        transcript_payload = {
            "filename": path.name,
            "language": tr.get("language") or "pt",
            "text": full_text,
            "segments": segments,
        }
        tr_file = path.with_suffix(".transcript.json")
        tr_file.write_text(json.dumps(transcript_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**dl, **transcript_payload}

    def get_gpt_config(self) -> dict[str, str]:
        return {
            "project_url": (self.repository.get_setting("gpt_project_url") if self.repository else "") or "",
            "instructions": (self.repository.get_setting("gpt_offer_instructions") if self.repository else "") or "",
            "monitor_keywords": (self.repository.get_setting("spy_monitor_keywords") if self.repository else "") or "natal, skincare, organizador",
        }

    def save_gpt_config(self, body: dict[str, Any]) -> dict[str, str]:
        if self.repository is not None:
            if "project_url" in body and body["project_url"] is not None:
                self.repository.set_setting("gpt_project_url", str(body["project_url"]).strip())
            if "instructions" in body and body["instructions"] is not None:
                self.repository.set_setting("gpt_offer_instructions", str(body["instructions"]).strip())
            if "monitor_keywords" in body and body["monitor_keywords"] is not None:
                self.repository.set_setting("spy_monitor_keywords", str(body["monitor_keywords"]).strip())
        return self.get_gpt_config()

    def build_offer_prompt(self, body: dict[str, Any]) -> dict[str, str]:
        page_filename = str(body.get("page_filename") or "").strip()
        transcript = str(body.get("transcript") or "").strip()
        product_hint = str(body.get("product") or body.get("query") or "").strip()
        user_goal = str(body.get("goal") or "").strip()
        page_copy = ""
        if page_filename:
            page_path = self.verified_media_path("pages", page_filename)
            page_copy = extract_page_copy_summary(page_path.read_text(encoding="utf-8", errors="replace"), max_chars=4500)
        cfg = self.get_gpt_config()
        extra_rules = str(body.get("instructions") or cfg.get("instructions") or "").strip()

        prompt = (
            "Você é um estrategista sênior de Direct Response e criativos em vídeo (TikTok/Meta Ads + Google Flow).\n"
            "Analise a oferta abaixo, melhore a concepção comercial (promessa, mecanismo único, dor, objeções, CTA) "
            "e devolva UM ÚNICO JSON válido com exatamente as chaves:\n"
            "1. `offer_analysis`: objeto com `headline_melhorada`, `subheadline`, `mecanismo_unico`, `oferta_irresistivel`, `copy_anuncio`, `roteiro_ugc`.\n"
            "2. `flow_spec`: JSON compatível com FlowLocal (`version`: 1, `name`, `inputs`: [slot `foto_produto` type `image` + `direcao_visual` type `text`], "
            "`video_defaults`: {`model_id`: \"omni-1.2\", `aspect`: \"9:16\", `duration_seconds`: 6, `resolution`: \"720p\"}, "
            "e `nodes`: primeiro os nodes `image_gen` (sheet do produto, cenário, cenas 01..N usando `@input.foto_produto` e `@node.<id>.asset_id`) "
            "e por último 1 node `video_batch` (`depends_on` com os IDs das cenas) contendo `items` para gerar TODOS os vídeos baseados nas imagens criadas).\n"
        )
        if extra_rules:
            prompt += f"\nRegras adicionais do usuário:\n{extra_rules}\n"
        if product_hint:
            prompt += f"\nProduto / Nicho: {product_hint}\n"
        if user_goal:
            prompt += f"\nObjetivo / Ajustes solicitados: {user_goal}\n"
        if transcript:
            prompt += f"\nTranscrição do vídeo de referência:\n\"\"\"\n{transcript[:3500]}\n\"\"\"\n"
        if page_copy:
            prompt += f"\nConteúdo extraído da página clonada ({page_filename}):\n\"\"\"\n{page_copy}\n\"\"\"\n"
        return {"prompt": prompt, "page_copy_chars": str(len(page_copy)), "project_url": cfg.get("project_url", "")}

    def offer_to_flow(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.gpt_chat is None:
            raise FlowError("DEPENDENCY_UNAVAILABLE", "Serviço ChatGPT Web indisponível.", status=503)
        built = self.build_offer_prompt(body)
        project = str(body.get("project_url") or built.get("project_url") or "").strip() or None
        conversation = str(body.get("conversation_id") or "").strip() or None
        res = self.gpt_chat.ask_json(
            built["prompt"],
            new_chat=not bool(conversation),
            conversation=conversation,
            project=project,
            timeout=180,
        )
        payload = res.get("json")
        if not isinstance(payload, dict):
            raise FlowError("GPT_INVALID_JSON", "O ChatGPT não retornou um objeto JSON com offer_analysis e flow_spec.")
        flow_spec = payload.get("flow_spec") or (payload if "nodes" in payload else None)
        if not isinstance(flow_spec, dict) or not isinstance(flow_spec.get("nodes"), list):
            raise FlowError("GPT_INVALID_JSON", "JSON sem `flow_spec.nodes` válido para montar a fila de imagens e vídeos.")
        validated_preview = self.flow_runner.preview(flow_spec) if self.flow_runner is not None else {"spec": flow_spec}
        return {
            "offer_analysis": payload.get("offer_analysis") or {},
            "flow_spec": validated_preview.get("spec") or flow_spec,
            "preview": validated_preview,
            "conversation_id": res.get("conversation_id"),
            "conversation_url": res.get("conversation_url"),
            "prompt": built["prompt"],
        }

    @staticmethod
    def _parse_html_summary(raw_html: str) -> dict[str, Any]:
        title_m = re.search(r"<title[^>]*>([\s\S]*?)</title>", raw_html, re.IGNORECASE)
        title = html_lib.unescape(re.sub(r"\s+", " ", title_m.group(1)).strip()) if title_m else ""
        headlines = []
        for hm in re.finditer(r"<h[12][^>]*>([\s\S]*?)</h[12]>", raw_html, re.IGNORECASE):
            txt = html_lib.unescape(re.sub(r"<[^>]+>", " ", hm.group(1)))
            txt = re.sub(r"\s+", " ", txt).strip()
            if 3 <= len(txt) <= 180 and txt not in headlines:
                headlines.append(txt)
            if len(headlines) >= 8:
                break
        cleaned = re.sub(r"<(?:script|style|noscript)[^>]*>[\s\S]*?</(?:script|style|noscript)>", " ", raw_html, flags=re.IGNORECASE)
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", cleaned))
        text = re.sub(r"\s+", " ", text).strip()
        prices = []
        for pm in re.finditer(r"(?:R\$|\$|US\$)\s?\d{1,4}(?:[.,]\d{2,3})*", text):
            val = pm.group(0).strip()
            if val not in prices:
                prices.append(val)
            if len(prices) >= 8:
                break
        imgs = []
        for im in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', raw_html, re.IGNORECASE):
            src = im.group(1).strip()
            if src.startswith(("http://", "https://")) and src not in imgs:
                imgs.append(src)
            if len(imgs) >= 12:
                break
        return {"title": title[:160], "headlines": headlines, "prices": prices, "excerpt": text[:1600], "images": imgs}

    def capture_page(self, url: str) -> dict[str, Any]:
        clean_url = (url or "").strip()
        if not clean_url.startswith(("http://", "https://")):
            raise FlowError("SCHEMA_VALIDATION", "Informe uma URL válida (https://...) para copiar a página.", pointer="/url")
        slug = _slug(urllib.parse.urlparse(clean_url).netloc or "pagina", 36)
        short = hashlib.sha1(clean_url.encode("utf-8")).hexdigest()[:8]
        out_html = self.pages_dir / f"{slug}-{short}.html"

        sf_cmd = self._single_file_cmd()
        chrome = self._chrome_exe()
        used_tool = "http"
        if sf_cmd and chrome:
            cmd = sf_cmd + ["--browser-executable-path", str(chrome), "--browser-wait-until", "load", clean_url, str(out_html)]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=75, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                if r.returncode == 0 and out_html.is_file() and out_html.stat().st_size > 200:
                    used_tool = "single-file"
            except (OSError, subprocess.SubprocessError):
                pass

        if used_tool != "single-file":
            try:
                req = urllib.request.Request(clean_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                out_html.write_text(raw, encoding="utf-8")
            except Exception as exc:
                raise FlowError("LOCAL_FAILURE", f"Não foi possível copiar a página: {exc}", status=502) from exc

        parsed = self._parse_html_summary(out_html.read_text(encoding="utf-8", errors="replace"))
        return {
            "url": clean_url,
            "tool": used_tool,
            "filename": out_html.name,
            "html_path": str(out_html.resolve()),
            "download_url": f"/media/spy/pages/{out_html.name}",
            "size_bytes": out_html.stat().st_size,
            **parsed,
        }

    def save_edited_page(self, filename: str | None, raw_html: str | None) -> dict[str, Any]:
        if not isinstance(raw_html, str) or len(raw_html.strip()) < 20:
            raise FlowError("SCHEMA_VALIDATION", "Conteúdo HTML vazio ou inválido.", pointer="/html")
        base_name = Path(str(filename or "pagina-clonada.html").strip()).name
        stem = _slug(Path(base_name).stem.removesuffix("-editada"), 40) or "pagina"
        out_name = f"{stem}-editada.html"
        target = (self.pages_dir / out_name).resolve()
        if not target.is_relative_to(self.pages_dir):
            raise FlowError("INVALID_ARTIFACT", "Caminho de página inválido.")
        target.write_text(raw_html, encoding="utf-8")
        parsed = self._parse_html_summary(raw_html)
        return {
            "filename": target.name,
            "html_path": str(target),
            "download_url": f"/media/spy/pages/{target.name}",
            "size_bytes": target.stat().st_size,
            **parsed,
        }

    def list_downloads(self) -> dict[str, Any]:
        media = []
        for p in sorted(self.media_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file() and not p.name.endswith(".transcript.json"):
                tr_file = p.with_suffix(".transcript.json")
                tr_text = ""
                if tr_file.is_file():
                    try:
                        tr_text = str(json.loads(tr_file.read_text(encoding="utf-8")).get("text") or "")
                    except Exception:
                        tr_text = ""
                media.append({
                    "name": p.name,
                    "local_path": str(p.resolve()),
                    "media_url": f"/media/spy/media/{p.name}",
                    "media_type": "video" if p.suffix.lower() in (".mp4", ".mov", ".webm") else "image",
                    "size_bytes": p.stat().st_size,
                    "transcript": tr_text,
                })
        pages = []
        for p in sorted(self.pages_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file():
                pages.append({
                    "name": p.name,
                    "html_path": str(p.resolve()),
                    "download_url": f"/media/spy/pages/{p.name}",
                    "size_bytes": p.stat().st_size,
                })
        return {"media": media[:40], "pages": pages[:25]}
