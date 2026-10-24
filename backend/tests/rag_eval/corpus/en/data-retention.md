# Data retention guide

## Backups
Database backups are taken every night and kept for 35 days. Backups are encrypted at rest and stored in a separate region.

## Audit logs
Audit logs record who changed what and when. They are retained for 400 days and cannot be edited or deleted by administrators.

## Deleting a workspace
When a workspace is deleted its data enters a 14 day grace period. During the grace period an owner can restore it. After that the data is permanently erased.

## Exporting data
Owners can export a workspace as a ZIP archive containing CSV files and the original uploads. Large exports are prepared in the background and a download link is emailed.
