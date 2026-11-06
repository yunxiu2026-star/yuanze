# B2B starter maintenance

- Treat `config/site.json`, `config/catalog.json`, `config/guides.json` and `config/pages.json` as the content/branding source of truth. Do not hardcode a client's domain, contacts, products, certifications or partner logos in application code.
- `.local/`, `.env`, customer uploads and databases are private runtime state and must never enter Git or deliverable archives.
- Preserve the intake validation, idempotency, consent checks, exact-origin policy, authenticated GEO routes, CSRF and credential revision/session invalidation.
- Default development data is fictitious, outbound notifications and paid AI are disabled, and example pages are noindex. Production requires explicit configuration, HTTPS, PostgreSQL roles/migrations and fresh secrets.
- For functional changes run `python -m unittest discover -s tests`, `python scripts/audit_template.py`, and the applicable browser checks. Do not deploy to any remote environment unless requested.

