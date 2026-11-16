---
name: security-review
description: Security review of a change or codebase that reports only exploitable findings with evidence. Use when asked for a security audit, before merging code that handles input, auth, files, network or secrets, or after a dependency change.
metadata:
  category: Quality
---
# Security review

Find what an attacker can actually do. Every finding needs a source, a sink, and a path between them that you traced in the code.

## Scope
- Diff review: `git diff <base>...HEAD`, plus the callers and handlers it reaches.
- Codebase review: start at entry points (HTTP routes, CLI args, message consumers, file uploads, webhooks, scheduled jobs) and follow untrusted data inward.
- Note the trust model: who is the user, who is the attacker, what is already behind auth.

## Checklist (apply what the code touches)
- **Injection**: SQL built by string formatting; shell commands with `shell=True`, backticks or string-joined args; template rendering of user input; LDAP/XPath/NoSQL query building; eval/exec.
- **AuthN/AuthZ**: endpoints missing auth checks; object access by id without an ownership check (IDOR); role checks only in the UI; session/token expiry, signature verification and algorithm pinning on JWTs.
- **Secrets**: keys or tokens in code, config, tests or logs; secrets returned in API responses or error messages.
- **SSRF**: server fetches a user-supplied URL without an allowlist; redirects followed into private ranges (127.0.0.0/8, 10/8, 169.254.169.254, ::1).
- **Path traversal**: user input joined into file paths without resolving and checking the result stays under the intended root; archive extraction (zip slip); symlink following.
- **Deserialization**: pickle, yaml.load without a safe loader, Java/PHP native serialization, or similar on untrusted data.
- **Web**: XSS via unescaped output or `innerHTML`; CSRF on cookie-auth state changes; open redirects; permissive CORS with credentials.
- **Crypto**: home-made crypto, MD5/SHA1 for passwords, missing constant-time compare for tokens, predictable random for secrets.
- **Resource abuse**: unbounded uploads, regex with catastrophic backtracking, missing rate limits on login or expensive endpoints.
- **Dependencies**: run the ecosystem audit if available (`pip-audit`, `npm audit --omit=dev`, `cargo audit`, `govulncheck ./...`) and check whether the vulnerable function is actually reachable.

## Evidence
For each candidate, confirm it before reporting:
- Trace input from entry point to sink with file:line references.
- Check for existing mitigations (validation, framework escaping, middleware).
- If safe to do locally, demonstrate with a test or a request against a local instance. Never attack production or third-party systems, and never exfiltrate real data.

## Report
For each confirmed finding:
```
[Critical|High|Medium|Low] <title>
Where: path:line
Attack: who sends what, and what they get
Evidence: trace or reproduction
Fix: concrete change
```
Drop theoretical issues with no reachable path, or list them briefly under "Hardening (not exploitable as found)". Do not print real secret values; name the file and line and recommend rotation.
