# Apartment Maintenance Manager

A mobile-first web app (installable as a PWA) to manage apartment/society maintenance:
monthly maintenance collection from flats, daily expense tracking, watchman advances,
income & expense reporting with automatic month-to-month balance carry-forward, and a
separate module for one-off events (festival collections, etc.).

Built with **Python + Flask**. Data is stored in plain **JSON files** (`/data` folder) —
no database server required, fully human-readable, easy to back up (just copy the folder).

---

## Features

- **Flats master**: flat number, owner/tenant, contact, per-flat monthly maintenance amount, active/inactive status
- **Income types & Expense types**: fully configurable master lists (18 common recurring expense types pre-seeded: watchman salary, housekeeping, electricity, water tanker, lift AMC, etc.)
- **Monthly maintenance collection**: one click to generate that month's dues for every active flat, mark as paid, track pending dues
- **Daily expense tracking**: log every expense with type, amount, paid-to, and remarks
- **Watchman advance ledger**: track advances given and recoveries/deductions, always shows outstanding balance due
- **Live running balance**: shown on the dashboard at all times
- **Monthly Income & Expense report**: opening balance → income → expenses → closing balance, which automatically becomes next month's opening balance (carry-forward)
- **CSV export** of any month's report
- **6-month trend view**
- **Events module**: create an event, collect contributions (must be linked to a flat — no anonymous entries), log event expenses, see live event balance, and a dedicated **Event Report** showing exactly how much each flat contributed and what was spent — completely separate from the main society ledger
- **Four user roles**:
  - **Admin** — full access to everything, including Users
  - **Manager** — full access to everything except Users management
  - **Owner** — a flat owner's login; can **view** Home, Income, Expenses, Watchman & Events, plus the **Reports** section — all view-only (no adding/editing/deleting). No access to Users, Income Types, or Expense Types.
  - **Tenant** — a flat tenant's login; access is limited to the **Reports** section only (view-only)
- **Multi-user with roles** (admin / manager)
- **Mobile app look & feel**: bottom tab bar, card-based UI, installable to your phone's home screen (PWA)

---

## 1. Run locally

```bash
cd apartment_maintenance
python3 -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5000** on your phone/laptop browser.

**Default login:** `admin` / `admin123` — change this immediately (Users tab, or just add a new
admin user and delete the default one) once deployed.

---

## 2. Push to your GitHub repository (`rebbagirish-dev`)

From inside the `apartment_maintenance` folder:

```bash
git init
git add .
git commit -m "Initial commit: Apartment Maintenance Manager"
git branch -M main
git remote add origin https://github.com/rebbagirish-dev/apartment-maintenance-manager.git
git push -u origin main
```

> If the repository `apartment-maintenance-manager` doesn't exist yet, create it first at
> https://github.com/new under the `rebbagirish-dev` account (keep it empty — no README/license —
> so the push above doesn't conflict), then run the commands.

---

## 3. Deploy on Railway

1. Go to https://railway.app → **New Project** → **Deploy from GitHub repo**.
2. Select the `rebbagirish-dev/apartment-maintenance-manager` repository.
3. Railway will auto-detect Python and use the included `Procfile` / `railway.json`
   (`gunicorn app:app`) — no extra build config needed.
4. Under **Variables**, add:
   - `SECRET_KEY` = any long random string (this signs login sessions — required for production)
5. **Important — persistent storage:** Railway's filesystem is reset on every redeploy. Since this
   app stores data as JSON files, you must attach a **Volume** so your data survives deploys:
   - In the Railway service → **Settings → Volumes** → **Add Volume**
   - Mount path: `/app/data`
   - This makes the `data/` folder persistent across deploys/restarts.
6. Click **Deploy**. Railway will give you a public URL (`something.up.railway.app`) — open it on
   your phone and use **"Add to Home Screen"** to install it like a native app.

---

## 4. Project structure

```
apartment_maintenance/
├── app.py                 # All routes & business logic
├── db.py                  # JSON file "database" layer
├── seed.py                 # Seeds default admin user + master data on first run
├── requirements.txt
├── Procfile                # For Railway/Heroku-style start command
├── railway.json
├── data/                   # JSON data files (created automatically) — back this up!
├── static/
│   ├── css/style.css       # Mobile app styling
│   └── icons/               # PWA icons
└── templates/               # All pages (mobile app shell + bottom nav)
```

## 5. Backing up your data

Your entire dataset lives in the `data/` folder as plain JSON files — copy that folder anywhere
to back it up, or open the files directly in any text editor if you ever need to fix something
by hand.

## 6. Notes / things you may want to tune later

- The default admin password is `admin123` — change it right after first login.
- "Generate Monthly Maintenance Dues" creates one **unpaid** due per active flat for the chosen
  month, using that flat's configured maintenance amount — then you mark each as paid as
  collections come in.
- Watchman advances reduce the overall cash balance shown on the dashboard (money paid out is
  money paid out); recoveries add it back. This is separate from your recurring "Watchman Salary"
  expense entries.
