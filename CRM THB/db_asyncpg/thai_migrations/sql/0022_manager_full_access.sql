-- Managers added through /mgr have full CRM editing access.
-- Keep a single permission source for Telegram, OIDC, API and UI.
INSERT INTO role_permissions(role, permission)
SELECT 'manager', permission FROM role_permissions WHERE role = 'admin'
ON CONFLICT DO NOTHING;
INSERT INTO role_permissions(role, permission) VALUES ('manager', 'cash.execute')
ON CONFLICT DO NOTHING;
