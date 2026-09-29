-- ===========================================================================
-- Coastal economies and the offshore wind pipeline
-- ---------------------------------------------------------------------------
-- Tables:
--   wind_farms  one row per project, with its assigned NUTS3 region
--   regions     one row per region that has at least one project
--   economics   one row per region: GDP, population, unemployment
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- 1. What is in the database
-- ---------------------------------------------------------------------------

SELECT COUNT(*) AS n_projects FROM wind_farms;

SELECT COUNT(*) AS n_regions FROM regions;

SELECT COUNT(*) AS n_with_gdp FROM economics WHERE gdp_meur IS NOT NULL;


-- ---------------------------------------------------------------------------
-- 2. Capacity by status
-- ---------------------------------------------------------------------------
-- GROUP BY collapses many rows into one per group. The aggregate functions
-- (COUNT, SUM, AVG) then describe each group. Anything in the SELECT list
-- that is not aggregated must appear in the GROUP BY.

SELECT
    status,
    COUNT(*)              AS project_count,
    ROUND(SUM(power_mw))  AS total_mw,
    ROUND(AVG(power_mw))  AS mean_mw
FROM wind_farms
GROUP BY status
ORDER BY total_mw DESC;


-- ---------------------------------------------------------------------------
-- 3. The join: capacity per region
-- ---------------------------------------------------------------------------
-- A JOIN combines rows from two tables where a key matches. Here many farms
-- share one region, so this is a many-to-one join. Rows with no region are
-- excluded explicitly rather than by accident.

SELECT
    r.nuts3_id,
    r.nuts3_name,
    r.country_code,
    COUNT(*)              AS project_count,
    ROUND(SUM(w.power_mw)) AS total_mw
FROM wind_farms AS w
JOIN regions AS r ON r.nuts3_id = w.nuts3_id
WHERE w.nuts3_id IS NOT NULL
GROUP BY r.nuts3_id, r.nuts3_name, r.country_code
ORDER BY total_mw DESC
LIMIT 20;


-- ---------------------------------------------------------------------------
-- 4. Three tables: capacity against the size of the economy
-- ---------------------------------------------------------------------------
-- The question the project exists to answer. GDP is in millions of euros, so
-- dividing by 1000 gives billions, and the ratio is megawatts of proposed
-- capacity per billion euros of regional output.

SELECT
    r.nuts3_name,
    r.country_code,
    ROUND(SUM(w.power_mw))                          AS total_mw,
    e.gdp_meur,
    ROUND(SUM(w.power_mw) / (e.gdp_meur / 1000.0), 2) AS mw_per_bn_eur_gdp
FROM wind_farms AS w
JOIN regions   AS r ON r.nuts3_id = w.nuts3_id
JOIN economics AS e ON e.nuts3_id = w.nuts3_id
WHERE e.gdp_meur > 0
GROUP BY r.nuts3_id, r.nuts3_name, r.country_code, e.gdp_meur
ORDER BY mw_per_bn_eur_gdp DESC
LIMIT 20;


-- ---------------------------------------------------------------------------
-- 5. A CTE and a window function
-- ---------------------------------------------------------------------------
-- WITH ... AS (...) names an intermediate result, called a common table
-- expression, so the query reads top to bottom instead of nesting inside
-- itself.
--
-- RANK() OVER (PARTITION BY ... ORDER BY ...) ranks rows without collapsing
-- them. GROUP BY would leave one row per country; this leaves every region
-- and adds its position within its own country. That distinction is the
-- single most useful thing to be able to explain about window functions.

WITH regional_capacity AS (
    SELECT
        nuts3_id,
        COUNT(*)       AS project_count,
        SUM(power_mw)  AS total_mw
    FROM wind_farms
    WHERE nuts3_id IS NOT NULL
    GROUP BY nuts3_id
)
SELECT
    r.nuts3_name,
    r.country_code,
    ROUND(c.total_mw)                                     AS total_mw,
    ROUND(c.total_mw / (e.gdp_meur / 1000.0), 2)          AS mw_per_bn_eur_gdp,
    RANK() OVER (
        PARTITION BY r.country_code
        ORDER BY c.total_mw DESC
    )                                                     AS rank_in_country
FROM regional_capacity AS c
JOIN regions   AS r ON r.nuts3_id = c.nuts3_id
JOIN economics AS e ON e.nuts3_id = c.nuts3_id
WHERE e.gdp_meur > 0
ORDER BY r.country_code, rank_in_country;


-- ---------------------------------------------------------------------------
-- 6. Coverage check
-- ---------------------------------------------------------------------------
-- How much capacity is lost at each stage of the joins. Run this before
-- believing any total above, and put the answer in the README.

SELECT
    'all projects'            AS stage,
    COUNT(*)                  AS n,
    ROUND(SUM(power_mw))      AS total_mw
FROM wind_farms

UNION ALL

SELECT
    'assigned to a region',
    COUNT(*),
    ROUND(SUM(power_mw))
FROM wind_farms
WHERE nuts3_id IS NOT NULL

UNION ALL

SELECT
    'region has GDP data',
    COUNT(*),
    ROUND(SUM(w.power_mw))
FROM wind_farms AS w
JOIN economics AS e ON e.nuts3_id = w.nuts3_id
WHERE e.gdp_meur > 0;
