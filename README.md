# Overcome Stress — L402 Skill Server

## Wat is dit?

Een server die jouw 16 AI-agent skill blocks serveert achter Lightning Network paywalls via het L402 protocol. AI-agents betalen per query in sats — geen accounts, geen API-keys, geen signup.

## Architectuur

```
AI-Agent → GET /api/skills/K01 → Server antwoordt: HTTP 402 + Lightning invoice (50 sats)
AI-Agent → Betaalt invoice via Lightning → Ontvangt preimage
AI-Agent → GET /api/skills/K01 + Authorization: L402 {macaroon}:{preimage}
Server  → Verifieert betaling → Serveert volledige skill content
Sats    → Stromen naar jouw Lightning wallet
```

## Componenten

| Service | Wat het doet |
|---|---|
| **API Server** | Python Flask app die skills serveert achter L402 paywall |
| **LNbits** | Lightning payment backend — maakt invoices, checkt betalingen |
| **Caddy** | Reverse proxy met automatische HTTPS (Let's Encrypt) |

## Snelle Start (5 minuten)

### Stap 1: Huur een VPS

Goedkoopste optie: **Hetzner Cloud** (€4,51/maand voor CX22)
- Ga naar https://www.hetzner.com/cloud
- Kies Ubuntu 24.04, CX22 (2 vCPU, 4GB RAM)
- Maak een SSH key aan of gebruik wachtwoord
- Noteer het IP-adres

### Stap 2: Upload bestanden naar de server

```bash
# Vanaf je computer (of gebruik FileZilla):
scp -r skill-server/ root@JOUW-IP:/root/skill-server/
```

### Stap 3: SSH naar je server en deploy

```bash
ssh root@JOUW-IP
cd /root/skill-server
chmod +x deploy.sh
./deploy.sh
```

### Stap 4: LNbits configureren

1. Open `http://JOUW-IP:5000` in je browser
2. Maak een nieuwe wallet aan ("OvercomeStress")
3. Klik op het sleutel-icoon rechtsboven
4. Kopieer:
   - **Admin key** → dit wordt `LNBITS_ADMIN_KEY`
   - **Invoice/read key** → dit wordt `LNBITS_API_KEY`

### Stap 5: API keys instellen

```bash
nano .env
# Plak de keys bij LNBITS_API_KEY en LNBITS_ADMIN_KEY
# Save: Ctrl+X → Y → Enter

docker compose restart api
```

### Stap 6: Testen

```bash
# Catalogus ophalen (gratis)
curl http://JOUW-IP:8402/api/catalog

# Skill opvragen (krijgt 402 + invoice terug)
curl http://JOUW-IP:8402/api/skills/K01

# Preview ophalen (gratis)
curl http://JOUW-IP:8402/api/skills/K01/preview
```

## Domein + HTTPS (optioneel maar aanbevolen)

1. Koop een domein (bijv. `skills.overcomestress.com`)
2. Zet een DNS A-record naar je server IP
3. Pas de `Caddyfile` aan met je domein
4. `docker compose restart caddy`
5. Caddy regelt automatisch HTTPS via Let's Encrypt

## API Endpoints

| Endpoint | Auth | Prijs | Beschrijving |
|---|---|---|---|
| `GET /` | Nee | Gratis | Server info |
| `GET /api/catalog` | Nee | Gratis | Volledige skill catalogus |
| `GET /api/skills/{id}/preview` | Nee | Gratis | Eerste sectie van skill |
| `GET /api/skills/{id}` | L402 | 50-100 sats | Volledige skill content |
| `GET /api/trajectories/{id}` | L402 | 150 sats | Traject routering |
| `GET /api/payment/{hash}/status` | Nee | Gratis | Betaalstatus check |
| `GET /api/stats` | Nee | Gratis | Publieke statistieken |
| `GET /health` | Nee | Gratis | Health check |

## Pricing

| Type | Aantal | Prijs/query |
|---|---|---|
| Knowledge (K01-K08) | 8 | 50 sats |
| Intervention (I01-I07) | 7 | 75 sats |
| Proprietary (I08) | 1 | 100 sats |
| Trajectories (T01-T04) | 4 | 150 sats |

## Lightning Backend Configureren

LNbits start standaard met een **FakeWallet** (voor testen). Voor echte betalingen:

### Optie A: Phoenixd (aanbevolen — sluit aan bij je Phoenix wallet)

[Phoenixd](https://phoenix.acinq.co/server) is de server-versie van Phoenix Wallet.

```bash
# Installeer phoenixd op je server
# Zie: https://phoenix.acinq.co/server

# Pas docker-compose.yml aan:
# Uncomment de PhoenixdWallet regels bij lnbits environment
# Herstart: docker compose up -d
```

### Optie B: LND node

Als je een LND node hebt of wilt draaien:

```bash
# Pas docker-compose.yml aan:
# Uncomment de LndRestWallet regels bij lnbits environment
# Vul je LND REST endpoint, cert, en macaroon in
# Herstart: docker compose up -d
```

## Bestanden

```
skill-server/
├── api/
│   ├── server.py          # Hoofd API server met L402 protocol
│   └── requirements.txt   # Python dependencies
├── skills/                # De 16 skill markdown bestanden
│   ├── K01_what_is_stress.md
│   ├── K02_autonomic_nervous_system.md
│   ├── ... (16 bestanden)
│   └── I08_vergeetmuts_technique.md
├── docker-compose.yml     # Service orchestratie
├── Dockerfile             # API server container
├── Caddyfile              # HTTPS reverse proxy config
├── deploy.sh              # Deployment script
├── .env.example           # Environment template
└── README.md              # Dit bestand
```

## Monitoring

```bash
# Logs bekijken
docker compose logs -f api
docker compose logs -f lnbits

# Status checken
docker compose ps

# Herstarten
docker compose restart

# Stoppen
docker compose down
```

## Kosten

- VPS: €4,51-9/maand (Hetzner CX22/CX32)
- Domein: €10-15/jaar
- LNbits: gratis (open source)
- Totaal: ~€70/jaar

## Licentie

© 2024-2026 Sieto Reitsma. Alle rechten voorbehouden.
Forgive and Forget Hood: © 2025 Dr. Paula Zeestraten-Bartholomeus, ReAttach Therapy International Foundation.
Corpus Systemics® — Geregistreerd Handelsmerk, Benelux.
