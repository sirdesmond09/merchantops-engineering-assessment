# Engineering report

## 1. Findings and priorities

I read the backend and the Vue frontend, then measured the three screens an operator uses: the product list page, the product page and the product activity page. For each one I checked the HTTP requests the browser makes and the SQL queries the server runs, and confirmed the request counts in the browser's Network tab.

**Main finding:** the database is not slow. The application did far more work than a screen needs: too many HTTP requests from the browser and too many SQL queries per request. Here is a list of the findings:

| # | Issue | Where | Effect on the original code | Outcome |
| --- | --- | --- | --- | --- |
| 1 | The product list page fetched every product a second time, one request after another. | `frontend/src/views/CatalogueView.vue` | 26 requests to show 25 products. | Fixed |
| 2 | The product list API ran one category query per product. | `ProductListView` | 30 queries per call. | Fixed |
| 3 | The full product API ran two queries per variant inside a loop. | `ProductEditorView` | 106 queries for a 50 variant product. | Fixed |
| 4 | The product page made a second API call only to get the image path. | `frontend/src/views/EditorView.vue` | 2 requests where 1 is enough. | Fixed |
| 5 | The merchant access check was copied into all four views. | `backend/catalog/views.py` | A maintenance and security risk, not a speed problem. | Fixed |
| 6 | The last list page and search read all of a merchant's products. | `ProductListView` | 2 to 5 ms of database time. | Investigated, not changed |

**Priorities:** issues 1 and 2 came first because together they are the home page and the worst screen measured. Issue 3 came next because its cost grows with the size of the product. Issues 4 and 5 were small and cheap. Issue 6 was last because it costs a few milliseconds and the fixes available change behaviour.

## 2. Performance evidence

### How I measured

One read-only management command produces every figure:

```bash
docker compose exec backend python manage.py measure_performance
```

It signs in as `demo_operator` and replays the API calls each screen makes, using the data seeded by `./setup.sh` (2,500 products for the merchant measured).

| Column | Meaning |
| --- | --- |
| HTTP requests | API calls the browser makes to show the screen. |
| DB queries | SQL statements the server sends to MySQL for those calls. |
| DB time ms | Time spent waiting on MySQL. |
| Server time ms | Time for the server to produce the responses, database time included. |
| Response bytes | Size of the JSON sent back. |

Requests, queries and bytes are exact and identical on every run. Times are the median of 30 loads, and the before and after were run back to back on the same machine.

### Before: original code

| Screen | HTTP requests | DB queries | Server time ms |
| --- | ---: | ---: | ---: |
| Product list page | 26 | 130 | 61.8 |
| Product list page, searching | 26 | 130 | 65.9 |
| Product page, 2 variants | 2 | 14 | 6.1 |
| Product page, 50 variants | 2 | 110 | 30.0 |
| Product activity page | 2 | 9 | 4.0 |

| What the call returns | API URL | DB queries | DB time ms | Server time ms | Response bytes |
| --- | --- | ---: | ---: | ---: | ---: |
| Product list, first page | `/api/products/` | 30 | 1.6 | 7.0 | 3926 |
| Product list, last page | `/api/products/?page=100` | 30 | 4.9 | 10.9 | 3984 |
| Product search, many matches | `/api/products/?search=assessment` | 30 | 3.1 | 8.8 | 3944 |
| Product search, one match | `/api/products/?search=000123` | 6 | 2.4 | 4.5 | 206 |
| Product basic fields | `/api/products/5/` | 4 | 0.3 | 1.8 | 224 |
| Full product, 2 variants | `/api/products/5/editor/` | 10 | 0.6 | 3.1 | 746 |
| Full product, 50 variants | `/api/products/1/editor/` | 106 | 5.6 | 26.6 | 11233 |
| Product activity history | `/api/products/5/activity/` | 5 | 0.4 | 2.4 | 52 |

### After: issues 1 to 5 fixed

| Screen | HTTP requests | DB queries | Server time ms |
| --- | ---: | ---: | ---: |
| Product list page | 1 | 5 | 3.2 |
| Product list page, searching | 1 | 5 | 4.7 |
| Product page, 2 variants | 1 | 7 | 3.9 |
| Product page, 50 variants | 1 | 7 | 7.3 |
| Product activity page | 2 | 9 | 4.2 |

| What the call returns | API URL | DB queries | DB time ms | Server time ms | Response bytes |
| --- | --- | ---: | ---: | ---: | ---: |
| Product list, first page | `/api/products/` | 5 | 0.7 | 3.0 | 3926 |
| Product list, last page | `/api/products/?page=100` | 5 | 5.5 | 8.1 | 3984 |
| Product search, many matches | `/api/products/?search=assessment` | 5 | 2.2 | 4.8 | 3944 |
| Product search, one match | `/api/products/?search=000123` | 5 | 2.4 | 4.5 | 206 |
| Product basic fields | `/api/products/5/` | 4 | 0.3 | 2.0 | 224 |
| Full product, 2 variants | `/api/products/5/editor/` | 7 | 0.5 | 3.9 | 788 |
| Full product, 50 variants | `/api/products/1/editor/` | 7 | 1.0 | 7.0 | 11275 |
| Product activity history | `/api/products/5/activity/` | 5 | 0.3 | 2.1 | 52 |

### What the results mean

- **Product list page:** 26 requests and 130 queries became 1 request and 5 queries. Server time fell from 61.8 ms to 3.2 ms. Searching improved the same way (65.9 ms to 4.7 ms).
- **Product page, 50 variants:** 2 requests and 110 queries became 1 request and 7 queries. Server time fell from 30.0 ms to 7.3 ms. The query count is now 7 whatever the number of variants.
- **Product page, 2 variants:** 2 requests and 14 queries became 1 request and 7 queries. Server time fell from 6.1 ms to 3.9 ms.
- **Product activity page:** not changed, and its numbers did not move (4.0 ms and 4.2 ms). The same is true of the basic fields call (1.8 ms and 2.0 ms). This shows the two runs are comparable and that a difference of a few tenths of a millisecond is noise.
- **Responses:** the full product response grew by 42 bytes because it now includes `image_url`. Every other response is byte for byte identical before and after.

## 3. Implementation

| # | What I changed | Why | Test |
| --- | --- | --- | --- |
| 1 | `CatalogueView.vue` uses the list response directly. The loop that refetched each product is removed. | The list already returns every field the table shows. | Checked in the browser: 1 API request per page load. |
| 2 | `ProductListView` adds `select_related("category")`. | The category now arrives in the same query as the products. | `test_query_count_does_not_grow_with_products`. On the original code it fails with `26 != 6`. |
| 3 | `ProductEditorView` loads variants, prices, inventory and sales channels with `select_related` and `prefetch_related`, and builds the response with serializers. | A fixed number of queries replaces two queries per variant. | `test_query_count_does_not_grow_with_variants`. On the original code it fails with `28 != 8`. |
| 4 | The full product response includes `image_url`, and `EditorView.vue` no longer makes a second call for it. | One request now carries everything the page needs. | `test_editor_includes_image_path`. |
| 5 | `MerchantScopedMixin.get_merchant_id()` in `backend/utils/mixin.py` holds the merchant check once. All four views inherit it. | A new endpoint inherits the check instead of copying it. | The existing access tests pass unchanged, and 19 responses, including every error case, are identical before and after. |

**Issue 6, not changed.** I added an index on `(merchant, id)` so the last list page could skip sorting 2,500 rows. MySQL did not use it, and the measured time did not change (5.8 ms before, 5.5 ms after), so I removed it. Search matches text anywhere in the name or SKU, which no ordinary index can serve. A full-text index would make search match whole words only, so a search for `000123` would stop finding `ASSESS-000123`. That is a behaviour change for a saving of about 3 ms.

## 4. Correctness



## 5. Trade-offs

- **Serializers cost a little processing time.** The full product call for a 2 variant product went from 3.1 ms to 3.9 ms even though it runs 3 fewer queries, because serializers do more work per object than hand-written dictionaries. I accepted this because it is under 1 ms, it removes the loop that caused issue 3, and the product page is still faster overall since it makes one request instead of two.
- **The full product response has one new field.** `image_url` was added and nothing was removed or renamed, so existing consumers keep working.
- **The activity page still makes 2 requests.** Removing the second one means changing the shape of the paginated activity response to save about 2 ms.
- **The last list page got slightly heavier in the database** (4.9 ms to 5.5 ms) because the list query now joins the category while sorting. The call is still faster overall (10.9 ms to 8.1 ms).
- **Times exclude the network.** Everything ran on one machine. A real operator pays a network delay on every request, so removing 25 requests from the list page saves more in production than these times show.
- **Next, if catalogues grow much larger:** cursor based paging so deep pages do not sort and skip thousands of rows, and a decision with the product owner on whether search can become word based.
