## Updating

- **One-line install (Mac mini, Raspberry Pi):** run the install line again. It updates `~/fleetwatch`, keeps
  your `.env`, sign-in and history, and restarts the service.

  ```bash
  curl -fsSL https://raw.githubusercontent.com/ScientiaCapital/fleetwatch/main/install.sh | bash
  ```

- **Docker:** `docker compose pull && docker compose up -d`.
- **Check it:** `fleetwatch doctor` shows the new version on its first line.

Verify the image came from this repository's CI:

```bash
gh attestation verify oci://ghcr.io/scientiacapital/fleetwatch:<version> --owner ScientiaCapital
```
