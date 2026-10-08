# TLS intermediates

`geotrust_ev_rsa_ca_g2.pem` — intermediate CA "GeoTrust EV RSA CA G2"
(issuer: DigiCert Global Root G2).

Provenance: published in the AIA extension of the leaf certificate served by
`suameca.banrep.gov.co`:

```
CA Issuers - URI:http://cacerts.digicert.com/GeoTrustEVRSACAG2.crt
```

Downloaded on 2026-10-08, converted from DER to PEM with `openssl x509`.
It is pinned only because the server omits the intermediate from its TLS
chain; `src/etl/http.py` combines it with certifi's roots for that host.
