# djnago-bookmyshow-clone-main

## Movie filtering performance notes

- Server-side filters: multi-select genres and languages, search, sorting, and pagination run on indexed fields; genre M2M is filtered via joins with `.distinct()` to avoid duplicates.
- Indexes: single-field (`name`, `rating`, `language`) plus composites (`language, name`, `name, id`, `rating, id`) to support filtered sorts and keyset pagination. Added covering index on the M2M join table (`genre, movie`) to keep genre filters sargable.
- Search: prefix-only (`istartswith`) so the `name` index is usable; avoids full scans from `%term%` lookups.
- Pagination: offset/page numbers are default; switch to keyset via the UI when you need faster deep paging. Per-page size is selectable from the top toolbar.
- Live counts: genre counts respect search + language filters; language counts respect search + genre filters so each facet shows \"what you'd get if you add this filter next\".
- Seeding: `python manage.py seed_sample_movies` for small demo data, `python manage.py seed_bulk_movies --count 6000` to generate a large catalog for perf testing.
- Benchmarking: `python manage.py benchmark_filters` runs representative queries and reports average timings (run after seeding).
- Email confirmations: bookings enqueue a confirmation email; run `python manage.py process_email_queue --limit 20 --loop --sleep 30` as a worker (or schedule) to send with retries/backoff. Configure SMTP via `EMAIL_*` env vars (defaults to console backend). `Procfile` includes a ready-to-run `worker` entry.
- Monitoring: EmailQueue visible in Django admin with requeue action; queue worker logs batch summaries and failures. Use `.env.example` to set secrets via environment (keep real keys out of `.env` in VCS).
- EXPLAIN: in DEBUG, append `?explain=1` to the movie list URL to log the query plan (console output); use to verify index usage.
- Trade-offs: keyset is faster for deep pages but cannot jump to arbitrary page numbers; composites increase index storage but keep common filters sargable; live counts stay accurate but add a grouped query (still index-backed).

## Ops guide (filters & email queue)

- Run server: `python manage.py runserver 0.0.0.0:8000`
- Run worker: `python manage.py process_email_queue --limit 50 --loop --sleep 15`
- Perf logging: `movies.perf` logger records request durations and parameters for the movie list view; in DEBUG it includes query counts. Increase the log level or attach to APM if needed.
- Observability: use the admin EmailQueue list to monitor failures; requeue from the admin action. For DB plans in dev, hit `/movies/?explain=1` (DEBUG only). Consider adding DB-level monitoring (pg_stat_statements) in production.
- Index maintenance: migrations add composite indexes plus a covering index on the genre/movie join table; keep ANALYZE/autovacuum enabled on Postgres for sargable filters at scale.



How to run:
    - .\.venv\Scripts\python.exe manage.py runserver
    - .\.venv\Scripts\python.exe manage.py process_email_queue --limit 50
    


Admin Analytics Dashbaord :- http://127.0.0.1:8000/analytics/dashboard/
Admin controls :- http://127.0.0.1:8000/admin/