# Isai for macOS

pywebview window + your FastAPI backend (backend.py) + bundled ffmpeg, the same design as the Windows and Android apps.
The UI is the live https://isai.thirai.uk/ page, fetched through the local server at http://127.0.0.1:8765 and saved to ~/Library/Application Support/Isai/index.html. Offline it opens the saved copy; back online it updates (checked on launch, on reload, and every 2 minutes). The API is served locally too.

## Build (no Mac needed)
1. Push this folder to a GitHub repo (keep the .github folder).
2. Actions -> "Build macOS app" -> Run workflow.
3. Download the artifacts: Isai-mac-arm64 (Apple Silicon) and Isai-mac-x64 (Intel).
   Or push a tag (git tag mac-v1.0 && git push origin mac-v1.0) to attach both DMGs to a release.

## Run from source on a Mac
pip install -r requirements.txt
python launcher.py

## First launch
The app is unsigned: right-click Isai -> Open -> Open.
If macOS says it is damaged: xattr -cr /Applications/Isai.app
Logs: ~/Library/Logs/Isai/isai.log

## Updating backend.py
Replace backend.py with the new version and run the workflow again.
