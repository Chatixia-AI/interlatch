# Chronicle Blueprint icon

Superseded as the app icon by the 3D render in `icon-3d.webp` (see `packaging/icons3d/README.md`), which keeps this
emblem's shape and palette. `icon-blueprint.webp` stays as the flat original.

Created with the built-in imagegen tool on October 3, 2026. The new Chatixia organization icon was used only as a style reference; Chronicle has its own layered-session emblem.

## Assets

- `icon-blueprint.webp`: 1024 × 1024 lossless source, resized once from the generated 1254px PNG.
- `icon.png`: exported artwork with standard macOS Dock margins, also used by the repository READMEs and docs.
- `Chronicle.icns`: macOS icon bundle at the standard icon sizes.
- `src/interlatch/web/icon.png` and `favicon.png`: dashboard, source app, and browser icons.
- `src/interlatch/web/icon-{180,192,512}.png`: opaque navy touch and maskable phone icons.

Regenerate these exports with `uv run --group build python packaging/macos/make_icon.py`. The WebP stores the source
pixels without further compression loss and stays below the repository's 600 KB limit for new files.

## Exact generation prompt

```text
Use case: logo-brand.
Asset type: finished macOS application icon for Chronicle, the Chatixia tool that saves coding-agent sessions and turns them into reusable knowledge.
Input image: the supplied Chatixia organization emblem is a BRAND FAMILY REFERENCE ONLY. Match its midnight navy, golden amber, sky blue, confident folded geometry, and restrained soft tonal treatment. Invent a distinct Chronicle symbol, not the same aperture logo.
Primary request: a memorable compact archive / layered-session emblem. Three substantial offset document sheets form one coherent, gently isometric stack. Sky-blue back sheets and a warm amber front sheet, with an angular folded corner that echoes the family's ribbon motif. The front sheet has just two broad navy inset transcript strokes; no tiny details or text. The pages should feel like collected knowledge, intelligently folded and kept. Aim for a distinctive silhouette rather than a generic file icon.
Style: premium contemporary app identity. Crisp clean edges, broad geometric planes, soft friendly geometry, only very restrained tonal variation to reveal the fold and layered depth. No glass, chrome, heavy gradients, realistic lighting or glossy 3D effects.
Palette: midnight blueprint navy #081A31 for the tile and inset details, amber #FFB45E, sky blue #7CC7FF.
Composition: square 1024x1024 canvas; one macOS-style rounded-square navy tile centered at about 80% of the canvas width and height, with transparent outer margins. Emblem centered within it, about 62% of the tile width, generous breathing room, readable at 32px. Standard balanced app icon proportions; all shadows, if any, subtle and inside the safe margin.
Background: actual transparent alpha outside the navy rounded tile, no colored background behind it, no checkerboard baked in.
Text: none.
Avoid: old indigo/white icon, the organization aperture shape itself, speech bubbles, C lettering, robot faces, brains, stars, sparkles, neural nodes, clocks, captions, mockups, collage, watermark.
Output one finished icon only, with actual transparency outside the tile.
```
