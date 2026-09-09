# Feeding this archive to an AI

The export is already structured. Prefer **filtered text**, not the raw 1.3 GiB JSONL dump.

## What to send

Use records where `message_type_normalized` is `text` or `system`, and the `text` field does **not** start with `<msg` or `<?xml`. Those XML blobs are images, voice, and app cards; they waste context and can contain CDN keys.

Good unit of work:

| Goal | Slice |
| --- | --- |
| One relationship | `targets/<name>/messages.jsonl` |
| One month | `targets/<name>/YYYY-MM.md` |
| Search | viewer search, then copy the hits |
| Cross-chat themes | readable text only, grouped by `conversation_id` |

Each JSONL line is one message with `source_kind`, timestamps, sender, and conversation id. Keep those fields so the model can cite.

## Local vs hosted

- **Local / private models** (LM Studio, Ollama, a local agent): point them at `data/exports/…`. Do not copy the folder to a SaaS.
- **Hosted APIs**: upload only the slice you need, strip XML, and assume anything you send can be retained. Never send `data/private/`.

Suggested system preamble:

> You are reading a personal WeChat archive exported as JSONL. `source_kind=live-db` means the current Mac database, not the phone backup. Ignore messages whose text is XML. Answer with dates and sender names from the records. Do not invent chats that are not in the file.

## Chunking

1. Split by `conversation_id`.
2. Then by calendar month (`timestamp_utc`).
3. Keep a running cast list: `sender_display_name` → `sender_id`.
4. Cap each prompt (for example 20–40k tokens of message text) and retrieve the next month if needed.

The SQLite index (`archive.sqlite`) can retrieve `WHERE preview LIKE … AND readable=1` without loading the whole JSONL into the model.

## What not to do

- Do not paste `passphrase.raw` or decrypted `.db` files into a chat.
- Do not claim backup 2 is included; it is still encrypted RMFH.
- Do not send media XML as if it were the picture itself — media files were not extracted.
