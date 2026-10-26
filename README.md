# 🍺 Beer DB

A self-hosted beer database web application, packaged with Docker Compose.

Beer DB runs as a web application backed by MariaDB. Docker Compose builds the application from `./app`, starts the database, waits for the database health check, and exposes the web interface on port **8080**. Uploaded files and database data are stored in persistent Docker volumes.

## ✨ Overview

- **Web application:** built locally from `./app`
- **Database:** MariaDB 11
- **Deployment:** Docker Compose
- **Web access:** `http://localhost:8080`
- **Persistent storage:** database and uploads use Docker volumes
- **License:** [Unlicense](LICENSE)

## 🧰 Requirements

- Docker Engine
- Docker Compose v2 (`docker compose`)
- A free host port **8080**

## 🚀 Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/Freaky2112/Beer.git
cd Beer
```

### 2. Configure environment variables

Create a `.env` file in the repository root. Add the variables used by `docker-compose.yml`:

```dotenv
DB_ROOT_PASSWORD=replace_with_a_strong_root_password
DB_NAME=beerdb
DB_USER=beeruser
DB_PASSWORD=replace_with_a_strong_database_password
SECRET_KEY=replace_with_a_long_random_secret
ADMIN_USER=admin
ADMIN_PASSWORD=replace_with_a_strong_admin_password
```

Replace every example value with your own secure values before starting the application. Keep `.env` private and **never commit real passwords or secret keys** to Git.

Generate a random secret key on Linux with:

```bash
openssl rand -hex 32
```

### 3. Start Beer DB

Build the web image and start the services in the background:

```bash
docker compose up -d --build
```

### 4. Open the web interface

Visit:

**http://localhost:8080**

If you are accessing the application from another machine, replace `localhost` with the IP address or hostname of the Docker host.

## ⚙️ Configuration

The following environment variables are read by the Compose configuration:

| Variable | Purpose |
|---|---|
| `DB_ROOT_PASSWORD` | MariaDB root password |
| `DB_NAME` | Application database name |
| `DB_USER` | Database user for the application |
| `DB_PASSWORD` | Password for the application database user |
| `SECRET_KEY` | Application secret key |
| `ADMIN_USER` | Administrator username configured for the application |
| `ADMIN_PASSWORD` | Administrator password configured for the application |

The Compose file maps host port `8080` to container port `5000`. The database service is not published directly to the host; the web service connects to it over the Compose network.

## 🗄️ Persistent Data

Docker Compose creates two named volumes:

| Volume | Stores |
|---|---|
| `db_data` | MariaDB database files |
| `uploads` | Uploaded application files |

These volumes persist when containers are recreated. Removing containers alone does not remove named volumes. However, commands that explicitly remove volumes can permanently delete application data.

## 🛠️ Useful Commands

View service status:

```bash
docker compose ps
```

Follow logs:

```bash
docker compose logs -f
```

Follow only the web application logs:

```bash
docker compose logs -f web
```

Follow only the database logs:

```bash
docker compose logs -f db
```

Restart the services:

```bash
docker compose restart
```

Stop the services while keeping persistent volumes:

```bash
docker compose down
```

Rebuild and restart after application changes:

```bash
docker compose up -d --build
```

## 🔒 Security Notes

- Use strong, unique values for all passwords and secrets.
- Do not expose the application publicly without considering HTTPS, authentication, firewall rules, and updates.
- Back up the database and uploaded files regularly.
- Do not commit `.env` files containing real credentials. If a secret has been committed, rotate it; deleting it from the latest commit alone may not remove it from Git history.
- Keep Docker and the underlying host up to date.

## 🧯 Troubleshooting

**The page does not load**

Check that the services are running with `docker compose ps` and inspect the logs with `docker compose logs -f`.

**The database is still starting**

The web service is configured to wait for the database health check. Check `docker compose logs -f db` and confirm the database environment variables are set correctly.

**Port 8080 is already in use**

Change the host-side port in `docker-compose.yml`. For example, change `8080:5000` to `8081:5000`, recreate the services, and visit `http://localhost:8081`.

**Configuration changes do not take effect**

After changing environment values, recreate the services:

```bash
docker compose up -d
```

For changes to the application image, rebuild it:

```bash
docker compose up -d --build
```

## 📄 License

This project is released under the [Unlicense](LICENSE), placing it in the public domain to the extent permitted by law.

---

Made for beer lovers and self-hosters. 🍻
