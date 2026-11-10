# Changelog

All notable changes to ISS Track (web) are listed here, newest first.

## 1.2.4 - 2026-11-10

### Added
- **A privacy policy.** A page at `/privacy`, linked from the bottom of the site, says in plain language what happens to your location, what your browser remembers, what a calendar address reveals, how visits are counted and which other services are involved. Statements about this used to be spread over the footer, the place picker, the guide and the README; they now live in that one place.

### Changed
- **Credits.** The site is credited to Matthew Baker, first. The ESA ISS Tracker, whose map styling inspired this one, is credited last.
- **Cloudflare Web Analytics is allowed.** The content security policy lets a page load Cloudflare's analytics script and report to it, so a site hosted behind Cloudflare can count visits and measure page load times. They are the only two outside addresses the page may run code from or talk to. The policy used to refuse the script, which logged an error on every page load of the public site.
- **The live video is fetched only when you open it.** The YouTube player used to load with every page, whether or not anyone watched.
- **The server keeps no request log.** The container runs without one, and a failed sky forecast no longer writes a position to the log.
- **US spelling throughout**, on the pages and in the code.

## 1.2.3 - 2026-11-10

### Fixed
- **A new version reaches visitors at once.** The page, its scripts and its styles are now sent with `Cache-Control: no-cache`, so a browser or a proxy in front checks them on every visit and an unchanged file costs one short 304. They used to carry no caching instruction at all: Cloudflare kept them for four hours and browsers for as long as they pleased, so after 1.2.2 people ran the old page until they forced a reload. Pictures and fonts are kept for a day.
- **`/favicon.ico` answers with the station icon.** Crawlers and some browsers ask for that address whatever icon the page names, and got a 404; it was a third of all the site's 404s in its first three days.

## 1.2.2 - 2026-11-07

### Fixed
- **The Google Calendar button works.** Google's add-a-calendar page accepts only the `webcal://` form of a feed address; given `https://` it said "Unable to add calendar. Check the URL." The button now hands it the webcal address. Found on the first public deployment, at iss.whereisitin.space.

## 1.2.1 - 2026-11-07

### Added
- **A warning on a private address.** When the site is opened at a home-network address (localhost, 192.168.x.x and the like), the Remind me sheet says that Google Calendar and Outlook.com fetch the feed from their own servers and cannot reach it, so nobody adds a calendar that stays empty. The calendar file is unaffected.

## 1.2.0 - 2026-11-07

### Changed
- **Remind me, one step on every device.** On a computer the top button downloads a calendar file named after the place; opening it imports the next ten days of matching passes, reminders included. An iPhone or iPad subscribes Apple Calendar with one tap, and Android opens Google Calendar's add-a-calendar page. Google Calendar and Outlook.com are one-step subscribe links everywhere. The feed address, the webcal link and a Microsoft 365 link sit under a fold.

### Fixed
- The calendar feed answers a HEAD request; it used to return 404, which a probing calendar client would read as "no such feed".

## 1.1.0 - 2026-11-06

### Added
- **Reminders through your calendar.** The bell in the top bar gives you a calendar feed for your place and settings. Subscribe once and every matching pass appears in your calendar for the next ten days, with a reminder 5, 10, 15 or 30 minutes before. The feed's address carries the place and the settings; the sky forecast is written into each event and never drops a pass.
- **A guide.** The question mark in the top bar opens a plain-language guide: what the map shows, why passes happen at dusk and dawn, a gentle chapter on orbits, how to read a pass, the settings, privacy, and what to do outside.

## 1.0.0 - 2026-11-06

### Added
- The first version of ISS Track as a standalone website: the live map, the station's position, visible passes for any place, pass settings saved in the browser, the sky forecast from Open-Meteo, and the note and dashed track for unseen overflights.
- The engine is the ISS Track integration's own, pinned at version 2.0.2 as a git submodule.
