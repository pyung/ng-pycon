# PyCon Nigeria

The official website and conference management platform for **PyCon Nigeria** — the premier Python conference in Nigeria.

## Tech Stack

- **Backend:** Django 5.2, Wagtail CMS
- **Frontend:** Tailwind CSS, Alpine.js
- **Database:** PostgreSQL (production) or SQLite (local dev)
- **Payments:** Paystack

## Features

- **Multi-year support** — Content for 2024, 2025, 2026 with year-specific themes
- **Tickets** — Ticket types, purchase flow, Paystack integration
- **Call for Proposals (CFP)** — Submit talks/workshops, reviewer workflow, program chair tools
- **Travel Grants** — Apply for grants, reviewer scoring, finance tracking
- **User Dashboard** — Role-based hub for attendees, reviewers, chairs, and admins

## Prerequisites

- **Python 3.10+**
- **Node.js 18+** (for Tailwind CSS)
- **PostgreSQL** (optional for local dev; SQLite works out of the box)

---

## Quick Start (Local Development)

### 1. Clone the repository

```bash
git clone https://github.com/pyung/ng-pycon.git
cd ng-pycon
```

### 2. Create a virtual environment

```bash
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Install Node dependencies (for Tailwind CSS)

```bash
npm install
```

### 5. Set up environment variables

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```env
# Use SQLite for local dev (no PostgreSQL needed)
DB=sqlite

# Django
SECRET_KEY=your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
```

For PostgreSQL instead:

```env
DB_NAME=pyconng_db
DB_USER=pyconng_user
DB_PASSWORD=pyconng_password
DB_HOST=localhost
DB_PORT=5432
```

### 6. Run migrations

```bash
python manage.py migrate
```

### 7. Create a superuser (optional)

```bash
python manage.py createsuperuser
```

### 8. Build CSS (first time)

```bash
npm run build-css
```

### 9. Start the development server

```bash
npm run dev
```

This runs:

- **Tailwind CSS** in watch mode (rebuilds on file changes)
- **Django development server** at http://127.0.0.1:8000/

> **Note:** On first run, you may need to set up the Wagtail site at http://127.0.0.1:8000/admin/ (create a site and root page if prompted).

---

## Running the App

| Command | Description |
|---------|-------------|
| `npm run dev` | Start Django + Tailwind watch (recommended for development) |
| `python manage.py runserver` | Start Django only (use after `npm run build-css`) |
| `npm run build-css` | Build CSS once (for production) |
| `npm run build-css-watch` | Watch and rebuild CSS only |

---

## Key URLs

| Path | Description |
|------|-------------|
| `/` | Homepage (current year) |
| `/admin/` | Wagtail CMS admin |
| `/django-admin/` | Django admin |
| `/dashboard/` | User dashboard (login required) |
| `/tickets/` | Ticket purchase |
| `/cfp/` | Call for Proposals |
| `/grants/` | Travel Grant applications |
| `/accounts/login/` | Sign in |
| `/accounts/signup/` | Create account |

---

## Project Structure

```
ng-pycon/
├── manage.py
├── requirements.txt
├── package.json
├── .env.example
├── pyconng/                 # Django project
│   ├── settings/            # base, dev, production
│   ├── apps/                # Django apps (tickets, cfp, grants, dashboard)
│   ├── static/              # CSS, JS, images
│   └── templates/           # Shared templates
├── home/                    # Wagtail home & pages
├── search/                  # Search
└── to_docs/                 # Documentation
```

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DB` | `sqlite` for SQLite, anything else for PostgreSQL | — |
| `DB_NAME` | PostgreSQL database name | `pyconng_db` |
| `DB_USER` | PostgreSQL user | `pyconng_user` |
| `DB_PASSWORD` | PostgreSQL password | `pyconng_password` |
| `DB_HOST` | PostgreSQL host | `localhost` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `SECRET_KEY` | Django secret key | — |
| `DEBUG` | Debug mode | `True` (dev) |
| `ALLOWED_HOSTS` | Comma-separated hosts | `localhost,127.0.0.1` |
| `PAYSTACK_SECRET_KEY` | Paystack API secret | — |
| `PAYSTACK_PUBLIC_KEY` | Paystack public key | — |

---

## License

MIT
