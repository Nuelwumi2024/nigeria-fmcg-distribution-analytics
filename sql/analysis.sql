-- Analysis queries (DuckDB). `src/analysis.py` runs each block and writes it to data/processed/<name>.csv
-- Grain: sales = one row per week x outlet x SKU with units sold.

-- name: sales_enriched
CREATE OR REPLACE VIEW sales_enriched AS
SELECT s.week_start::DATE AS week_start,
       strftime(s.week_start::DATE, '%Y-%m') AS month,
       dense_rank() OVER (ORDER BY s.week_start::DATE) AS week_no,
       s.outlet_id, s.sku_id, s.units, s.net_price, s.unit_cost,
       s.units * s.net_price               AS revenue,
       s.units * (s.net_price - s.unit_cost) AS gross_margin,
       o.region, o.channel, o.rep_id, p.category, p.sku_name
FROM sales s
JOIN outlets  o USING (outlet_id)
JOIN products p USING (sku_id);

-- name: monthly
SELECT month,
       sum(revenue)::DOUBLE                         AS revenue,
       sum(units)                                   AS units,
       sum(gross_margin) / sum(revenue) * 100       AS gm_pct,
       count(DISTINCT outlet_id)                    AS active_outlets,
       count(DISTINCT week_no)                      AS weeks
FROM sales_enriched
GROUP BY month ORDER BY month;

-- name: like_for_like
-- Same SKU, first 13 weeks vs last 13 weeks: how far did price rise against cost?
WITH w AS (SELECT max(week_no) AS last_wk FROM sales_enriched),
per AS (
    SELECT sku_id, sku_name, category,
           CASE WHEN week_no <= 13 THEN 'start' WHEN week_no > last_wk - 13 THEN 'end' END AS period,
           sum(revenue) / sum(units)                       AS net_price,
           sum(units * unit_cost) / sum(units)             AS unit_cost,
           sum(units)                                      AS units
    FROM sales_enriched, w
    WHERE week_no <= 13 OR week_no > last_wk - 13
    GROUP BY ALL)
SELECT s.sku_id, s.sku_name, s.category,
       (e.net_price / s.net_price - 1) * 100 AS price_change_pct,
       (e.unit_cost / s.unit_cost - 1) * 100 AS cost_change_pct,
       s.units AS start_units
FROM per s JOIN per e ON s.sku_id = e.sku_id AND s.period = 'start' AND e.period = 'end';

-- name: pareto
WITH t AS (SELECT sku_id, sku_name, category, sum(revenue) AS revenue, sum(gross_margin) AS gross_margin
           FROM sales_enriched GROUP BY ALL),
c AS (SELECT *, sum(revenue) OVER (ORDER BY revenue DESC) / sum(revenue) OVER () AS cum_share,
             row_number() OVER (ORDER BY revenue DESC) AS rnk
      FROM t)
SELECT *, CASE WHEN cum_share - revenue / sum(revenue) OVER () < 0.80 THEN 'A'
               WHEN cum_share - revenue / sum(revenue) OVER () < 0.95 THEN 'B' ELSE 'C' END AS abc_class,
       gross_margin / revenue * 100 AS gm_pct
FROM c ORDER BY rnk;

-- name: region_channel
SELECT region, channel,
       count(DISTINCT outlet_id)                         AS outlets,
       sum(revenue)::DOUBLE                              AS revenue,
       sum(gross_margin) / sum(revenue) * 100            AS gm_pct,
       sum(revenue) / count(DISTINCT outlet_id) / count(DISTINCT week_no) AS revenue_per_outlet_week
FROM sales_enriched GROUP BY ALL ORDER BY revenue DESC;

-- name: outlet_concentration
WITH o AS (SELECT outlet_id, sum(revenue) AS revenue FROM sales_enriched GROUP BY 1),
r AS (SELECT *, ntile(10) OVER (ORDER BY revenue DESC) AS decile FROM o)
SELECT decile, count(*) AS outlets, sum(revenue)::DOUBLE AS revenue,
       sum(revenue) / sum(sum(revenue)) OVER () * 100 AS share_pct
FROM r GROUP BY decile ORDER BY decile;

-- name: lapsed_outlets
-- Baseline-active = bought in >= 6 of weeks 30-60. Lapsed = then bought nothing in the last 12 weeks.
WITH w AS (SELECT max(week_no) AS last_wk FROM sales_enriched),
base AS (SELECT outlet_id, count(DISTINCT week_no) AS weeks_bought, sum(revenue) / 31 AS avg_week_rev
         FROM sales_enriched WHERE week_no BETWEEN 30 AND 60 GROUP BY 1 HAVING count(DISTINCT week_no) >= 6),
recent AS (SELECT outlet_id, sum(revenue) AS recent_rev FROM sales_enriched, w
           WHERE week_no > last_wk - 12 GROUP BY 1)
SELECT b.outlet_id, o.region, o.channel, o.rep_id, b.avg_week_rev * 52 AS annual_revenue_at_risk
FROM base b JOIN outlets o USING (outlet_id)
LEFT JOIN recent r USING (outlet_id)
WHERE coalesce(r.recent_rev, 0) = 0
ORDER BY annual_revenue_at_risk DESC;

-- name: rep_attainment
WITH m AS (SELECT rep_id, month, sum(revenue) AS revenue FROM sales_enriched GROUP BY 1, 2)
SELECT t.rep_id, r.region, t.month, m.revenue::DOUBLE AS revenue, t.target_revenue,
       m.revenue / t.target_revenue * 100 AS attainment_pct
FROM targets t JOIN m ON m.rep_id = t.rep_id AND m.month = t.month
JOIN reps r ON r.rep_id = t.rep_id
WHERE t.month IN (SELECT month FROM sales_enriched GROUP BY month HAVING count(DISTINCT week_no) >= 4)
ORDER BY t.month, t.rep_id;

-- name: stockout_loss
-- Estimated lost units = trailing-26-week baseline (stock-out weeks excluded) x share of the week out of stock.
WITH base AS (
    SELECT st.week_start::DATE AS week_start, st.region, st.sku_id, st.units_sold, st.stockout_days,
           avg(CASE WHEN st.stockout_days = 0 THEN st.units_sold END)
               OVER (PARTITION BY st.region, st.sku_id ORDER BY st.week_start
                     ROWS BETWEEN 26 PRECEDING AND 1 PRECEDING) AS baseline_units
    FROM stock st),
price AS (SELECT sku_id, sum(revenue) / sum(units) AS avg_net_price FROM sales_enriched GROUP BY 1)
SELECT b.week_start, b.region, b.sku_id, b.stockout_days, b.baseline_units,
       b.baseline_units * b.stockout_days / 7.0                 AS est_lost_units,
       b.baseline_units * b.stockout_days / 7.0 * p.avg_net_price AS est_lost_revenue
FROM base b JOIN price p USING (sku_id)
WHERE b.stockout_days > 0 AND b.baseline_units IS NOT NULL
ORDER BY est_lost_revenue DESC;
