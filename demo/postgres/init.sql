-- The API connects as a normal role, so a connection leak cannot eat the slots
-- Postgres reserves for superusers. Monitoring and on-call stay able to log in.
CREATE ROLE app LOGIN PASSWORD 'app';
