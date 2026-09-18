# Vercel deployment

The local CLI is still supported. This guide describes the production product
deployment; do not deploy the local `web.py` server because its filesystem
storage is not durable on Vercel.

## Deployment baseline included in this repository

- Python 3.12 is pinned in `.python-version`.
- Vercel loads the FastAPI entrypoint declared in `pyproject.toml`.
- `vercel.json` excludes tests, local virtual environments, and generated runs
  from the function bundle.
- `/api/health` confirms runtime and storage configuration.
- `/api/deployment-readiness` prevents preparation runs from being enabled
  before durable storage and access control exist.

## Before enabling customer data

1. Import this Git repository into a Vercel project.
2. In **Storage**, create a Vercel Blob store and set access to **Private**.
   Connect it to Production, Preview, and Development as appropriate.
3. Add a tenant-aware Postgres database for user, company, project, profile,
   run, and approval metadata.
4. Add application authentication and authorization. Vercel deployment
   protection is useful for a pilot, but is not a replacement for roles and
   tenant isolation.
5. Configure a private Blob artifact store. Raw uploads, generated CSVs, and
   decision logs must be private and downloads must pass through an authorized
   application route.
6. Use direct browser-to-Blob uploads for files over 4.5 MB.

## Deployment

After those services are connected, deploy from the Vercel dashboard or CLI:

```bash
vercel
```

Never commit `.env.local`, `BLOB_READ_WRITE_TOKEN`, raw customer data, or
generated runs. `.env.example` documents variable names only.
