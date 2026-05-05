# Sample Data Needed for BeyondTrust Connector Validation

Place representative source samples here to enable local dry-run validation and field-level mapping checks.

Recommended sample files:

1. `managed_accounts.json`
- API response sample from `BEYONDTRUST_MANAGED_ACCOUNTS_ENDPOINT`
- Include at least 5 records

2. `managed_systems.json`
- API response sample from `BEYONDTRUST_DEVICES_ENDPOINT`
- Include at least 5 records

3. `applications.json`
- API response sample from `BEYONDTRUST_APPLICATIONS_ENDPOINT`
- Include at least 5 records

4. `access_assignments.json`
- API response sample from `BEYONDTRUST_ACCESS_ASSIGNMENTS_ENDPOINT`
- Include at least 10 assignments
- Include both user and group assignments if applicable

Optional:
- `users.json` from `BEYONDTRUST_USERS_ENDPOINT`
- `groups.json` from `BEYONDTRUST_GROUPS_ENDPOINT`

After adding samples, run:

```bash
cd integrations/beyondtrust
./venv/bin/python3 beyondtrust.py --data-dir ./samples --dry-run --save-json --log-level DEBUG
```
