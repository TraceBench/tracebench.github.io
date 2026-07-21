# TraceBench Website

Static single-page application for the public TraceBench benchmark website at
`https://tracebench.github.io/`.

The site is a rendering layer only. Benchmark results, trajectories, generated
question data, and accepted submissions are treated as canonical in the
Hugging Face dataset repository `eth-siplab/tracebench`.

## Preview Locally

```bash
cd /Users/tbe/repos/tracebench.github.io
python3 scripts/serve.py
```

Open `http://127.0.0.1:8000/`.

Nested SPA routes such as `/results/agent-x-2026-05-01/` and
`/environments/BallDrop/` are served through `index.html`. The deployed
`404.html` loads the same shell for GitHub Pages deep-link fallback.

## Data

Browser-ready data lives under `public/data`:

```text
public/data/
  summary.json
  leaderboard.json
  environments/
    BallDrop/
      description.json
      data_1.json
      ...
  submissions/
    <submission-id>.json
```

Validate checked-in data:

```bash
python3 scripts/validate_website_data.py
```

Regenerate from Hugging Face:

```bash
python3 scripts/sync_hf_to_website_data.py \
  --hf-repo eth-siplab/tracebench \
  --tracebench-root /Users/tbe/repos/TraceBench
```

For local prepared data:

```bash
python3 scripts/sync_hf_to_website_data.py --source-dir /path/to/public/data
```

## Deployment

GitHub Pages is deployed by `.github/workflows/deploy.yml`. Configure Pages to
use GitHub Actions, then push to `main` or trigger the workflow manually. The
workflow regenerates `public/data` from Hugging Face, validates it, uploads the
static site artifact, and deploys Pages.

## Notes

- The current repository includes representative browser-ready data so the site
  can be previewed before the canonical Hugging Face dataset is published.
- Replace placeholder author, email, and final citation text once publication
  metadata is available.
