# Privacy and data handling

Citation Lens has no telemetry, accounts, remote storage or embedded model API.
Queries and selected paper identifiers go to the requested scholarly provider:
OpenAlex, Semantic Scholar or arXiv. Paper text/images are fetched from their
indexed public source. Jina Reader is contacted only for an explicitly configured
fallback and receives the selected PDF URL. Third-party terms and usage limits
apply. Avoid putting confidential information into research queries.

API keys are read from environment variables and sent only to their respective
provider origin. They are not written to the cache. Paper text, metadata, query
URLs hashed as cache keys, and graph queries/snapshots are stored in local SQLite.
There is no automatic upload of user documents or local files. Removing the
configured cache directory removes retained data. Expired rows are removed on
writes; no background process runs when the server is stopped.

Keep the cache in an access-controlled local directory. Paper content and
references are third-party material; the code license does not grant rights to
redistribute cached paper contents. Publish code and metadata examples, not
unlicensed full-text corpora.
