# Structured-message card delivery — 2026-09-09

Continues T05 from the original guided-export plan. This is explicitly a supported
structural subset, not a claim to parse every WeChat app-message variant.

## Implemented

- New bounded `message_cards` parser recognizes explicit `refermsg`, `recorditem`
  and file-attachment structure. Generic app messages with neither remain links.
- Quote cards expose allowlisted reply title, quoted author and plain quoted text.
  XML inside a quote is not flattened into human text or secret fields.
- File cards show title, extension, size and **file not acquired**. An extension or
  URL does not make an attachment available; no guessed download is started.
- Merged-forward cards show at most eight plain-text item previews and an explicit
  count/truncation status. Unparsed nested records show a clear placeholder.
- Link descriptions/titles remain text; external links are never automatically
  fetched. No CDN URL, key field or raw structured payload is stored in card JSON.
- XML inputs are length/node bounded, DTD/entity declarations are rejected, and
  malformed/oversized input falls back to an unparsed placeholder. Raw canonical
  records are unchanged and still available through explicit raw export.
- Index presentation version advanced to 3, adding allowlisted `card_json`;
  existing indexes rebuild from canonical data, not from guessed old card fields.
- Browser renderer uses DOM textContent/createElement for quote blocks, files and
  forwarded previews. Markdown and offline HTML share text-only card summaries;
  HTML remains escaped. CSV/JSONL analysis records carry safe metadata.
- Synthetic packaged/checkout demos now contain 12 records including quote, file
  and forward examples. Installed demo uses a new `demo-v2` directory, preserving
  prior demo copies instead of overwriting user-visible artifacts.

## Verification

- Focused tests cover subtype recognition, CDATA, bounded forwarded previews,
  secret-field exclusion, structured quoted content, DTD/oversized inputs,
  indexed card fields, offline quote escaping and legacy HTML-escaping behavior.
- Real browser loaded an isolated synthetic demo on port 8894: displayed quoted
  author/text, file name/8192-byte size/missing status, and two forwarded items.
- Browser console was empty; observed page resource entries all used local
  127.0.0.1 URLs. Missing-image requests stayed local and rendered placeholders.
  No raw WeChat account, keys, real chat screenshot or original app was accessed.
- Test tab and owned server process were closed. No commit/push.

## Remaining bounds

- Forwarded items are previews, not a recursive forensic export of all nested
  media. Unsupported layouts stay partial/unparsed; the original canonical XML
  is retained rather than silently claimed as fully understood.
- Exact source-version subtype capability matrix, real-account visual review,
  small-screen/focus/keyboard comprehensive matrix and independent user acceptance
  are not proved by these synthetic examples.
- Initial output-directory UI, dependency guidance, synthetic CI, full privacy
  history audit and operator-authorized coordinator validation remain on the
  full-goal checklist. Developer-preview status remains appropriate.

## Final regression and artifact

**176 tests passed, 37.072 s**. JS syntax and git whitespace checks passed.
Rebuilt wheel source bytes/demo contents and private-path exclusions checked.
SHA-256: `d6f08346a8c5d4b6172ed01e93ba63f30ed31d25c6dba1e1250a00c6dfbc4d34`.
