# tap-navan

Singer tap for [Navan](https://navan.com), extracting travel booking data for expense management and travel spend analytics.

Built with the [Meltano Singer SDK](https://sdk.meltano.com).

## Streams

| Stream | Endpoint | Replication | Primary Key |
|---|---|---|---|
| `bookings` | `GET /v1/bookings` | Incremental (`created`) | `uuid` |
| `users` | `GET /v1/users` | Full table | `id` |

The **bookings** stream is the primary source for expense management. Each record represents one booking segment (flight, hotel, rail, car, or black car) and includes financial fields (`grandTotal`, `currency`, fee breakdown), traveler info (`booker`, `departments`, `costCenters`), policy status (`outOfPolicy`, `approvalStatus`), and payment transaction details (`transactions`).

The **users** stream requires the `users:read` OAuth scope. TMC credentials typically only include `bookings:read` — if the endpoint returns 403, the stream is skipped gracefully.

## Authentication

Navan uses the OAuth2 **client credentials** flow. Credentials are issued per company from the Navan Admin Dashboard under **Settings → Integrations**.

For TMC integrations, Navan RSA-encrypts the `client_secret` with your public key. Decrypt it before use:

```bash
cat secret_enc.txt | base64 -D > secret_enc_base64
openssl pkeyutl -decrypt -inkey myself.pem -in secret_enc_base64 \
  -out secret_dec.txt -pkeyopt rsa_padding_mode:oaep \
  -pkeyopt rsa_oaep_md:sha256 -pkeyopt rsa_mgf1_md:sha256
```

## Configuration

| Setting | Required | Default | Description |
|---|---|---|---|
| `client_id` | Yes | — | OAuth client ID |
| `client_secret` | Yes | — | OAuth client secret (decrypted) |
| `start_date` | No | — | Earliest booking creation date to sync (ISO 8601) |
| `api_url` | No | `https://api.navan.com` | API base URL (see environments below) |

### Environments

| Environment | URL |
|---|---|
| Production US | `https://api.navan.com` |
| Production EU | `https://app-fra.navan.com` |
| Staging | `https://staging-prime.tripactions.com` |

### Configure using environment variables

```bash
TAP_NAVAN_CLIENT_ID=your_client_id
TAP_NAVAN_CLIENT_SECRET=your_decrypted_secret
TAP_NAVAN_START_DATE=2024-01-01T00:00:00Z
TAP_NAVAN_API_URL=https://api.navan.com  # optional
```

Copy `.env.example` to `.env` and fill in your values. Pass `--config=ENV` to the tap to load them automatically.

## Installation

```bash
uv tool install git+https://github.com/MeltanoLabs/tap-navan.git@main
```

## Usage

```bash
# Discovery
tap-navan --config=ENV --discover > catalog.json

# Sync
tap-navan --config=ENV --catalog catalog.json
```

### With Meltano

```bash
uv tool install meltano
meltano invoke tap-navan --version
meltano run tap-navan target-jsonl
```

## Development

```bash
# Install dependencies
uv sync

# Run tests (requires TAP_NAVAN_CLIENT_ID and TAP_NAVAN_CLIENT_SECRET env vars)
uv run pytest

# Type checks
uv run mypy tap_navan tests
```
