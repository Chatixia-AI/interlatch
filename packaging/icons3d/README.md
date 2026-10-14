# Interlatch 3D art

The 3D icons on the dashboard (page headers, Home tiles, empty states) and the app icon are rendered with the
[Chatixia Studio](https://studio.chatixia.net) Blueprint Cast, the same characters, objects, materials and light rig as
the studio's explainer films. The small line icons in the sidebar and on buttons stay as they are: they follow the
theme colour and stay sharp at 14–20 px, where a render can't.

- `art.json`: what to render. Keys become `src/interlatch/web/art-<key>.webp`; `app-icon` becomes
  `packaging/macos/icon-3d.webp`. A spec names a cast object (`obj`), a character (`char`, `mood`) or one of the
  members in `members.js` (`custom`), plus optional camera `az`, `pitch` and `pad`.
- `members.js`: Interlatch's own members built from the cast's parts: the open book (Knowledge) and the 3D emblem.
- `render.html`: the studio light rig on a transparent background. `render.py` drives it in headless Chromium.
- `filing.html`, `scenes.html` and `scenes-more.js`: short looping scenes of the cast doing Interlatch's work, each a
  pure function of time: filing a session, syncing, searching, analyzing, the editor, the website's hero (Tink) and
  team scenes. `loops.json` lists each one's page, render size, output size and still frame; `loops.py` renders them at
  24 fps and writes `out/<scene>.webp` (16 fps, about 100–330 KB) and `out/<scene>-still.webp`. The website
  (Chatixia-AI/chronicle-site) serves them from `public/media/cast/`.

## Regenerate

1. Put `blueprint-cast.js` (v1.0) in this folder. It is not committed: download it from the Blueprint Cast Library
   artifact, or copy `public/downloads/blueprint-cast.js` from the Chatixia-AI/chatixia repository.
2. Render and export:

   ```sh
   uv run --no-project --with playwright --with pillow python -m playwright install chromium
   uv run --no-project --with playwright --with pillow python packaging/icons3d/render.py
   uv run --group build python packaging/macos/make_icon.py
   ```

   For the loops: `uv run --no-project --with playwright --with pillow python packaging/icons3d/loops.py`.

The last step turns `icon-3d.webp` into `Chronicle.icns` and the dashboard, favicon and phone icons.
