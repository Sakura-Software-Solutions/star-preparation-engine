# Shared internal deployment

The dashboard supports a single internal team using individual accounts,
roles, private persistent files and SQLite metadata. Every customer-data
route, including artifact downloads, requires a session in shared mode.

Use a persistent internal host/container with an HTTPS reverse proxy. The
included Vercel ASGI adapter refuses preparation on Vercel: ephemeral local
disk and the presence of a Blob token alone do not satisfy this implementation's
storage requirements.

## Container deployment

Included files: `Dockerfile`, `compose.yaml`, and `Caddyfile`. The app
container runs as an unprivileged user, has a read-only root filesystem, and
exposes port 8080 only to the private compose network. Caddy terminates HTTPS.
The STAR volume contains both private files and metadata.

1. Point an internal team hostname at the deployment host. Set `STAR_HOST`
   in an untracked `.env` file; copy the variable names from `.env.example`.
   For automatic public-certificate issuance, the hostname and ports 80/443
   must be reachable for the certificate challenge. For a private CA, adapt
   Caddy to your organization's TLS setup and trusted certificates.
2. Build and provision the first account interactively:

   ```bash
   docker compose build
   docker compose run --rm app ./star-prep user-add team-admin \
     --role admin --data-dir /var/lib/star
   ```

   Passwords are prompted twice, never command-line arguments. Minimum length
   is 12 characters. Passwords are salted and hashed with PBKDF2-SHA256.
3. Start the service:

   ```bash
   docker compose up -d
   ```

4. Open the configured HTTPS hostname, sign in, and run **Try a synthetic
   example** before introducing customer files.
5. Provision teammates with the same `user-add` command. Use `--role preparer`
   for upload/mapping/rules/corrections and `--role viewer` for history and
   download-only access. Reprovisioning an existing username changes its
   password/role and revokes existing sessions.

All members of this deployment can read this team's customer runs. Do not
mix organizations with different access entitlements in the same volume.

## Existing internal host

The same service can run without Docker:

```bash
./star-prep user-add team-admin --role admin --data-dir /private/star-data
./star-prep serve --shared --secure-cookies \
  --host 127.0.0.1 --port 8080 --data-dir /private/star-data
```

Place your HTTPS reverse proxy on the same host and preserve the original
Host header. Keep the application port inaccessible to clients; they should
only use the HTTPS endpoint. Shared mode will refuse startup unless secure
cookies are enabled and at least one account exists. Local mode refuses
non-loopback binding.

For an ASGI host, install the project and an ASGI server, then use
`star_preparation.cloud_app:app` with `STAR_DATA_DIR` and
`STAR_SECURE_COOKIES=1`. It exposes the same UI and API. Missing configuration
or an ephemeral Vercel runtime returns HTTP 503, not a misleading readiness
claim. The standard-library server is sufficient for a small internal pilot;
apply upload/concurrency/rate limits at the organization's reverse proxy.

## Access and provenance

- Sessions use opaque random tokens, hashed in SQLite, with an eight-hour
  expiry. Cookies are Secure, HttpOnly, and SameSite=Strict.
- Same-origin checks protect every mutation. Failed sign-ins are throttled
  after ten failures for a username in a 15-minute window. Add network-level
  rate limiting at the proxy.
- Viewers cannot upload, inspect new sources, save profiles or prepare runs.
- Preparers/admins can create runs and profiles. Account administration and
  retention require host/container CLI access.
- Customer data and API responses use no-store caching. Downloads pass through
  authorization; no public artifact mount or listing is exposed.
- Profile saves use revision checks to prevent lost updates. Run revisions
  preserve parent IDs and actor information. This is an application decision
  trail, not tamper-proof storage against a host administrator.

## Storage, backup and retention

Use an encrypted private volume and host access controls appropriate for your
customer agreements. Files are protected by a mode-0700 data directory.
Retain the entire volume: SQLite metadata, sources, run artifacts and profile
versions belong together. Stop the application while taking a filesystem
backup, or use a coordinated SQLite-aware backup process.

Choose the retention window according to the team's agreement with the
customer. Cleanup is explicit; nothing is automatically erased. Stop the app
during maintenance to avoid concurrent preparations.

```bash
# Preview counts without deleting anything:
./star-prep purge --data-dir /private/star-data --older-than-days 90

# Once the preview and your retention policy agree:
./star-prep purge --data-dir /private/star-data --older-than-days 90 --execute
```

Execution permanently removes expired run artifacts and old uploads that
have no retained runs. Saved profile versions and accounts remain. Delete or
retire customer-specific profiles separately when required by your policy.
Recovery of purged material requires your private backup.

Do not commit the private volume, raw uploads, run exports, credentials,
database files or customer screenshots. The repository ignores `data/` and
`runs/`; Docker and Vercel packaging also exclude them. Do not place private
data in arbitrary unignored repository folders.

## Health and operational checks

`GET /api/health` reports whether the configured app can start. Verify sign-in,
a synthetic preparation, a private download, and reopening history after a
restart as deployment acceptance checks. No real customer upload is required
for those checks.

This change provides the deployable implementation and configuration. DNS,
TLS, the actual internal host, provisioning real accounts, and production
deployment are environment-specific operations.
