# Changelog

All notable changes to ISS Track (web) are listed here, newest first.

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
