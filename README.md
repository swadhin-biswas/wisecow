# Wisecow

Wisecow serves a random fortune through `cowsay` over HTTP. The app is the
upstream shell script from [nyrahul/wisecow](https://github.com/nyrahul/wisecow);
this repo adds a container image, Kubernetes manifests, TLS and a GitHub
Actions pipeline around it.

```
client -- 443 --> ingress-nginx -- 80 --> service -- 4499 --> pod
```

## Layout

| Path | What it holds |
| --- | --- |
| `Dockerfile` | Debian slim image, non-root user, tini, healthcheck |
| `wisecow.sh` | the app, unchanged from upstream |
| `k8s/base` | namespace, deployment, service, ingress, network policy, PDB |
| `k8s/overlays/local` | kind cluster with a throwaway cert-manager CA |
| `k8s/overlays/prod` | Let's Encrypt issuer and the public hostname |
| `scripts/` | system health monitor and access log analyzer |
| `.github/workflows/ci-cd.yml` | lint, build, push to GHCR, deploy |
| `hack/kind-config.yaml` | kind node with ports 8080/8443 mapped to 80/443 |

## Run the container

```
docker build -t wisecow:local .
docker run --rm -p 4499:4499 wisecow:local
curl http://localhost:4499
```

## Run on Kubernetes

Tested with kind v0.33 (Kubernetes 1.37). You need docker, kind, kubectl and
helm installed.

```
make cluster-up     # kind + ingress-nginx + cert-manager, takes a few minutes
make deploy         # build the image, load it into kind, apply the local overlay
make verify         # fetch the local CA and call the app over HTTPS
```

The kind node publishes ports 8080 and 8443 on the host, mapped to 80 and 443.
That keeps the demo away from anything already listening on the standard ports.
`make verify` uses `curl --resolve`, so no `/etc/hosts` entry is needed.

Useful follow ups:

```
kubectl -n wisecow get pods,certificate,ingress
kubectl -n wisecow logs -l app.kubernetes.io/name=wisecow --tail=50
make cluster-down
```

### TLS

TLS terminates at the ingress and the pod only speaks HTTP on port 4499. The
local overlay uses cert-manager to build a small CA (`selfSigned` issuer, then
a CA issuer) so the demo can be verified against a real CA file instead of
`curl -k`. The certificate is ECDSA, valid for 90 days and renewed 15 days
before expiry. The ingress forces HTTPS, sends HSTS and only accepts TLS 1.2
and 1.3.

The prod overlay adds a Let's Encrypt issuer and moves the host to
`wisecow.example.com`. Change the email in `k8s/overlays/prod/issuer.yaml` and
the hostname in `k8s/overlays/prod/kustomization.yaml` before using it. HTTP-01
needs public DNS for that name and port 80 reachable from the internet.

## CI/CD

`.github/workflows/ci-cd.yml` runs on pushes to `main`, on pull requests and on
manual dispatch.

1. `lint`: hadolint, Python compile check, unit tests and kubeconform
2. `build`: Buildx build, push to GHCR with `sha-*`, branch and semver tags,
   provenance and SBOM
3. `deploy`: apply the selected overlay and pin the deployment to the digest
   that was just built

The deploy job only runs on `main`, and only when the repository variable
`DEPLOY_ENABLED` is set to `true`. It expects a self-hosted runner on the
machine that holds the kubeconfig.

| Repository variable | Default | Purpose |
| --- | --- | --- |
| `DEPLOY_ENABLED` | unset | set to `true` to arm the deploy job |
| `DEPLOY_OVERLAY` | `prod` | overlay directory to apply |
| `WISECOW_HOST` | unset | hostname used in the workflow summary |

Images are published as `ghcr.io/swadhin-biswas/wisecow:latest` and
`ghcr.io/swadhin-biswas/wisecow:sha-<commit>`.

## Ops scripts

### System health monitor

`scripts/system_health_monitor.py` samples CPU over one second, reads memory
and disk, counts processes and zombies, and writes a single summary line to a
log. A metric over its threshold becomes an `ALERT` line on stderr as well.

```
./scripts/system_health_monitor.py
CPU_THRESHOLD=85 DISK_PATH=/var LOG_FILE=/var/log/health.log ./scripts/system_health_monitor.py
REQUIRED_PROCESSES="sshd,cron" ./scripts/system_health_monitor.py
./scripts/system_health_monitor.py --json
```

Exit code 0 means healthy, 1 means at least one alert, 2 means bad settings.
Thresholds can be flags or environment variables. `--help` shows the flag and
the matching environment name for each one.

### Access log analyzer

`scripts/log_analyzer.py` reads nginx or Apache combined logs, plain or
gzipped, and reports totals, status classes, error rate, top pages, top client
addresses, top 404 paths and the busiest hour.

```
./scripts/log_analyzer.py /var/log/nginx/access.log
./scripts/log_analyzer.py /var/log/nginx/access.log.gz --top 5
./scripts/log_analyzer.py access.log --json | jq .top_ips
```

It only uses the Python standard library.

## Tests

```
python3 -m unittest discover -s tests -v
```

The tests cover the `/proc` parsers, threshold evaluation, exit codes, log
counters and gzip input. Both scripts are Python 3 standard library only.

## License

Apache-2.0, same as upstream.
