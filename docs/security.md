# Security notes

- **Sign-in**: 5 wrong passwords for a username or 20 from one address in 15 minutes locks sign-in (even the right
  password is refused until the time passes). Failures are recorded.
- **Two-factor**: TOTP (RFC 6238) required for administrators and managers (`TWO_FACTOR_REQUIRED`). A code can be used once.
  Known gap: the secret is stored as plain text in the database; encrypt it at rest if the database host is shared.
- **Roles**: every page checks the role; refused requests are written to the audit trail as "denied". A test opens every
  page as every role.
- **Database accounts**: the website uses a limited account. It cannot change structure, cannot edit or delete audit,
  payment and stock-movement rows, and cannot delete sync bookkeeping.
- **Secrets**: all in environment files or the key file, none in the repository (`.env`, `.env.cloud` are ignored).
- **Cloud copy**: read-only (all writes refused except sign-in, sign-out, sync receive, 2FA). Sync needs a bearer token.
  Passwords and 2FA secrets are never synced.
- **Dependencies**: `pip-audit` runs in CI; last local run found no known vulnerabilities.
- **`check --deploy` warnings accepted** (set in `SILENCED_SYSTEM_CHECKS`):
  - `security.W005` (HSTS subdomains): the hospital name may share a domain with other services, so subdomains are not forced.
  - `security.W021` (HSTS preload): preload cannot be undone and is meaningless for a private hospital address.
- **Not covered**: disk encryption of the server, physical security, a penetration test. Do these before go-live.
