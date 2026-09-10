"""Offline learning pack. No localhost, no remote scripts, no fake article bodies."""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
from typing import Any

from wechat_export.insights.store import InsightStore
from wechat_export.learning.importer import get_item, list_items
from wechat_export.link_urls import safe_web_url


def export_learning_pack(store: InsightStore, dest: Path, *, archive_root: Path | None = None) -> dict[str, Any]:
    if dest.exists():
        raise FileExistsError(dest)
    dest.mkdir(parents=True)
    items = list_items(store)
    articles = dest / "articles"
    notes_dir = dest / "notes"
    review_dir = dest / "review"
    summaries_dir = dest / "summaries"
    media_dir = dest / "media"
    for folder in (articles, notes_dir, review_dir, summaries_dir, media_dir):
        folder.mkdir()
    bundled = 0
    bundle = None
    if archive_root is not None:
        from wechat_export.bundle_media import BundleMedia

        bundle = BundleMedia(archive_root)
    catalog = []
    for preview in items:
        item = get_item(store, preview["item_id"], archive_root=archive_root)
        catalog.append(
            {
                "item_id": item["item_id"],
                "kind": item["kind"],
                "title": item["display_title"],
                "reading_state": item["reading_state"],
                "content_state": item["content_state"],
                "topics": item["topics"],
                "sources": [
                    {
                        "record_uid": s["record_uid"],
                        "saved_at": s["saved_at"],
                        "original_url": s["original_url"],
                        "saved_comment": s.get("saved_comment"),
                    }
                    for s in item["sources"]
                ],
            }
        )
        body = None
        if item["contents"]:
            body = item["contents"][-1].get("body")
        media_copied: list[dict[str, Any]] = []
        if bundle is not None:
            for source in item["sources"]:
                copied = bundle.include(source["record_uid"], dest)
                if copied and copied.get("relative_path"):
                    bundled += 1
                    media_copied.append(copied)
            if media_copied:
                catalog[-1]["media"] = media_copied
        article_lines = [f"# {item['display_title']}", "", f"content_state: {item['content_state']}", ""]
        for source in item["sources"]:
            if source.get("saved_comment"):
                article_lines.append(f"收藏备注：{source['saved_comment']}")
                article_lines.append("")
            if source.get("original_url"):
                article_lines.append(f"原链接：{source['original_url']}")
                article_lines.append("")
        for copied in media_copied:
            rel = copied.get("relative_path")
            if not rel:
                continue
            mime = str(copied.get("mime") or "")
            name = copied.get("filename") or rel
            if mime.startswith("image/") or str(copied.get("kind") or "") == "image":
                article_lines.append(f"![{name}](../{rel})")
            else:
                article_lines.append(f"[{name}](../{rel})")
            article_lines.append("")
        if body:
            for i, para in enumerate(item["contents"][-1].get("paragraphs") or [body], start=1):
                article_lines.append(f"<!-- p{i} -->")
                article_lines.append(para)
                article_lines.append("")
        elif not media_copied:
            article_lines.append("正文尚未取得。不能根据标题生成摘要。")
        (articles / f"{item['item_id']}.md").write_text("\n".join(article_lines), encoding="utf-8")
        (articles / f"{item['item_id']}.html").write_text(
            _article_html(item, media_copied, body),
            encoding="utf-8",
        )
        if item["notes"]:
            note_text = "\n\n".join(n["user_text"] for n in item["notes"])
            (notes_dir / f"{item['item_id']}.md").write_text(note_text + "\n", encoding="utf-8")
        if item["review_cards"]:
            cards = "\n\n".join(f"Q: {c['question']}\nA: {c['answer']}" for c in item["review_cards"])
            (review_dir / f"{item['item_id']}.md").write_text(cards + "\n", encoding="utf-8")
        for summary in item["summaries"]:
            if summary["status"] in {"ok", "excerpt", "draft"}:
                (summaries_dir / f"{item['item_id']}.md").write_text(json.dumps(json.loads(summary["claims_json"]), ensure_ascii=False, indent=2), encoding="utf-8")
    (dest / "items.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in catalog) + ("\n" if catalog else ""), encoding="utf-8")
    (dest / "目录.md").write_text(
        "# 目录\n\n" + "\n".join(f"- {row['title']} ({row['content_state']})" for row in catalog) + "\n",
        encoding="utf-8",
    )
    (dest / "先读我.md").write_text(
        "# 学习资料包\n\n这些条目来自本机聊天档案中指定的收藏会话。收藏不等于认同作者观点。没有正文的条目不能当作已读文章。\n",
        encoding="utf-8",
    )
    index_html = _index_html(catalog)
    (dest / "开始阅读.html").write_text(index_html, encoding="utf-8")
    manifest = {
        "kind": "learning_pack",
        "item_count": len(catalog),
        "media_count": bundled,
        "source_kind": "live-db",
        "backup2_coverage": "unverified",
        "engine_required": False,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksums = []
    for path in sorted(p for p in dest.rglob("*") if p.is_file()):
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        checksums.append(f"{digest}  {path.relative_to(dest).as_posix()}")
    (dest / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    return {"path": str(dest), "item_count": len(catalog), "media_count": bundled}


def _media_html(media: list[dict[str, Any]], *, prefix: str) -> str:
    parts = []
    for copied in media:
        rel = copied.get("relative_path")
        if not rel:
            continue
        href = html.escape(f"{prefix}{rel}")
        name = html.escape(str(copied.get("filename") or rel))
        mime = str(copied.get("mime") or "")
        kind = str(copied.get("kind") or "")
        if mime.startswith("image/") or kind == "image":
            parts.append(f'<p><img src="{href}" alt="{name}"></p><p><a href="{href}" download="{name}">下载图片</a></p>')
        elif mime.startswith("video/") or kind == "video":
            parts.append(f'<p><video controls src="{href}"></video></p><p><a href="{href}" download="{name}">下载视频</a></p>')
        elif mime.startswith("audio/") or kind == "voice":
            parts.append(f'<p><audio controls src="{href}"></audio></p><p><a href="{href}" download="{name}">下载音频</a></p>')
        else:
            parts.append(f'<p><a href="{href}" download="{name}">{name}</a></p>')
    return "".join(parts)


def _article_html(item: dict[str, Any], media: list[dict[str, Any]], body: str | None) -> str:
    title = html.escape(item["display_title"])
    blocks = [f"<h1>{title}</h1>"]
    for source in item.get("sources") or []:
        if source.get("saved_comment"):
            blocks.append(f"<p>收藏备注：{html.escape(str(source['saved_comment']))}</p>")
        if source.get("original_url"):
            url=safe_web_url(source['original_url'])
            if url:blocks.append('<p><a href="'+html.escape(url,quote=True)+'" target="_blank" rel="noopener noreferrer">打开原网页（外部网站）</a></p>')
    blocks.append(_media_html(media, prefix="../"))
    if body:
        blocks.append(f"<pre>{html.escape(body)}</pre>")
    elif not media:
        blocks.append("<p>正文尚未取得。不能根据标题生成摘要。</p>")
    if item.get('notes'):
        blocks.append('<h2>我的笔记</h2>')
        for note in item['notes']:blocks.append('<pre>'+html.escape(note['user_text'])+'</pre>')
    for summary in item.get('summaries') or []:
        if item.get('active_content_id') and summary['content_id']!=item['active_content_id']:continue
        blocks.append('<h2>'+('AI学习草稿（含义待核对）' if summary['status']=='draft' else '正文摘录')+'</h2>')
        for claim in json.loads(summary['claims_json'] or '[]'):
            blocks.append('<p>'+html.escape(claim.get('text',''))+'</p>')
            if claim.get('quote'):blocks.append('<blockquote>'+html.escape(claim['quote'])+'</blockquote>')
    for card in item.get('review_cards') or []:
        if item.get('active_content_id') and card['content_id']!=item['active_content_id']:continue
        blocks.append('<details><summary>'+html.escape(card['question'])+'</summary><p>'+html.escape(card['answer'])+'</p></details>')
    return (
        "<!DOCTYPE html><html lang=\"zh-Hans\"><head><meta charset=\"utf-8\">"
        "<meta name=\"referrer\" content=\"no-referrer\">"
        f"<title>{title}</title>"
        "<style>body{font-family:-apple-system,sans-serif;max-width:720px;margin:24px auto;padding:0 16px;color:#202b24;background:#f4f6f2}img,video{max-width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}details{padding:12px 0}</style>"
        "</head><body>"
        + "".join(blocks)
        + "</body></html>"
    )


def _index_html(catalog: list[dict[str, Any]]) -> str:
    rows = []
    for row in catalog:
        title = html.escape(row["title"])
        state = html.escape(_state_label(row.get("content_state") or ""))
        href = html.escape(f"articles/{row['item_id']}.html")
        comments = [
            html.escape(str(source.get("saved_comment")))
            for source in row.get("sources") or []
            if source.get("saved_comment")
        ]
        comment_html = "".join(f"<p>收藏备注：{text}</p>" for text in comments)
        media_html = _media_html(row.get("media") or [], prefix="")
        rows.append(
            f"<section><h2><a href=\"{href}\">{title}</a></h2><p>{state}</p>{comment_html}{media_html}</section>"
        )
    return (
        "<!DOCTYPE html><html lang=\"zh-Hans\"><head><meta charset=\"utf-8\">"
        "<meta name=\"referrer\" content=\"no-referrer\">"
        "<title>学习资料</title>"
        "<style>body{font-family:-apple-system,sans-serif;max-width:720px;margin:24px auto;padding:0 16px;color:#202b24;background:#f4f6f2}img,video{max-width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}details{padding:12px 0}section{margin:24px 0;padding:16px 0;border-bottom:1px solid #dfe5dc}</style>"
        "</head><body><h1>学习资料</h1><p>离线目录。图片和文件都在本包内，不需要联网或打开 JSON。</p>"
        + "".join(rows)
        + "</body></html>"
    )


def _state_label(state: str) -> str:
    return {
        "has_body": "有正文",
        "has_attachment": "有附件",
        "attachment_missing": "附件未找到",
        "title_only": "仅标题",
    }.get(state, state)
