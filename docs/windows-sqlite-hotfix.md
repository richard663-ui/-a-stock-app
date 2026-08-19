# Windows SQLite self-test hotfix

Root cause: Python's `sqlite3.Connection` context manager commits/rolls back but does not close the database handle. On Windows this left the temporary `pred.sqlite3` file locked when `TemporaryDirectory` attempted cleanup, producing WinError 32.

Fix: all `PredictionJournal` database operations now use an explicit context manager that commits/rolls back and always calls `close()` in `finally`.

Regression guard: GitHub Actions now runs the deterministic V18 self-test on `windows-latest` as well as Linux, so this Windows-specific file-handle bug is caught before changes reach `main`.
