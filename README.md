# ISS Track (web)

A website that tracks the International Space Station on a live map and tells you when you can step outside and see it, from any place you choose. It is the standalone, browser-only version of the [ISS Track](https://github.com/mattbratt/iss_track) Home Assistant integration, and it runs on the same engine.

It predicts every pass you can actually see: the station in sunlight, your sky dark, and the station high enough to spot. Each pass is rated from Poor to Excellent with an estimated brightness, and you choose which ones count. When the station crosses your sky unseen, it says why.

See the [changelog](CHANGELOG.md) for what is new in each version.

## What it shows

- The station's position on a world map with day and night shading and its orbit paths, 1.5 hours ahead and behind.
- Latitude, longitude, altitude, speed and ISS time, in miles or kilometers.
- Every pass you can see in the next 10 days from the place you are watching from, with the next matching pass drawn in green on the map and a blue ring showing where the station has to be for you to see it.
- A note when the station is about to cross your sky with nothing to see, and why: it is in Earth's shadow, or your sky is too bright. That stretch of track is drawn dashed and gray.
- A sky forecast for each pass, from Open-Meteo's hourly cloud cover at your place.
- Your matching passes in your own calendar, as a downloaded file or a feed that updates itself, so it reminds you before each one.
- A guide that explains all of it in plain language.
- A live video feed from the station when you click its icon (via YouTube).

## Where you are watching from

- **Use my location** asks your browser for its position once, and works with it rounded to about a kilometer.
- **A place** finds a town or city by name. Pass times are shown in that place's own time zone.
- Up to five recent places are offered again the next time you choose one.

## Privacy

The site has a privacy policy at `/privacy`, linked from the bottom of every page. It says what happens to a visitor's location, what the browser remembers, what a calendar address reveals, how visits are counted, and which other services are involved. Nothing about any of that is stated anywhere else, so there is one place to keep true.

The policy describes the public site at `iss.whereisitin.space`. If you host a copy, it is yours to keep accurate: the page is `web/privacy/index.html`. The server keeps its request log, where each pass request shows the visitor's position rounded to two decimal places, within about half a mile, and the policy says so. Behind a tunnel or proxy the log shows that connection's address; served directly, it shows each visitor's, which your policy would have to say.

## Reminders

The bell in the top bar lets your own calendar remind you, 5, 10, 15 or 30 minutes before each pass that matches your settings. The sheet puts the one-step route for the device on top:

- **On a computer**, it downloads a calendar file (`ISS passes - <place>.ics`) holding the next ten days of matching passes. Opening the file imports them all, with their reminders, into Outlook, Apple Calendar, Thunderbird or any other calendar. The file is a snapshot: it does not update itself.
- **On an iPhone or iPad**, it subscribes Apple Calendar to a feed that keeps itself up to date.
- **On Android**, it opens Google Calendar's add-a-calendar page for the same feed.
- **Google Calendar** and **Outlook.com** are one-step subscribe links on every device, through the add-by-address pages both services provide. A Microsoft 365 work account has its own link under "More ways to add it", with the feed address for any other calendar.

The file and the feed carry the place and the settings you had when you made them, so after changing your settings, add it again. The sky forecast is written into each event and never drops a pass. A subscribing calendar fetches the address itself, so for a subscription the site has to be reachable from where that calendar runs: a phone or Google Calendar needs the public address, not one on your own network. The sheet says so when it is opened at a private address. The downloaded file has no such need.

## The guide

The question mark in the top bar opens a plain-language guide at `/guide`: what the map shows, why passes happen at dusk and dawn, a gentle chapter on orbits, how to read a pass, the settings, and what to do when you step outside.

## Pass settings

The gear opens the pass settings. They are saved in your browser.

| Setting | Default | What it does |
|---|---|---|
| Pass quality | Good and Excellent | Judged on the highest point reached while the station is visible |
| Minimum brightness | Magnitude -2 or brighter | Leaves out fainter passes. Lower is brighter |
| Time of day | Evening only | About half of all visible passes are before dawn |
| Sky good enough when | Partly cloudy or better | A pass under a worse forecast is marked "Clouds likely". A pass with no forecast is never marked |

Pass quality bands:

| Quality | Highest point | Typical experience |
|---|---|---|
| Poor | 10-20° | Easy to miss; dim, hazy, often obstructed |
| Fair | 21-40° | Visible if you know where to look |
| Good | 41-60° | Strong, reliable first-timer pass |
| Excellent | 61-90° | High, bright, hard to miss |

Things worth knowing:
- A pass counts only while the Sun is at least 6° below your horizon. A pass that begins in brighter twilight is shown from the moment the sky is dark enough, so its rating can be lower than the full pass would suggest.
- A pass visible for less than one minute is left out.
- Brightness is an estimate at the pass's highest point. It agreed with published predictions to within 0.1 magnitude when checked, but flares off the solar arrays are not predicted.

## Running it

The site is one small container. It fetches fresh orbital elements from Celestrak every two hours, keeps the last good set on disk, and asks Open-Meteo for places and sky forecasts. Browsers only share a location over HTTPS, so put it behind HTTPS before sharing the address.

### On a Synology NAS with a Cloudflare Tunnel

This is how the public site is hosted. A Cloudflare Tunnel gives the site HTTPS and a public address without opening any ports on your router, and visitors never see your home IP address. The tunnel is free; it needs a domain whose DNS is managed by Cloudflare.

1. In DSM's File Station, create a project folder, for example `docker/iss_track_web`, and inside it an empty folder named `data`. DSM's Docker does not create a missing bind-mount folder, so without `data` the first start fails with "Bind mount failed".
2. Copy `docker-compose.yml` and `.env.example` from this repository into the project folder, and rename `.env.example` to `.env`.
3. In the Cloudflare dashboard, under **Networking, Tunnels**, create a tunnel. Its page shows a Docker command ending in `--token` and a long string; put that string in `.env` as `CLOUDFLARE_TUNNEL_TOKEN`. The page waits for a connection before it lets you continue, which is the next step.
4. In **Container Manager**, open **Project**, click **Create**, choose the project folder and **Use existing docker-compose.yml**, and finish. Both containers start, and the tunnel shows as **Healthy** within a minute.
5. Back on the tunnel's page, add a route of type **Published application**, with the subdomain and domain you want, for example `iss.example.com`, and the service URL `http://iss_track_web:8000`. The site is up at that hostname straight away. On your own network it is also at `http://<nas-address>:8765`.
6. Under the domain's **SSL/TLS, Edge Certificates**, turn on **Always Use HTTPS**, so a visitor who types the bare hostname lands on the secure page, where the location button works.

To update to a newer version later, select the project in Container Manager, click **Stop**, then **Action, Build**. The compose file's `pull_policy: always` makes that fetch the newest image; without that line the NAS keeps reusing the image it already has.

Cloudflare's Web Analytics is optional and works as is. If it is on for the domain, Cloudflare adds its script to each page, and the site's content security policy allows that one script and the address it reports to. If you use it, say so to your visitors: the privacy page describes the public site, so edit it for yours.

Without a domain, DSM's own reverse proxy with a Let's Encrypt certificate on a `synology.me` name works too, but that means opening port 443 on your router and the site runs at your home IP address.

### On any Docker host

```
docker compose up -d
```

The image is published for `amd64` and `arm64` at `ghcr.io/mattbratt/iss_track_web`. Orbital elements are kept in the `data` folder next to the compose file.

### Locally, without Docker

```
git clone --recurse-submodules https://github.com/mattbratt/iss_track_web.git
cd iss_track_web
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m uvicorn server.main:app --port 8765
```

Then open `http://localhost:8765`. Browser geolocation works on `localhost` without HTTPS.

### The live video

Clicking the station opens NASA's live view from it, on YouTube. NASA restarts that stream every few months, each restart gets a new address, and the old address then says "Video unavailable" for good. Nothing announces the change.

Each version of the site has the current stream built in, which works until NASA's next restart. To have the site follow restarts by itself, give it a YouTube Data API key. With a key the server asks YouTube about once an hour which of NASA's streams is live, shows that one, and shows a short "No live video right now" notice when none is. The key is free and needs no billing account. A key is given 100 searches a day and the site uses 24.

1. Open [console.cloud.google.com](https://console.cloud.google.com), sign in, and create a project.
2. Under **APIs & Services, Library**, find **YouTube Data API v3** and click **Enable**.
3. Under **APIs & Services, Credentials**, click **Create credentials**, then **API key**.
4. Restrict the key: under **API restrictions** choose **Restrict key** and tick **YouTube Data API v3** only.
5. Put the key in `.env` beside the compose file as `YOUTUBE_API_KEY=your-key`. It is a secret, like the tunnel token. The compose file in this repository passes it to the site; one copied from an older version needs the `YOUTUBE_API_KEY` line under `environment` added by hand.
6. Start the project again. `/api/video` on your site then answers with `"source": "lookup"`.

If the lookup fails (a wrong key, the allowance used up, YouTube unreachable) the site keeps showing the last stream it found and tries again an hour later. The key is sent only to Google and is never written to the log.

### Settings

| Variable | Default | What it does |
|---|---|---|
| `YOUTUBE_API_KEY` | none | A YouTube Data API v3 key, so the site finds NASA's current live stream every hour. Without it the stream built into the version is shown |
| `ISS_TRACK_DATA_DIR` | `./data` | Where the cached orbital elements and the last live stream found are kept |
| `ISS_TRACK_TRUST_PROXY` | `0` | Set to `1` behind a tunnel or reverse proxy, so request limits apply per visitor rather than per proxy |

## Development

The orbit, pass, brightness and sky calculations are not in this repository. They are the integration's engine, included as a git submodule at `vendor/iss_track` and pinned to a released version, so the website and the integration always agree. The website computes no position of its own: the service calls the engine, and the page draws what the service returns.

```
pip install -r requirements.txt pytest
pytest tests
pytest vendor/iss_track/tests
```

To move to a newer engine, check out the new tag inside `vendor/iss_track` and commit the submodule change.

## Credits
- Made by [Matthew Baker](https://github.com/mattbratt).
- Uses [`sgp4`](https://github.com/brandon-rhodes/python-sgp4) for orbital calculations.
- Uses [`astronomy.browser.js`](https://github.com/cosinekitty/astronomy) for moon position calculations.
- Orbital elements courtesy of [Celestrak](https://celestrak.org/).
- Place search and sky forecasts from [Open-Meteo](https://open-meteo.com/).
- Map styling inspired by the European Space Agency's [ESA ISS Tracker](https://isstracker.spaceflight.esa.int/). Everything else is its own implementation.

## License
MIT License
