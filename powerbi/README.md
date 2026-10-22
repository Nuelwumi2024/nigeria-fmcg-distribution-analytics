# Power BI build notes

This folder holds DAX measures and a model sketch. It does not contain a `.pbix` file.

1. Generate the data: `python src/gen_data.py --out data/raw`.
2. Get data → Text/CSV: `sales`, `outlets`, `products`, `reps`, `stock`, `targets`.
3. Relationships: `sales[outlet_id]` → `outlets`; `sales[sku_id]` → `products`; `outlets[rep_id]` → `reps`; `targets[rep_id]` → `reps`. Add a Date table on `sales[week_start]`.
4. Paste [`measures.dax`](measures.dax).

| Page | Visuals |
|---|---|
| Margin | Line: GM % by month. Table: SKU, Avg Net Price, Avg Unit Cost for two date ranges. |
| Products | Pareto: columns = Total Revenue by SKU, line = Cumulative Revenue %. |
| Outlets | Matrix: region × channel with Revenue per Outlet and GM %. Table of Silent Outlet = 1. |
| Supply | Heatmap: Stockout Days by region × week. Card: Days of Cover. |
| Sales team | Bar: Attainment % by rep, with a 100% reference line. |

> The DAX measures have not been validated in Power BI Desktop yet; check them against the Python results (see the main README) when you build the report.
