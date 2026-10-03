-- Login roles for the application. Passwords come from env (psql -v app_pw=... -v api_pw=...). Idempotent.
SELECT format('CREATE ROLE twin_app LOGIN IN ROLE twin_admin') WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'twin_app') \gexec
SELECT format('CREATE ROLE twin_api LOGIN IN ROLE twin_ro')    WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'twin_api') \gexec
SELECT format('ALTER ROLE twin_app PASSWORD %L', :'app_pw') \gexec
SELECT format('ALTER ROLE twin_api PASSWORD %L', :'api_pw') \gexec
-- Django (admin, sessions, users) lives in schema app; the app role may create tables there.
GRANT USAGE, CREATE ON SCHEMA app TO twin_app;
ALTER ROLE twin_app SET search_path = app, catalog, citydb, public;
ALTER ROLE twin_api SET search_path = catalog, citydb, public;
