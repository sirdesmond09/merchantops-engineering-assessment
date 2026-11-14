# Engineering report

Status: draft. Sections marked "to be written" are filled in as the work is done.

## 1. Findings and priorities

### What I investigated

I read the backend (`backend/catalog`) and the Vue frontend (`frontend/src`), then measured every screen an operator uses: the product list page, the product page and the product activity page. For each screen I counted the HTTP requests the browser makes and the SQL queries the server runs, and confirmed the request counts in the browser's Network tab.

### What I found

The database is not the bottleneck. Individual queries take well under a millisecond each. The application is slow because it does far more work than a screen needs: too many HTTP requests from the browser, and too many SQL queries per request on the server.

| # | Issue | Where | Evidence from the baseline |
| --- | --- | --- | --- |
| 1 | The product list page fetches every product a second time, one request after another, for fields the list call already returned. | `frontend/src/views/CatalogueView.vue`, `fetchPage` | 26 HTTP requests and 130 queries to show one page of 25 products. |
| 2 | The product list API runs one category query per product (an N+1 query). | `ProductListView` in `backend/catalog/views.py`, `ProductListSerializer` | 30 queries for one call; 25 of them are category lookups. |
| 3 | The full product API runs one price query and one inventory query per variant, inside a Python loop. | `ProductEditorView.get` in `backend/catalog/views.py` | 10 queries for a 2 variant product, 106 for a 50 variant product. The count grows with the number of variants. |
| 4 | The product page and the activity page each make a second API call for one or two fields. | `frontend/src/views/EditorView.vue`, `frontend/src/views/ActivityView.vue` | 2 HTTP requests per screen where 1 would do. |
| 5 | The merchant access check is copied into all four views. | `backend/catalog/views.py` | Not a speed problem. It is a maintenance and security risk, because a new endpoint has to repeat it correctly. |
| 6 | Search matches text anywhere in the name or SKU, so MySQL cannot use an index and reads every product the merchant owns. The last list page sorts all of the merchant's products. | `ProductListView.get_queryset` | About 2 ms of database time for search and 3.3 ms for the last page, with 2,500 products per merchant. |

### Priorities

1. **Issue 1, then issue 2.** Together they are the product list page, which is the screen operators use most and the worst one measured.
2. **Issue 3.** It only hurts products with many variants, but there the cost grows without limit.
3. **Issue 4.** A small, cheap saving on two screens.
4. **Issue 5.** Done before the inventory work in Task 2, because that work adds a fifth endpoint that needs the same check.
5. **Issue 6.** Left for last and only acted on if the numbers justify it. At the current data size it costs a few milliseconds, and changing how search matches would change behaviour.

## 2. Performance evidence

### How I measured

One Django management command, committed in this repository, produces all the figures:

```bash
docker compose exec backend python manage.py measure_performance
```

It signs in as `demo_operator`, calls the product API in-process with Django's test client, and replays the API calls each screen makes. It is read-only. Conditions for every run: the data seeded by `./setup.sh` (5,000 products across two merchants, 2,500 for the merchant measured), the same machine, and the same command.

- **HTTP requests:** API calls the browser makes to show the screen.
- **DB queries:** SQL statements the server sends to MySQL for those calls.
- **DB time:** time spent waiting on MySQL.
- **Server time:** time for the server to produce the responses. No network delay is included.

The two counts are exact and identical on every run. Times depend on how busy the machine is, so a "before" and an "after" taken at different moments are not comparable. The times below were therefore taken back to back.

### Results: issues 1, 2 and 3 fixed

Taken on 14 November 2026 on one machine. I checked out the original code (the commit that adds the measurement command), ran the command, then checked out the fixed code and ran it again, and repeated the pair a second time. Each run used `--runs 30`, so every time is the median of 30 loads after 1 warm-up. Both rounds are shown as `round 1 / round 2`.

```bash
git checkout 6569a4a   # "Add measure_performance command", the original code
docker compose exec backend python manage.py measure_performance --runs 30
git checkout main
docker compose exec backend python manage.py measure_performance --runs 30
```

Work the server does to show each screen:

| Screen | HTTP requests before | HTTP requests after | DB queries before | DB queries after | Server time ms before | Server time ms after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Product list page | 26 | 1 | 130 | 5 | 59.9 / 54.3 | 2.9 / 2.9 |
| Product list page, searching | 26 | 1 | 130 | 5 | 58.9 / 60.8 | 4.5 / 4.5 |
| Product page, 2 variants | 2 | 2 | 14 | 11 | 5.5 / 5.2 | 5.2 / 5.5 |
| Product page, 50 variants | 2 | 2 | 110 | 11 | 29.4 / 28.7 | 8.2 / 9.9 |
| Product activity page | 2 | 2 | 9 | 9 | 4.1 / 4.2 | 3.9 / 4.5 |

One call to each API endpoint:

| What the call returns | API URL | DB queries before | DB queries after | Server time ms before | Server time ms after |
| --- | --- | ---: | ---: | ---: | ---: |
| Product list, first page | `/api/products/` | 30 | 5 | 7.1 / 7.8 | 2.8 / 2.9 |
| Product list, last page | `/api/products/?page=100` | 30 | 5 | 10.2 / 10.8 | 7.7 / 7.9 |
| Product search, many matches | `/api/products/?search=assessment` | 30 | 5 | 8.5 / 8.7 | 5.0 / 4.4 |
| Product search, one match | `/api/products/?search=000123` | 6 | 5 | 4.4 / 4.3 | 4.5 / 4.9 |
| Product basic fields | `/api/products/5/` | 4 | 4 | 1.9 / 2.0 | 1.9 / 1.9 |
| Full product, 2 variants | `/api/products/5/editor/` | 10 | 7 | 3.1 / 3.1 | 3.6 / 4.0 |
| Full product, 50 variants | `/api/products/1/editor/` | 106 | 7 | 25.8 / 27.0 | 6.6 / 6.9 |
| Product activity history | `/api/products/5/activity/` | 5 | 5 | 2.1 / 2.2 | 2.2 / 2.2 |

What the results show:

- The product list page went from 26 requests and 130 queries to 1 request and 5 queries.
- The full product call no longer grows with the number of variants: 7 queries for both a 2 variant and a 50 variant product.
- The response size of every call is unchanged, and the JSON returned by the list, search, full product, basic fields and activity calls was compared byte for byte before and after and is identical.
- The two calls that were not changed (basic fields and activity history) show the same times before and after, which confirms the two sets of runs are comparable.
- The full product call for a 2 variant product is about 0.5 to 0.9 ms slower even though it runs 3 fewer queries. It now builds its response with serializers instead of hand-written dictionaries, which costs some processing time. For a product with many variants the saving in queries far outweighs that cost.

### Limits of these measurements

- Everything runs on one machine, so the times leave out the network delay a real operator pays on every request. The product list page makes 26 requests in sequence, so it is worse in production than the server time suggests. The request and query counts do not have this limit.
- Every request includes two queries from Django's login system (session and user lookup) and one merchant access check. They are counted because they are real work on every request.

## 3. Implementation

To be written.

## 4. Correctness

To be written.

## 5. Trade-offs, limitations and next steps

To be written.

## 6. How AI tools were used

To be written.
