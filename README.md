# Beer log

A self-hosted beer tasting log for you and your friends. Log what you drink, rate it out of 10, add a photo and flavour tags, and search it all later. Runs as two containers (Flask + MariaDB) with Docker Compose.

## Features

- **Reviews:** name, brewery, style, ABV, IBU, serving temperature, date, a written review and a photo.
- **Ratings:** 1 to 10 in half points, shown as a ring on each beer.
- **Flavour tags:** 20 presets plus your own custom tags.
- **Search and filters:** by text, style, minimum rating, date range, reviewer and tags. Sort by newest, best rated, A to Z, strongest or most bitter.
- **Multiple users:** everyone logs in with their own account and edits their own beers. Admins add users and can edit anything.
- **Public reading:** the log can be read without logging in. Only logged-in users can add reviews.
- **Phone friendly:** installs to the home screen as an app, with light and dark themes that follow your device.

## Quick start

You need Docker with Compose v2.

**1. Clone the repo**

```bash
git clone https://github.com/Freaky2112/Beer.git
cd Beer
```

**2. Create a `.env` file** in the repo root:

```dotenv
DB_ROOT_PASSWORD=change_me_root
DB_NAME=beerdb
DB_USER=beeruser
DB_PASSWORD=change_me_db
SECRET_KEY=change_me_long_random_string
ADMIN_USER=admin
ADMIN_PASSWORD=change_me_admin
```

Use your own values. Generate the secret key with `openssl rand -hex 32`.

**3. Check the proxy network and labels.** `docker-compose.yml` is set up for [Traefik](https://traefik.io/) and expects an external Docker network called `proxy`. Pick one:

- **With Traefik:** edit the two `Host(...)` rules in the `web` labels to your own domains.
- **Without Traefik:** delete the `labels:` block and the `proxy` entries under `networks:` (in `web` and at the bottom of the file).

If you keep the network but it doesn't exist yet, create it once:

```bash
docker network create proxy
```

**4. Start it**

```bash
docker compose up -d --build
```

Open **http://localhost:5000** and log in with `ADMIN_USER` and `ADMIN_PASSWORD`. From another machine, use the Docker host's IP address instead of `localhost`.

## Users and accounts

`ADMIN_USER` and `ADMIN_PASSWORD` only create the first admin, and only when there are no users yet. After that, accounts live in the database:

| Where | What you can do |
|---|---|
| `/account` | Change your own password (any user) |
| `/admin/users` | Add users, reset passwords, delete users (admins only) |

Changing `ADMIN_PASSWORD` in `.env` later has no effect. Deleting a user keeps their beers in the log, and from then on only admins can edit them. There is no public sign-up: an admin creates every account.

**Forgot the admin password?** Set a new one straight in the database (replace `NEW_PASSWORD` and `admin`):

```bash
docker compose exec -T web python -c "from werkzeug.security import generate_password_hash as g; print(\"UPDATE users SET password_hash='\" + g('NEW_PASSWORD') + \"' WHERE username='admin';\")" \
  | docker compose exec -T db sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" "$MARIADB_DATABASE"'
```

## Configuration

All settings are environment variables, set through `.env`.

| Variable | Purpose |
|---|---|
| `DB_ROOT_PASSWORD` | MariaDB root password |
| `DB_NAME` | Database name |
| `DB_USER` / `DB_PASSWORD` | Database login used by the app |
| `SECRET_KEY` | Signs login sessions. Changing it logs everyone out |
| `ADMIN_USER` / `ADMIN_PASSWORD` | Seeds the first admin (see above) |
| `COOKIE_SECURE` | Optional. Set to `1` to send the login cookie over HTTPS only. Add it under `web: environment:` in the compose file |

The web app is published on port `5000`. To change it, edit the host side of `"5000:5000"` in `docker-compose.yml`.

## Your data

Two named Docker volumes hold everything and survive rebuilds and `docker compose down`:

| Volume | Contents |
|---|---|
| `db_data` | The MariaDB database |
| `uploads` | Beer photos |

`docker compose down -v` deletes both volumes. Don't use `-v` unless you mean it.

**Back up the database**

```bash
docker compose exec -T db sh -c 'mariadb-dump -uroot -p"$MARIADB_ROOT_PASSWORD" "$MARIADB_DATABASE"' > beer-backup.sql
```

**Back up the photos** (check the volume name with `docker volume ls`; it is usually `beer_uploads`)

```bash
docker run --rm -v beer_uploads:/data -v "$PWD":/backup alpine tar czf /backup/uploads.tar.gz -C /data .
```

## Updating

```bash
git pull
docker compose up -d --build
```

Database changes run automatically at startup, so there is nothing to migrate by hand. When you upgrade an older single-user install, the existing beers are assigned to your admin user the first time the new version starts.

## Everyday commands

```bash
docker compose ps                # what is running
docker compose logs -f web       # follow the app logs
docker compose restart           # restart everything
docker compose down              # stop, keeping your data
```

## Security notes

- Put it behind HTTPS before exposing it to the internet, and set `COOKIE_SECURE=1` when you do.
- Never commit `.env`. If a secret was ever committed, rotate it, because deleting the file later does not remove it from Git history.
- Uploaded photos are re-encoded as JPEG and resized to at most 1200 px, with a 15 MB upload limit.
- Back up regularly, and keep Docker and the host up to date.

## Project layout

```
.
├── docker-compose.yml     # web + MariaDB (+ optional Traefik labels)
├── .env                   # your secrets (not committed)
└── app/
    ├── app.py             # Flask app: routes, accounts, database setup
    ├── Dockerfile
    ├── requirements.txt
    ├── templates/         # HTML pages
    └── static/            # CSS, font, images, app icons
```

Built with Flask, PyMySQL, Gunicorn and Pillow on Python 3.12, with MariaDB 11.

## Troubleshooting

**The page doesn't load.** Run `docker compose ps`, then `docker compose logs -f web`.

**`network proxy declared as external, but could not be found`.** Create it with `docker network create proxy`, or remove the Traefik network as described in Quick start.

**`Missing SECRET_KEY` (or `ADMIN_USER`, `ADMIN_PASSWORD`) in the web logs.** The `.env` file is missing or incomplete. Fix it, then run `docker compose up -d --build`.

**Port 5000 is already in use.** Change the host port in `docker-compose.yml`, for example `"5001:5000"`.

## License

Released under the [Unlicense](LICENSE): public domain, do what you like.
