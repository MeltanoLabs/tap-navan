# CLAUDE.md — tap-navan

Singer tap for the Navan APIs. Extracts travel bookings (TMC) and the full
Expense Partner API surface (card / connect / manual transactions, repayments,
fees, adjustments, daily rebates, disputes, and presigned receipt URLs).

## Architecture

```
tap_navan/
├── tap.py              # TapNavan — config schema, discover_streams()
├── client.py           # NavanStream base class, page-number paginator
├── auth.py             # NavanAuthenticator (OAuth2 client_credentials)
├── streams.py          # UsersStream, BookingsStream (TMC bookings API)
└── expense_streams.py  # Expense Partner API streams:
                        # card_transactions, connect_transactions,
                        # manual_transactions, repayments, fees,
                        # adjustments, daily_rebates, disputes, receipts
```

## API

**Auth**: `POST {api_url}/ta-auth/oauth/token` with `grant_type=client_credentials`, `client_id`, `client_secret`. The same OAuth credentials cover bookings + expense; granted scopes determine which streams return data. The Quick Start Guide documents `app.navan.com` for the auth URL but `api.navan.com` also works (alias).

**Base URLs**: Production US `https://api.navan.com` (default) · EU `https://app-fra.navan.com` · Staging `https://staging-prime.tripactions.com`. **EU customers** need an `X-ta-region: EU` header on every request or the API returns 500. The expense streams add this header automatically when `api_url` contains `app-fra` or `fra.navan`.

**Streams**:

- `GET /v1/users` — requires `users:read` scope; 403 → graceful skip.
- `GET /v1/bookings` — TMC bookings; not in the public OpenAPI spec.
- `GET /v1/expense/<type>` — Expense Partner API, one stream per type:
  `card-transactions`, `connect-transactions`, `manual-transactions`,
  `repayments`, `fees`, `adjustments`, `daily-rebates`, `disputes`.
  Requires the tenant to be enabled for the Expense API by Navan
  (separate from any OAuth scope); 403 → graceful skip per window.
- `GET /v1/expense/transactions/receipts` — presigned receipt URLs (7-day TTL).

**Bookings query params**: `createdFrom` / `createdTo` (epoch seconds — **both required together**), `page` (0-indexed), `size`, `includeTransactions=true`.

**Expense query params**: `date_modified.from` / `date_modified.to` (ISO `YYYY-MM-DD`, **max 93-day span**, must be on or after the first day of the month two months prior), `cursor`, `page_size` (1-500, default 100). Always sent: `include_field_group=POLICY,ERP,TAX,BOOKING,MILEAGE,TRAVELER_HR` so the schema is stable regardless of customer field-group defaults.

**Response envelopes**:
- Bookings/Users: `{"data": [...], "page": {"totalPages": N, ...}}` — `records_jsonpath = "$.data[*]"`
- Expense: `{"content": [...], "next_cursor": str | None, "has_next": bool, "has_previous": bool, "page_size": int, "content_size": int}` — `records_jsonpath = "$.content[*]"`, `NavanExpenseCursorPaginator` advances on `next_cursor` and stops on `has_next: false`.

## Incremental Sync

**Bookings**: replication key is `created`. `get_url_params` converts the Singer state bookmark to epoch seconds for `createdFrom`; `createdTo` is always set to `now()`.

**Expense transaction streams**: replication key is `modified_timestamp`. The Expense API caps date ranges at 93 days, so `NavanExpenseStream.request_records` walks 93-day `date_modified` windows from the bookmark (minus `LOOKBACK_DAYS=1` for late edits) — or `start_date` on first run — through "today" UTC, paginating each window with a cursor paginator. The bookmark is clamped to the API floor (first day of the month two months prior) since older records aren't retrievable. The Quick Start Guide notes that updates may take up to 4 hours to appear in GET results — covered by the 1-day lookback.

**Receipts**: presigned URLs expire 7 days after issue, so `ReceiptsStream` is FULL_TABLE and refreshes a rolling 7-day `date_modified` window on every run. Downstream consumers must fetch the binary content immediately rather than caching the URL.

## Schema Quirks (from live API)

| Field | Issue |
|---|---|
| `outOfPolicyViolations`, `outOfPolicyViolationTypes` (bookings) | Returns `""` when empty; normalized to `[]` in `post_process` |
| `tripLength`, `seats`, `billableEntities`, `navanPro`, `paymentSchedule`, `paymentMethod` (bookings) | Types vary from API docs — see schema for the actuals |
| `custom_field_values[].displayValue` (expense) | API returns **camelCase** (`displayValue`), NOT snake_case as the Quick Start Guide PDF documents. Singer SDK would silently strip the field if the schema disagreed with the wire. A unit test guards this. |
| `posted_amount`, `original_amount`, etc. (expense) | **Strings** like `"3.30"`, not numbers — API preserves decimal precision. Typed as `th.StringType`; dbt casts downstream. |
| `flight_miles`, `train_miles` (inside `booking_details`) | Also strings (`"1267.00"`). |
| `participants` (expense) | Comma-separated string, not an array. |
| `tax_details`, `line_items` (expense) | Schemas not documented inline; typed as `additional_properties=AnyType()` so the SDK passes them through verbatim. Only safe because they sit at the top level of `properties` (see singer-sdk gotchas). |
| Connect-specific fields | `card_program_name`, `reporting_transaction_id`, deprecated `vcf_transaction_reference_number` only appear on connect transactions. Included in the shared schema; null for other types. |

## Adding a Stream

1. Add class to `tap_navan/streams.py` extending `NavanStream`
1. Set `name`, `path`, `primary_keys`, `replication_key`
1. Define `schema` with `th.PropertiesList`
1. Register in `TapNavan.discover_streams()`

`NavanStream` provides: auth, `Accept: application/json`, `NavanPageNumberPaginator`, `records_jsonpath = "$.data[*]"`. Override `get_url_params` for extra query params.

## Config / meltano.yml Sync

When changing config, update `tap.py`, `meltano.yml`, and `.env.example` together. Type map: `StringType→string`, `IntegerType→integer`, `BooleanType→boolean`, `NumberType→number`, `DateTimeType→date_iso8601`. Mark `secret=True` props with `sensitive: true` in meltano.yml.

## Testing

```bash
export TAP_NAVAN_CLIENT_ID=... TAP_NAVAN_CLIENT_SECRET=... TAP_NAVAN_START_DATE=2024-01-01T00:00:00Z
uv run pytest   # 80 passed, 1 skipped (users — no users:read scope)
```
