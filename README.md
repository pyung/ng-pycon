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
| `npm run build` | Build CSS and vendor Alpine (what the Docker image runs) |

---

## Accessibility and performance

Both are checked, not assumed. `manage.py test` includes the audit over rendered
pages, so CI fails if a page loses its skip link, its `<main>` landmark, its title
or its image alt text. Run it against real content with:

| Command | Description |
|---------|-------------|
| `python manage.py audit_frontend` | Audit every live page and app view; errors only |
| `python manage.py audit_frontend --warnings` | Include warnings (image sizing, heading order, third-party assets) |
| `python manage.py audit_frontend --palette` | Colour contrast across the theme palettes |
| `python manage.py audit_frontend --strict` | Exit non-zero on any error |
| `python manage.py rewrite_dev_urls` | Find `http://127.0.0.1:8000/...` links saved into page content (`--apply` to fix) |

---

## Ticketing

| Command | Description |
|---------|-------------|
| `python manage.py issue_free_tickets --dry-run` | Who would get a complimentary ticket: speakers, volunteers, grant recipients |
| `python manage.py issue_free_tickets` | Issue them. Idempotent, so safe to run again |
| `python manage.py notify_ticket_waitlist --dry-run` | Who would be told a place has opened up |
| `python manage.py notify_ticket_waitlist` | Tell them, at most one per free place |

Sale windows, the transfer cutoff, the refund policy and the invoice details all
live on **Tickets → Settings** in the admin, one record per edition, so none of
them needs a deploy.

---

## Volunteers

The call for volunteers, the roster, and the certificates afterwards.

| Path | Who |
|------|-----|
| `/volunteers/` | Anyone — what volunteering involves, and the teams |
| `/volunteers/apply/` | Signed in — apply, or edit until a decision is made |
| `/volunteers/my-application/` | The volunteer — status, team, lead, shifts, certificate |
| `/volunteers/coordinate/` | Volunteer coordinator or organizer — the queue and the gaps |
| `/volunteers/coordinate/shifts/` | Build the roster, and publish it |

**Accepting somebody does four things at once**, in `volunteers/services.decide`:
grants the volunteer role for the edition, issues the free ticket, places them on a
team with a named lead, and emails them. Any other outcome revokes the role and
releases their shifts. All of it is idempotent, so re-accepting to fix a team
assignment does not issue a second ticket.

The roster is hidden behind **Shifts published** until a coordinator publishes it,
because rosters get rebuilt several times and a volunteer who reads a draft turns up
at the wrong hour. Publishing emails everyone who has a shift, and nobody who does
not. The availability question on the form is built from the **edition's own dates**
plus the set-up and pack-down days in the settings, so moving the conference moves
the question.

---

Every attendee's place carries a ten-character check-in code and a QR code of it.
The QR is generated by `pyconng/apps/tickets/qr.py` -- written rather than
installed, because one short uppercase URL does not justify a dependency for a team
that finds deploying expensive. It is checked against the specification's own worked
example and by decoding its own output; **scan one with a phone before an event**,
which is the part no test can do.

The audit reads markup, so it catches what markup can show: missing labels, dead
links, unnamed controls, blocking scripts, development URLs in published content.
It cannot judge focus order once JavaScript has moved things, or text over a
photograph. Those still need a person.

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
