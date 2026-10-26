# Embodied-BenchForge project page

Open `index.html` in a modern browser. All images, data, and styles are local; no build or installation is required. The case explorer also works when opened directly from disk.

For an HTTP preview, run `python -m http.server 8768` from this folder, then open `http://localhost:8768/`.

This version uses a conventional paper-project layout: centered title, authors and affiliations, resource buttons, the original overview figure, Abstract, Method Overview, benchmark examples, interactive tasks, construction results, and BibTeX.

The main Paper link is https://arxiv.org/abs/2609.13082. Author names and affiliations follow the public arXiv HTML version at https://arxiv.org/html/2609.13082v1.

This version includes:

- Thirty-three selected offline examples: six each from ALFRED, Habitat, LIBERO, TartanGround, and UAV, plus three from CARLA. Each includes actual source images, a record ID, the Chinese original, an English display translation, and a reference answer.
- Dataset tabs, per-dataset sample thumbnails, evidence-image switching, language switching, answer reveal, and individual JSON record downloads.
- A separate expandable area for the original six verification-and-repair case studies.
- Original main-paper Figures 1 and 2, plus the instruction–PDDL inconsistency case from supplement Table 5.
- Two recorded interactive trajectories, with selected task fields available as JSON.
- Construction ablations from main-paper Tables 7 and 8, with their measurement definitions and scope.
- An arXiv paper link and selected example records. The supplementary PDF and its download links are excluded from this website and the current website archive.

The interactive JSON files are selected transcriptions, not full simulator-restoration or executable scoring packages. The page does not launch a live simulator.

## Source editing

- `index.html`: page content and section layout.
- `assets/site.css`: responsive visual design.
- `assets/site.js`: dataset/sample tabs, image/language controls, record downloads, repair cases, goal comparison, and trajectory controls.
- `assets/data/showcase/*.json`: selected records for six datasets (six per dataset, except three for CARLA). Original questions, choices, and answer codes are retained.
- `assets/data/showcase.json` and `showcase.js`: merged JSON and browser-loadable copy for direct file preview.
- `assets/images/showcase/`: selected source images encoded for web display. Required labels and visual context are preserved.
- `assets/data/cases.json`: source case records.
- `assets/data/cases.js`: browser-loadable copy of the same data for local-file preview. Regenerate this copy when editing the JSON.
- `assets/images/*.webp`: original manuscript figures and repair illustrations. The PNG sources are retained alongside them.

## GitHub Pages deployment

This folder's contents are published from the `website/` directory in `EurecaMoment/BenchForge`. Existing `docs/`, `examples/`, and code are separate. The dedicated Pages workflow uploads only `website/`.

Project URL: `https://eurecamoment.github.io/BenchForge/`.

The same source can be previewed locally by opening `index.html`. This version includes the publicly available author list and does not claim anonymous hosting. Search-engine exclusions in the HTML and `robots.txt` are indexing preferences, not access controls.
