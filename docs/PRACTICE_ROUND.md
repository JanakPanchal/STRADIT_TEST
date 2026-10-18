# Practice round checklist (run before any real drive)

Use 5–10 volunteers, on the actual lab machines/browsers, with a throw-away practice test (3 questions, 5 minutes).

- [ ] Everyone can sign in; forced password change works for bulk-created accounts.
- [ ] Fullscreen starts on every machine; note any browser where it does not.
- [ ] Tab switch / Alt-Tab produce **one** warning each (not three).
- [ ] Reaching the warning limit auto-submits; the candidate sees a clear message.
- [ ] Close the tab mid-test, reopen, **Resume** — timer continues, answers are kept.
- [ ] Let one test run out of time — it submits itself; the admin sees `timeout`.
- [ ] Coding: Run samples and Submit work (Judge0 reachable, correct language ids).
- [ ] Admin: results, proctoring log, CSV export and shortlist filter look right.
- [ ] Test a 200-candidate burst on staging (`locust`, see README) **against PostgreSQL**; p95 < 300 ms.
- [ ] Take a database backup and restore it once (`scripts/backup.sh`).
- [ ] Delete the practice test and practice candidates before the real drive.
