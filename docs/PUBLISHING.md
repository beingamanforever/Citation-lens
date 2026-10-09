# Publishing

## GitHub preview release

This repository is the plugin root. It contains both host manifests and
marketplace catalogs; the backend is not copied into a second directory.

On a push to `main`, `.github/workflows/check.yml` runs lint, formatting, tests,
generated-manifest consistency and wheel/sdist builds on three OSes and two
Python versions. After every job passes, the release job packages the plugin and publishes a new **preview**
release if `v<pyproject version>` does not already exist. An existing release is
never overwritten. Increase the version and regenerate manifests to release again.
The release job alone has repository contents-write permission. No personal token
or publishing secret is required.

```bash
uv venv
uv pip install -r requirements-test.txt
uv pip install --no-deps -e .
uv run --no-project ruff check src tests scripts evals demo video
uv run --no-project ruff format --check src tests scripts evals demo video
uv run --no-project pytest -q
uv run --no-project python scripts/package.py
claude plugin validate .
claude plugin validate .claude-plugin/plugin.json
uv run --no-project python -m build --no-isolation
```

Users install from `beingamanforever/Citation-lens` using the README commands.
The release ZIP is a self-contained plugin; GitHub source archives also include
the marketplaces, tests and development files. The Python wheel is a separate
MCP backend distribution.

## Official directories and PyPI

Repository distribution is separate from official directory approval. Neither
OpenAI's nor Anthropic's directory submission has been completed.

OpenAI's public MCP submission currently requires a remote HTTPS endpoint.
Hosting this local service requires per-user state, authentication, quotas,
resource-isolated PDF workers and operational controls before submission.
See [OpenAI packaging](https://developers.openai.com/plugins/build/plugins) and
[Claude publishing](https://code.claude.com/docs/en/plugin-marketplaces).

PyPI publication requires name availability and a user-controlled credential or
trusted-publishing setup. Wheels/sdists are attached to GitHub releases; the README
uses GitHub marketplace installation so it does not depend on PyPI publication.
Research-quality claims come from the [paired evaluation](EVALUATION.md).
