"""Live vision-quality probe for the LumaField blinded assessment.

Downloads real streetlight photographs from Wikimedia Commons, normalizes them
through the project's own media pipeline, and runs the real vision adapter
against candidate models. Prints one compact verdict per (photo, model) pair.

Usage: .venv/Scripts/python scripts/test_vision_quality.py
"""
import sys
import time

import httpx

sys.path.insert(0, ".")
sys.path.insert(0, "backend")
from app.adapters.vision import GoogleVision, OpenRouterVision  # noqa: E402
from app.media import normalize_image  # noqa: E402
from scripts.setup_airtable import load_env  # noqa: E402

SEARCHES = {
    "LED (expected)": '"LED streetlight"',
    "LEGACY (expected)": '"sodium vapor streetlight"',
}
MODELS = ["gemini-3.6-flash", "gemini-3-flash-preview"]
PHOTOS_PER_QUERY = 3
SKIP_NAMES = ("shattered", "broken", "damaged", "vandal", "crushed")

UA = "LumaFieldTakehome/0.1 (https://github.com/brco89/lumafield) python-httpx"


def commons_photos(query, limit):
    search = httpx.get("https://commons.wikimedia.org/w/api.php", params={
        "action": "query", "generator": "search", "gsrsearch": query,
        "gsrnamespace": 6, "gsrlimit": 10, "prop": "imageinfo", "iiprop": "url|size|mime",
        "format": "json",
    }, headers={"User-Agent": UA}, timeout=20).json()
    pages = (search.get("query") or {}).get("pages") or {}
    picked = []
    for page in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        title = page.get("title", "").lower()
        info = (page.get("imageinfo") or [{}])[0]
        if any(word in title for word in SKIP_NAMES):
            continue
        if (info.get("mime") in {"image/jpeg", "image/png"}
                and 400 < info.get("width", 0)
                and 0 < info.get("size", 99 << 20) < 8 * 1024 * 1024):
            picked.append(info["url"])
        if len(picked) >= limit:
            break
    if not picked:
        raise SystemExit(f"No usable Commons photo for: {query}")
    return picked


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    env = load_env()
    provider = env.get("LUMAFIELD_VISION_PROVIDER", "openai")

    def make(model):
        if provider == "openrouter":
            return OpenRouterVision(env["LUMAFIELD_OPENROUTER_API_KEY"], model)
        if provider == "google":
            return GoogleVision(env["LUMAFIELD_GOOGLE_API_KEY"], model)
        raise SystemExit(f"Probe does not support provider {provider}")

    default_model = {"openrouter": env.get("LUMAFIELD_OPENROUTER_MODEL", "openai/gpt-5.5-mini"),
                     "google": env.get("LUMAFIELD_GOOGLE_VISION_MODEL", "gemini-3-flash-preview")}[provider]
    models = [m.strip() for m in env.get("LUMAFIELD_PROBE_MODELS", default_model).split(",") if m.strip()]
    for label, query in SEARCHES.items():
        for url in commons_photos(query, PHOTOS_PER_QUERY):
            name = url.rsplit("/", 1)[-1].split("?")[0][:55]
            print(f"\n=== {label} · {name}")
            normalized, _ = normalize_image(httpx.get(url, timeout=30,
                                        headers={"User-Agent": UA}).content)
            for model in models:
                started = time.time()
                try:
                    result = make(model).assess(normalized)
                    elapsed = time.time() - started
                    cues = "; ".join(result.visual_cues[:3])
                    print(f"  {model:28s} {result.visual_classification:12s} "
                          f"{result.visual_confidence:6s}/{result.evidence_quality:7s} "
                          f"{elapsed:4.1f}s · {cues[:90]}")
                except Exception as error:  # noqa: BLE001 - report and keep probing
                    print(f"  {model:28s} ERROR: {str(error)[:120]}")


if __name__ == "__main__":
    main()
