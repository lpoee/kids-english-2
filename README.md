# Kids English 2.0

Static GitHub Pages site for Kids English 2.0.

Live site: https://lpoee.github.io/kids-english-2/

## Pipeline

The ComfyUI + LTX 2.3 pipeline is included in this repo:

- `data/kids_english_2_cards.json` defines the 10 cards, prompts, audio, posters, and video paths.
- `comfyui/official/` contains the upstream LTX 2.3 workflow template used for patching.
- `comfyui/workflows/kids-english-2/` contains one patched UI workflow per card.
- `comfyui/jobs/kids-english-2/` contains one API prompt per card.
- `scripts/comfyui_ltx23_pipeline.py` prepares inputs, writes jobs, submits ComfyUI renders, downloads outputs, and validates the site assets.

Common commands:

```bash
python3 scripts/comfyui_ltx23_pipeline.py validate
python3 scripts/comfyui_ltx23_pipeline.py prepare-inputs
python3 scripts/comfyui_ltx23_pipeline.py write-jobs
python3 scripts/comfyui_ltx23_pipeline.py write-api-jobs
python3 scripts/comfyui_ltx23_pipeline.py submit-jobs
```
