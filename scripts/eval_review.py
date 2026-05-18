"""Visual review tool for scan-and-identify /identify accuracy.

Walks a folder of scan images, runs each through the local API, and emits an
HTML report sorted by top-1 confidence ascending so the ambiguous ones surface
first. Each row shows the scan side-by-side with the top-K TCGplayer candidates
(their reference images are loaded directly from TCGplayer CDN in the browser).

Usage:
  python3 scripts/eval_review.py --scans scan_images/ --out report.html
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import html
import json
import mimetypes
import time
from pathlib import Path

import httpx


def _data_uri(path: Path) -> str:
    mt, _ = mimetypes.guess_type(path.name)
    if mt is None:
        mt = "image/jpeg"
    return f"data:{mt};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"

REPO_ROOT = Path(__file__).resolve().parent.parent


async def identify_one(
    client: httpx.AsyncClient,
    api_base: str,
    api_key: str,
    image_url: str,
    top_k: int,
) -> dict:
    r = await client.post(
        f"{api_base}/identify",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"image_url": image_url, "top_k": top_k, "rotation_invariant": True},
        timeout=60.0,
    )
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
    return r.json()


async def run(
    scan_files: list[Path],
    scans_root: Path,
    server_port: int,
    api_base: str,
    api_key: str,
    top_k: int,
    concurrency: int,
) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)
    results: list[dict] = []

    async with httpx.AsyncClient() as client:
        async def task(idx: int, path: Path) -> None:
            rel = path.relative_to(scans_root.parent)
            url = f"http://host.docker.internal:{server_port}/{rel.as_posix()}"
            async with sem:
                t0 = time.monotonic()
                resp = await identify_one(client, api_base, api_key, url, top_k)
                elapsed = time.monotonic() - t0
            results.append({
                "idx": idx,
                "path": str(path),
                "rel_url": rel.as_posix(),
                "resp": resp,
                "elapsed_ms": int(elapsed * 1000),
            })
            print(f"  [{idx+1}/{len(scan_files)}] {rel} ({elapsed*1000:.0f}ms)")

        await asyncio.gather(*(task(i, p) for i, p in enumerate(scan_files)))

    return results


def _row_html(r: dict, server_port: int, top_k: int) -> str:
    rel_url = html.escape(r["rel_url"])
    scan_path = Path(r["path"])
    # Inline as data URI so the report is self-contained.
    scan_src = _data_uri(scan_path) if scan_path.exists() else ""
    resp = r["resp"]

    if "error" in resp:
        return f"""
<tr class="error">
  <td><img src="{scan_src}" loading="lazy" /></td>
  <td colspan="2" class="errmsg">ERROR: {html.escape(resp['error'])}</td>
</tr>"""

    cands = resp.get("candidates", [])
    if not cands:
        is_back = resp.get("is_card_back", False)
        msg = "card back detected" if is_back else "no candidates"
        return f"""
<tr class="empty">
  <td><img src="{scan_src}" loading="lazy" /></td>
  <td colspan="2" class="errmsg">{msg}</td>
</tr>"""

    top = cands[0]
    top_score = top["score"]
    gap = top_score - (cands[1]["score"] if len(cands) > 1 else 0.0)

    def cand_html(c: dict, primary: bool) -> str:
        cls = "primary" if primary else "alt"
        return f"""<div class="cand {cls}">
  <img src="{html.escape(c['image_url'])}" loading="lazy" />
  <div class="meta">
    <div class="name">{html.escape(c['name'])}</div>
    <div class="set">{html.escape(c['set_abbr'])} #{html.escape(c.get('collector_number') or '')} · {html.escape(c.get('rarity') or '')}</div>
    <div class="score">{c['score']:.3f}</div>
  </div>
</div>"""

    top_html = cand_html(top, primary=True)
    alt_html = "\n".join(cand_html(c, primary=False) for c in cands[1:top_k])

    conf_class = (
        "high" if top_score > 0.55 and gap > 0.15
        else "low" if top_score < 0.45 or gap < 0.08
        else "mid"
    )

    return f"""
<tr class="{conf_class}" data-score="{top_score:.4f}" data-gap="{gap:.4f}">
  <td class="scan">
    <img src="{scan_src}" loading="lazy" />
    <div class="filename">{rel_url}</div>
    <div class="latency">{r['elapsed_ms']}ms</div>
  </td>
  <td class="top">{top_html}<div class="conf">score {top_score:.3f} · gap {gap:.3f}</div></td>
  <td class="alts">{alt_html}</td>
</tr>"""


def write_html(results: list[dict], out_path: Path, server_port: int, top_k: int) -> None:
    # Sort by top-1 score ascending so suspect rows surface first
    def sort_key(r: dict) -> float:
        cands = r["resp"].get("candidates", []) if "error" not in r["resp"] else []
        return cands[0]["score"] if cands else -1.0

    results_sorted = sorted(results, key=sort_key)

    rows = "\n".join(_row_html(r, server_port, top_k) for r in results_sorted)
    n = len(results)
    errors = sum(1 for r in results if "error" in r["resp"])
    empty = sum(1 for r in results
                if "error" not in r["resp"] and not r["resp"].get("candidates"))
    backs = sum(1 for r in results if r["resp"].get("is_card_back"))

    out_path.write_text(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>scan-and-identify review ({n} scans)</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; padding: 16px; background: #f5f5f5; color: #222; }}
  h1 {{ font-size: 20px; margin: 0 0 8px; }}
  .summary {{ margin-bottom: 16px; font-size: 13px; color: #666; }}
  table {{ border-collapse: collapse; width: 100%; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  td {{ border-bottom: 1px solid #eee; padding: 12px; vertical-align: top; }}
  tr:hover {{ background: #fafafa; }}
  td.scan {{ width: 240px; }}
  td.scan img {{ max-width: 220px; max-height: 300px; display: block; border: 1px solid #ddd; }}
  td.scan .filename {{ font-size: 11px; color: #888; word-break: break-all; margin-top: 6px; font-family: monospace; }}
  td.scan .latency {{ font-size: 11px; color: #aaa; }}
  td.top {{ width: 280px; }}
  .cand {{ display: flex; gap: 10px; margin-bottom: 8px; }}
  .cand.primary {{ }}
  .cand.alt {{ opacity: 0.85; }}
  .cand img {{ max-width: 90px; max-height: 130px; border: 1px solid #ddd; flex-shrink: 0; }}
  .cand .name {{ font-weight: 600; font-size: 13px; }}
  .cand .set {{ font-size: 11px; color: #666; margin-top: 2px; }}
  .cand .score {{ font-size: 12px; color: #444; margin-top: 4px; font-family: monospace; }}
  td.alts .cand img {{ max-width: 60px; max-height: 85px; }}
  td.alts .cand .name {{ font-size: 12px; }}
  .conf {{ font-size: 11px; color: #999; margin-top: 8px; font-family: monospace; }}
  tr.high {{ background: #f0fdf4; }}
  tr.mid  {{ background: #fffbeb; }}
  tr.low  {{ background: #fef2f2; }}
  tr.error {{ background: #fee2e2; }}
  tr.empty {{ background: #f5f5f4; }}
  .errmsg {{ color: #b91c1c; font-family: monospace; font-size: 12px; }}
</style></head>
<body>
<h1>scan-and-identify review — {n} scans</h1>
<div class="summary">
  errors: {errors} · empty candidates: {empty} · card-back detections: {backs}<br>
  Sorted by top-1 score ascending (low-confidence/ambiguous rows first).<br>
  Row colors: <span style="background:#f0fdf4;padding:2px 6px;">high confidence</span>
  <span style="background:#fffbeb;padding:2px 6px;">mid</span>
  <span style="background:#fef2f2;padding:2px 6px;">low</span>
</div>
<table>{rows}</table>
</body></html>
""")
    print(f"\nWrote {out_path} — open it in a browser")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scans", type=Path, default=REPO_ROOT / "scan_images")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "report.html")
    ap.add_argument("--api-base", default="http://localhost:8000")
    ap.add_argument("--api-key", default="local-dev-key-change-me")
    ap.add_argument("--server-port", type=int, default=8765)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    scans_root = args.scans.resolve()
    if not scans_root.exists():
        raise SystemExit(f"scan folder not found: {scans_root}")

    exts = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
    files = sorted(p for p in scans_root.rglob("*") if p.suffix.lower() in exts)
    if not files:
        raise SystemExit(f"no images found under {scans_root}")
    print(f"Found {len(files)} scans. Hitting {args.api_base} with concurrency={args.concurrency}.")

    results = asyncio.run(run(
        scan_files=files,
        scans_root=scans_root,
        server_port=args.server_port,
        api_base=args.api_base,
        api_key=args.api_key,
        top_k=args.top_k,
        concurrency=args.concurrency,
    ))

    # Dump raw results next to the html for later analysis
    (args.out.parent / "results.json").write_text(json.dumps(results, indent=2))
    write_html(results, args.out, args.server_port, args.top_k)


if __name__ == "__main__":
    main()
