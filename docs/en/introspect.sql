-- Dumps the catalog/audit structure as JSON for build.py:
--   psql -At -f docs/en/introspect.sql > docs/en/schema_snapshot.json
SELECT jsonb_pretty(jsonb_build_object(
  'versions', jsonb_build_object('postgres', version(), 'postgis', postgis_lib_version(),
     'citydb', (SELECT citydb_pkg.citydb_version())::text),
  'tables', (SELECT jsonb_agg(t ORDER BY (t->>'schema'), (t->>'kind') DESC, (t->>'name')) FROM (
    SELECT jsonb_build_object(
      'schema', n.nspname, 'name', c.relname,
      'kind', CASE c.relkind WHEN 'r' THEN 'table' WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized view' END,
      'comment', obj_description(c.oid, 'pg_class'),
      'columns', (SELECT jsonb_agg(jsonb_build_object('name', a.attname, 'type', format_type(a.atttypid, a.atttypmod),
            'not_null', a.attnotnull, 'default', pg_get_expr(d.adbin, d.adrelid), 'comment', col_description(c.oid, a.attnum))
            ORDER BY a.attnum)
          FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
          WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped),
      'constraints', (SELECT jsonb_agg(jsonb_build_object('type', k.contype, 'name', k.conname, 'def', pg_get_constraintdef(k.oid),
            'ref_table', CASE WHEN k.contype = 'f' THEN k.confrelid::regclass::text END))
          FROM pg_constraint k WHERE k.conrelid = c.oid),
      'indexes', (SELECT jsonb_agg(pg_get_indexdef(i.indexrelid)) FROM pg_index i WHERE i.indrelid = c.oid),
      'triggers', (SELECT jsonb_agg(jsonb_build_object('name', tg.tgname, 'def', pg_get_triggerdef(tg.oid)))
          FROM pg_trigger tg WHERE tg.tgrelid = c.oid AND NOT tg.tgisinternal)
    ) AS t
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('catalog', 'audit') AND c.relkind IN ('r', 'v', 'm')) s),
  'functions', (SELECT jsonb_agg(jsonb_build_object('schema', n.nspname, 'name', p.proname,
        'args', pg_get_function_arguments(p.oid), 'returns', pg_get_function_result(p.oid),
        'security_definer', p.prosecdef, 'comment', obj_description(p.oid, 'pg_proc')) ORDER BY n.nspname, p.proname)
     FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname IN ('catalog', 'audit')),
  'roles', (SELECT jsonb_agg(jsonb_build_object('name', r.rolname, 'login', r.rolcanlogin,
        'member_of', (SELECT jsonb_agg(g.rolname) FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid WHERE m.member = r.oid)) ORDER BY r.rolname)
     FROM pg_roles r WHERE r.rolname LIKE 'twin\_%')
));
