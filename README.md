# SLSU-JGE Alumni Management System

Southern Luzon State University — Judge Guillermo Eleazar Campus  
**Stack:** Python Flask · SQLite · Tailwind CSS (CDN)

## Features

- **Public Graduate Directory** — no login required. Search by name/student number, filter by department and year range. Only name, student number, department, and graduation year are shown (no contact info).
- **Auto-Verification on Registration** — on submit, the system matches Full Name + Student Number + Year Graduated against the independent `graduate_master` table.
  - Match → status `Verified — Pending Admin Approval`
  - No match → status `Unverified — Requires Manual Review`
  - Live check indicator on the registration form (`/api/verify`).
- **Admin Panel** — Master Graduate DB (add/edit/delete + CSV bulk upload), Registration Approvals (approve/reject/mark verified), Manage Users (deactivate/activate), Homecoming Settings (per-batch or all-batch events), Announcements (auto-notifies all approved alumni).
- **Alumni Dashboard** — News Feed (post, like, comment, reply), My Homecoming (batch-specific events), Alumni Directory (logged-in members see employment/location/email + can message), private Messages, Notifications, and editable Profile.

## Quick Start

```bash
pip install -r requirements.txt
python app.py
# open http://localhost:5000
```

The SQLite database `slsu_alumni.db` is created and seeded automatically on first run.

## Demo Accounts

| Role   | Email               | Password  |
|--------|---------------------|-----------|
| Admin  | admin@slsu.edu.ph   | admin123  |
| Alumni | alumni@slsu.edu.ph  | alumni123 |

**Change these and set `SECRET_KEY` in production.**

## CSV Bulk Upload Format

Header row required: `full_name,student_number,department,year_graduated`  
See `sample_graduates.csv`. Existing student numbers are skipped (INSERT OR IGNORE).

## Database Tables

`users`, `graduate_master`, `posts`, `comments`, `likes`, `comment_likes`, `messages`, `notifications`, `homecoming_events`, `announcements`.

The master graduate list is fully independent from user accounts — a graduate can appear in the public directory without ever registering an account.
