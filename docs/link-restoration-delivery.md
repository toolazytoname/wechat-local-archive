# Original webpage links: deliberate navigation, not automatic loading

This corrects an over-restrictive reader behavior: disabling remote loading must
not erase a message's original webpage destination or prevent deliberate use.

## Delivered

- Parse only the app-message `url` field into a validated HTTP(S) destination.
  Never substitute attachment/CDN URLs or AES fields. No URL fetching, probing,
  DNS resolution or remote thumbnail loading occurs during parsing/rendering.
- Reject other schemes, credential-bearing authorities, control characters,
  backslashes, malformed ports, nested fields and oversized values. URLs are not
  truncated into a different valid destination. Preserve valid query strings.
- Reader shows **打开原链接** and the destination host. Confirmation is required;
  accept opens a new tab with `noopener,noreferrer`, decline opens nothing.
- Analysis JSONL keeps `card.url`; CSV adds `original_url`; Markdown retains the
  URL as text. Both HTML writers include escaped, explicit external anchors with
  no-referrer and noopener, without relaxing their resource-loading CSP.
- Presentation version **5** refreshes the derived index. Canonical records are
  not rewritten. Old generated slices are not silently replaced.

Original webpage query strings can contain private or expiring parameters. These
exports are **not anonymized**. An explicit external click exposes the usual
browser network information to the destination; it is not an archive upload.
Expired/login-gated pages and non-web mini-program destinations are not repaired
by restoring a link. Plaintext URL auto-linking and nested forwarded-message URL
recovery are outside this patch.

## Evidence

- Full Python suite: **336 passed**, 70.296 seconds
  (`/tmp/wla-links-final-full.log`).
- Real Chromium 19-record content fixture: declining confirmation generated no
  external request; accepting generated exactly one isolated-tab navigation,
  intercepted and aborted before reaching the synthetic trap. No actual remote
  page was fetched. Automatic external requests remained zero.
- Both HTML outputs opened with the source server stopped at two viewport widths.
  Correct anchors/referrer policies, zero automatic HTTP requests, no injected
  scripts, no overflow, and stale-search protection passed
  (`/tmp/wla-links-browser-final.log`).
- Fresh independent wheel installation and isolated 12-message export smoke
  passed (`/tmp/wla-links-install.log`).
- Current-tree privacy rules passed; not a history/privacy certification.

## Local artifacts

- Wheel: `dist/wechat_export-0.2.0-py3-none-any.whl`
  SHA256 `325f3de0a610b632c587dce63986a17dc6079f05a6a977e325cfbd8c122d9bef`
- Preview: `dist/wechat-local-archive-preview-r3.zip`
  SHA256 `1d6112bdef4660ff565f1d0c6b4c9e651399979b6ec81c5958558d4d660cf4b6`

## Not delivered by this patch

Local image/file inventory is not successful attachment recovery. A message
reference, a thumbnail, an encrypted container and a verified original file are
different things. This patch does not copy/decrypt media, declare attachment
completion, capture keys, change WeChat, upload archives or publish a release.
`source_kind=live-db`; `backup2_coverage=unverified` remain unchanged.

See [content and installation evidence](content-safety-delivery.md).
