"""Visuals for the five point.

* flow_png()        process flow with the bottleneck marked, drawn from requester input
* generate_example() photorealistic EXAMPLE image via an image API, stamped in the pixels
* reference_search() / reference_fetch()  freely-licensed reference photos from Wikimedia
                     Commons with a full citation; original pixels untouched, label band added
Every non-site image is labeled as an example. Nothing is presented as the actual asset.
"""
import base64
import datetime as dt
import html
import io
import json
import re
import urllib.parse
import urllib.request

import config

UA = "Groundwork-CapitalIntake/1.0 (internal drafting aid)"
EXAMPLE_LABEL = "EXAMPLE IMAGE · AI-generated illustration · not a photo of the actual site or asset"
REFERENCE_LABEL = "REFERENCE EXAMPLE · not the actual site or asset"
FREE_LICENSE = re.compile(r"^(cc0|public domain|pd|cc by(-sa)? \d)", re.I)


class ImageError(Exception):
    pass


def _font(size, bold=False):
    from PIL import ImageFont
    for path in (f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _wrap(draw, text, font, width):
    out, line = [], ""
    for w in str(text).split():
        t = (line + " " + w).strip()
        if draw.textlength(t, font=font) <= width:
            line = t
        else:
            out.append(line)
            line = w
    return out + ([line] if line else [])


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# --------------------------------------------------------------------------- process flow
def flow_png(steps, title="Process flow"):
    from PIL import Image, ImageDraw
    steps = [s for s in steps if str(s.get("name") or "").strip()][:12]
    if len(steps) < 2:
        raise ImageError("Add at least two steps to draw the flow.")
    per_row, bw, bh, gx, gy, mx = 4, 300, 150, 80, 110, 60
    rows = (len(steps) + per_row - 1) // per_row
    W = mx * 2 + per_row * bw + (per_row - 1) * gx
    H = 120 + rows * (bh + gy) - gy + 70
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    navy, red, grey = (24, 39, 65), (198, 70, 46), (107, 120, 144)
    d.text((mx, 34), title, fill=navy, font=_font(30, True))
    d.text((mx, 74), "Prepared from requester input. Red marks the bottleneck.", fill=grey, font=_font(18))
    pos = []
    for i, s in enumerate(steps):
        r, c = divmod(i, per_row)
        if r % 2:
            c = per_row - 1 - c                      # snake so arrows stay short
        x, y = mx + c * (bw + gx), 120 + r * (bh + gy)
        pos.append((x, y))
        bott = bool(s.get("bottleneck"))
        d.rounded_rectangle([x, y, x + bw, y + bh], 18, fill=(253, 232, 228) if bott else (238, 243, 250),
                            outline=red if bott else navy, width=4 if bott else 2)
        d.text((x + 16, y + 12), f"{i + 1:02d}", fill=red if bott else grey, font=_font(18, True))
        if bott:
            tag = "BOTTLENECK"
            tw = d.textlength(tag, font=_font(15, True))
            d.rounded_rectangle([x + bw - tw - 30, y + 10, x + bw - 12, y + 36], 10, fill=red)
            d.text((x + bw - tw - 21, y + 14), tag, fill="white", font=_font(15, True))
        for j, ln in enumerate(_wrap(d, s.get("name"), _font(21, True), bw - 32)[:2]):
            d.text((x + 16, y + 44 + j * 28), ln, fill=navy, font=_font(21, True))
        meta = " · ".join(x for x in (str(s.get("duration") or "").strip(), str(s.get("volume") or "").strip()) if x)
        if meta:
            d.text((x + 16, y + bh - 34), meta[:40], fill=red if bott else grey, font=_font(17))
        if s.get("note"):
            for j, ln in enumerate(_wrap(d, s["note"], _font(15), bw)[:2]):
                d.text((x, y + bh + 8 + j * 19), ln, fill=grey, font=_font(15))
    for (x1, y1), (x2, y2) in zip(pos, pos[1:]):
        if y1 == y2:
            a, b = (x1 + bw, y1 + bh // 2), (x2, y2 + bh // 2)
            if x2 < x1:
                a, b = (x1, y1 + bh // 2), (x2 + bw, y2 + bh // 2)
        else:
            a, b = (x1 + bw // 2, y1 + bh), (x2 + bw // 2, y2)
        d.line([a, b], fill=navy, width=4)
        ang = (b[0] - a[0], b[1] - a[1])
        n = max(1, (ang[0] ** 2 + ang[1] ** 2) ** 0.5)
        ux, uy = ang[0] / n, ang[1] / n
        d.polygon([b, (b[0] - 16 * ux + 9 * uy, b[1] - 16 * uy - 9 * ux), (b[0] - 16 * ux - 9 * uy, b[1] - 16 * uy + 9 * ux)], fill=navy)
    return _png(img)


# --------------------------------------------------------------------------- labeling
def _band(img, text, sub=""):
    from PIL import Image, ImageDraw
    W = img.width
    f, fs = _font(max(16, W // 55), True), _font(max(13, W // 80))
    h = int(f.size * 2.2 + (fs.size * 1.4 if sub else 0))
    out = Image.new("RGB", (W, img.height + h), (20, 31, 51))
    out.paste(img, (0, h))
    d = ImageDraw.Draw(out)
    d.text((16, int(f.size * 0.5)), text, fill=(244, 178, 76), font=f)
    if sub:
        d.text((16, int(f.size * 1.7)), sub[:160], fill=(220, 228, 241), font=fs)
    return out


def stamp_example(raw: bytes):
    """AI-generated: label band AND a diagonal watermark in the pixels, so it survives copy/paste."""
    from PIL import Image, ImageDraw
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img.thumbnail((1600, 1600))
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    f = _font(max(40, img.width // 9), True)
    tw = d.textlength("EXAMPLE", font=f)
    tile = Image.new("RGBA", (int(tw) + 40, f.size + 40), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((20, 10), "EXAMPLE", fill=(255, 255, 255, 70), font=f)
    tile = tile.rotate(28, expand=True)
    over.paste(tile, ((img.width - tile.width) // 2, (img.height - tile.height) // 2), tile)
    img = Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")
    return _png(_band(img, EXAMPLE_LABEL))


def label_reference(raw: bytes, citation_short: str):
    from PIL import Image
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    return _png(_band(img, REFERENCE_LABEL, citation_short))


# --------------------------------------------------------------------------- AI example images
def image_ready(cfg=None):
    cfg = cfg or config.load()
    return cfg.get("image_provider") in ("openai", "azure_openai") and bool(
        cfg.get("image_api_key") or (cfg.get("provider") == cfg.get("image_provider") and cfg.get("api_key")))


def generate_example(subject: str, cfg=None):
    cfg = cfg or config.load()
    if not image_ready(cfg):
        raise ImageError("Image generation isn't configured. An admin can enable it under Settings → Images.")
    subject = re.sub(r"\s+", " ", subject or "").strip()[:300]
    if len(subject) < 4:
        raise ImageError("Describe what the example image should show.")
    prompt = ("Photorealistic, generic example photograph for an internal business document. "
              f"Subject: {subject}. Realistic lighting and materials. No logos, no brand names, no readable "
              "text, no identifiable faces, no airline livery.")
    key = cfg.get("image_api_key") or cfg.get("api_key")
    if cfg["image_provider"] == "azure_openai":
        url = (f"{cfg.get('image_base_url', '').rstrip('/')}/openai/deployments/{cfg.get('image_deployment') or cfg.get('image_model')}"
               f"/images/generations?api-version={cfg.get('image_api_version') or '2025-04-01-preview'}")
        headers, body = {"content-type": "application/json", "api-key": key}, {"prompt": prompt, "n": 1, "size": "1536x1024"}
    else:
        url = (cfg.get("image_base_url") or "https://api.openai.com/v1").rstrip("/") + "/images/generations"
        headers = {"content-type": "application/json", "authorization": f"Bearer {key}"}
        body = {"model": cfg.get("image_model") or "gpt-image-1", "prompt": prompt, "n": 1, "size": "1536x1024"}
    try:
        req = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
        with urllib.request.urlopen(req, timeout=int(cfg.get("timeout_seconds") or 120)) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise ImageError(f"Image API returned {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    except Exception as e:
        raise ImageError(f"Image API unreachable: {str(e)[:200]}")
    item = (data.get("data") or [{}])[0]
    if item.get("b64_json"):
        raw = base64.b64decode(item["b64_json"])
    elif item.get("url"):
        with urllib.request.urlopen(urllib.request.Request(item["url"], headers={"User-Agent": UA}), timeout=60) as r:
            raw = r.read()
    else:
        raise ImageError("Image API returned no image.")
    citation = (f"AI-generated example image ({cfg.get('image_model') or 'image model'}), {dt.date.today():%b %d, %Y}. "
                f"Prompt: \"{subject}\". Illustrative only; not the actual site or asset.")
    return stamp_example(raw), citation


# --------------------------------------------------------------------------- reference photos
def _strip(h):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", str(h or "")))).strip()


def reference_search(query: str, limit=8, fetch=None):
    q = re.sub(r"\s+", " ", query or "").strip()[:120]
    if len(q) < 3:
        raise ImageError("Enter a few words to search for.")
    params = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
              "gsrsearch": f"{q} filetype:bitmap", "gsrlimit": 20, "prop": "imageinfo",
              "iiprop": "url|extmetadata|mime", "iiurlwidth": 900}
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    try:
        data = fetch(url) if fetch else json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20).read())
    except Exception as e:
        raise ImageError(f"Wikimedia Commons unreachable: {str(e)[:200]}")
    out = []
    for p in sorted((data.get("query") or {}).get("pages", {}).values(), key=lambda x: x.get("index", 0)):
        ii = (p.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata") or {}
        lic = _strip((md.get("LicenseShortName") or {}).get("value"))
        if not FREE_LICENSE.search(lic) or re.search(r"\bnc\b|\bnd\b", lic, re.I) or "image/" not in ii.get("mime", "image/"):
            continue
        out.append({"title": _strip((md.get("ObjectName") or {}).get("value")) or p.get("title", "").replace("File:", ""),
                    "thumb": ii.get("thumburl") or ii.get("url"), "page": ii.get("descriptionurl"),
                    "artist": _strip((md.get("Artist") or {}).get("value")) or "Unknown author",
                    "license": lic, "license_url": _strip((md.get("LicenseUrl") or {}).get("value")),
                    "date": _strip((md.get("DateTimeOriginal") or {}).get("value"))[:10]})
        if len(out) >= limit:
            break
    return out


def citation_for(c):
    return (f"\"{c['title']}\" by {c['artist']}, {c['license']}"
            f"{' (' + c['license_url'] + ')' if c.get('license_url') else ''}, via Wikimedia Commons: {c['page']}. "
            f"Retrieved {dt.date.today():%b %d, %Y}. Label band added by Groundwork; image otherwise unmodified.")


def reference_fetch(c, fetch_bytes=None):
    if not str(c.get("thumb", "")).startswith("https://upload.wikimedia.org/"):
        raise ImageError("Only Wikimedia Commons images can be imported.")
    try:
        raw = fetch_bytes(c["thumb"]) if fetch_bytes else urllib.request.urlopen(
            urllib.request.Request(c["thumb"], headers={"User-Agent": UA}), timeout=30).read()
    except Exception as e:
        raise ImageError(f"Couldn't download the image: {str(e)[:200]}")
    short = f"{c['title'][:60]} · {c['artist'][:40]} · {c['license']} · Wikimedia Commons"
    return label_reference(raw, short), citation_for(c)


def upload_label(raw: bytes, mime: str):
    from PIL import Image
    if len(raw) > 6 * 1024 * 1024:
        raise ImageError("Images must be under 6 MB.")
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        raise ImageError("That file isn't a readable image.")
    img.thumbnail((1800, 1800))
    return _png(img)
