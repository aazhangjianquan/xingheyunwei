-- MySQL 8 additive upgrade. API startup performs these checks idempotently.
-- Run this standalone script once only; it does not alter existing messages.
USE yunweishenji;
ALTER TABLE notification_logs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE notification_logs ADD COLUMN next_attempt_at DATETIME NULL;
ALTER TABLE notification_logs ADD COLUMN dispatch_ready BOOLEAN NOT NULL DEFAULT 0;
CREATE INDEX ix_notifications_dispatch ON notification_logs (status, next_attempt_at, id);
