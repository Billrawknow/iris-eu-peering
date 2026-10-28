-- Runs once when the local PostGIS volume is first created.
-- Creates only the staging schema; nothing in iris_core is created or touched.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS iris_staging;
