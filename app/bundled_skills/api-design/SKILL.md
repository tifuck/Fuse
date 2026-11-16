---
name: api-design
description: Design or review an HTTP/JSON API covering resources, naming, status codes, errors, pagination, idempotency, versioning and auth. Use when adding endpoints, writing an OpenAPI spec, or reviewing an API change for consistency.
metadata:
  category: Build
---
# API design

Consistency with the existing API beats any style guide. Read the current endpoints, error format and conventions first and match them.

## Resources and URLs
- Nouns, plural, lower-case, hyphenated: `/v1/invoices`, `/v1/invoices/{id}/line-items`.
- Nest only for true ownership, one level deep. Otherwise filter: `/v1/line-items?invoice_id=...`.
- Actions that are not CRUD: a sub-resource (`POST /v1/invoices/{id}/send`) is acceptable; keep them few.
- Stable opaque ids (strings). Do not expose auto-increment ids if enumeration matters.

## Methods and status codes
- GET read (safe, cacheable), POST create or action, PUT full replace, PATCH partial update, DELETE remove.
- 200 OK with body, 201 Created with `Location` header, 202 Accepted for async work, 204 No Content.
- 400 malformed request, 401 missing or bad credentials, 403 authenticated but not allowed, 404 not found (also for resources the caller may not see), 409 conflict (version or state), 422 validation failed, 429 rate limited with `Retry-After`.
- 5xx only for server faults; never for bad input.

## Payloads
- One field naming style across the API (snake_case or camelCase, matching what exists).
- Timestamps in RFC 3339 UTC; money as integer minor units plus currency; enums as strings.
- Return the full resource after create and update.
- Accept unknown fields leniently or reject them consistently; document which.

## Errors
One shape everywhere, e.g.:
```json
{"error": {"code": "invoice_not_found", "message": "Invoice inv_123 not found", "details": [{"field": "amount", "issue": "must be positive"}]}}
```
Machine-readable `code`, human `message`, per-field details for validation, a request id for support. Never leak stack traces or SQL. RFC 9457 problem+json is a good standard if nothing exists yet.

## Lists and pagination
- Cursor pagination for large or changing collections: `?limit=50&cursor=...` returning `next_cursor`. Offset only for small, stable sets.
- Cap `limit` server side; default to a sane size.
- Filtering and sorting via explicit query params (`?status=paid&sort=-created_at`), with an allowlist of sortable fields.

## Idempotency and concurrency
- GET, PUT, DELETE must be idempotent.
- For POST that creates or charges, accept an `Idempotency-Key` header and return the stored response on replay.
- Optimistic concurrency with `ETag` / `If-Match` (or a version field) returning 412 or 409 on mismatch.

## Versioning and change
- Version in the path (`/v1`) or a header; pick what exists.
- Additive changes (new optional fields, new endpoints) are fine in place. Removing or renaming fields, changing types or meaning, or tightening validation are breaking: new version or a deprecation period with a `Deprecation` header and docs.

## Auth and safety
- Auth on every endpoint by default; explicit allowlist for public ones.
- Authorize per object, not just per route.
- Rate limits, request size limits, and timeouts on expensive calls.

## Deliverable
For a design: endpoint table (method, path, purpose, request, response, errors) or an OpenAPI snippet. For a review: findings with the endpoint, the rule broken and the suggested change.
