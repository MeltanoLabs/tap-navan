# CLAUDE.md — tap-navan

Singer tap for the Navan TMC API. Extracts travel bookings for expense management.

## Architecture

```
tap_navan/
├── tap.py      # TapNavan — config schema, discover_streams()
├── client.py   # NavanStream base class, NavanPageNumberPaginator
├── auth.py     # NavanAuthenticator (OAuthAuthenticator, client_credentials)
└── streams.py  # UsersStream, BookingsStream
```

## API

**Auth**: `POST {api_url}/ta-auth/oauth/token` with `grant_type=client_credentials`, `client_id`, `client_secret`.

**Base URLs**: Production US `https://api.navan.com` (default) · EU `https://app-fra.navan.com` · Staging `https://staging-prime.tripactions.com`

**Streams**:
- `GET /v1/users` — requires `users:read` scope (TMC creds often only have `bookings:read`; 403 is caught and skipped gracefully)
- `GET /v1/bookings` — TMC-only endpoint, not in the public OpenAPI spec

**Bookings query params**: `createdFrom` / `createdTo` (epoch seconds — **both required together**), `page` (0-indexed), `size`, `includeTransactions=true` (beta, always sent).

**Response envelope**: `{"data": [...], "page": {"totalPages": N, "currentPage": N, ...}}`

## Incremental Sync

`BookingsStream` replication key is `created`. `get_url_params` converts the Singer state bookmark to epoch seconds for `createdFrom`; `createdTo` is always set to `now()`.

## Schema Quirks (from live API)

| Field | Issue |
|---|---|
| `outOfPolicyViolations`, `outOfPolicyViolationTypes` | Returns `""` when empty; normalized to `[]` in `post_process` |
| `tripLength` | String (`"Long haul"`), not integer |
| `seats` | `["8C", "7A"]` — strings, not objects |
| `billableEntities` | Array of company name strings, not objects |
| `navanPro` | Boolean `false`, not a number |
| `paymentSchedule` | String (`"NOW"`), not an object |
| `paymentMethod` | String (`"VISA 1235"`), not an object |

## Adding a Stream

1. Add class to `tap_navan/streams.py` extending `NavanStream`
2. Set `name`, `path`, `primary_keys`, `replication_key`
3. Define `schema` with `th.PropertiesList`
4. Register in `TapNavan.discover_streams()`

`NavanStream` provides: auth, `Accept: application/json`, `NavanPageNumberPaginator`, `records_jsonpath = "$.data[*]"`. Override `get_url_params` for extra query params.

## Config / meltano.yml Sync

When changing config, update `tap.py`, `meltano.yml`, and `.env.example` together. Type map: `StringType→string`, `IntegerType→integer`, `BooleanType→boolean`, `NumberType→number`, `DateTimeType→date_iso8601`. Mark `secret=True` props with `sensitive: true` in meltano.yml.

## Testing

```bash
export TAP_NAVAN_CLIENT_ID=... TAP_NAVAN_CLIENT_SECRET=... TAP_NAVAN_START_DATE=2024-01-01T00:00:00Z
uv run pytest   # 80 passed, 1 skipped (users — no users:read scope)
```
