Alfred Workflows
================

PoE2 Currency Exchange
----------------------

Look up Path of Exile 2 currency prices and exchange rates for the current
league, powered by the public [poe2scout.com](https://poe2scout.com) API.
Install by double-clicking `PoE2 Currency Exchange.alfredworkflow`; the editable
source lives in `src/PoE2 Currency Exchange/`.

Usage (keyword `poe2`):

- `poe2 divine` — price of Divine Orb (in Exalted, plus a Divine equivalent)
- `poe2 mirror in divine` — Mirror of Kalandra priced in Divine Orbs
- `poe2 chaos in exalt` — Chaos priced in Exalted
- `poe2 @fragments breach` — search a different category (default `currency`)

Every poe2scout price is denominated in the league base currency (Exalted Orb),
so the rate between any two items A and B is just price(A) / price(B). The full
currency list is fetched once and cached so typing stays instant, and the
current league is detected automatically. Press Return to copy the value, or
Cmd+Return to copy the currency name. Requires `python3` (macOS Command Line
Tools). Built for the current version of Alfred (5).

Upload to imgur
---------------

This allows you upload an image to imgur using a hotkey when the file is selected in Finder, the default hotkey is CTRL+OPT+CMD+I. A history.txt file is maintained in your workflow's directory that contains all links to uploaded images as well as their deletion links. Once an upload is complete the link is copied to your clipboard and then opened in your default browser.

Special thanks to Roland Rabien, I am using his project (https://github.com/FigBug/imguru) to handle the upload.

Clean Duplicate Apps in Open With
---------------------------------

If you notice the same app listed multiple times in your "Open With" menu, run this workflow to clear them out.

Restart Dock
-------------

Run 'killall Dock' to restart the Dock process. This can often resolve weird issues such as a missing wallpaper image or dock icons that are acting up.
