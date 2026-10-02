# BMI Health Check — EKS deployment

The same FastAPI BMI app (calculator, health advice, exercise guidance, branded UI) as the
Lambda version, but containerized for **Kubernetes** and shipped to an existing **EKS** cluster
by GitHub Actions.

**Everything runs in CI.** No local build or deploy step.

## Target

| | |
| --- | --- |
| Cluster | `etechapp-eks-4QAQxDD3` (EKS 1.32) |
| Region / account | `us-east-2` / `985539781710` |
| Namespace | `bmi-api` |
| Image repo | ECR `bmi-health-check-api` (lifecycle: keep last 5) |
| Service | `ClusterIP` on port 80 → container 8080 |

## Pipeline

`.github/workflows/deploy.yml` has two modes:

| Trigger | What runs |
| --- | --- |
| **Pull request** → `main` | tests, build, Trivy scan. No push, no deploy — the image is proven before review. |
| **Push to `main`** (i.e. a PR merge) | the full pipeline through to deploy |
| **Manual dispatch** | full pipeline, with a toggle to skip the public load balancer |

Full run order:

1. **Unit tests** — pytest (BMI logic, advice payloads, UI route, brand assets, metrics)
2. **Build** — Docker Buildx, non-root image on `python:3.12-slim`
3. **Scan** — Trivy, fails the run on fixable CRITICAL/HIGH; report kept as an artifact
4. **Slack** — notifies on scan pass *and* scan failure
5. **Push** — image to ECR, then resolve its digest
6. **Deploy** — `kubectl apply` of the kustomize overlay with the **digest-pinned** image
7. **Rollout wait** — `kubectl rollout status` (300s timeout)
8. **Smoke test** — runs *inside* a live pod: localhost `/health`, `/` HTML, and `/bmi`
   through the Service DNS so DNS and endpoint routing are both proven
9. **Load balancer gate** — waits for the NLB address and polls until `/health` returns 200
10. **Observability** — publishes the CloudWatch dashboard and alarms
11. **Slack** — notifies the final deploy result with the app URL

On failure, a diagnostics step dumps pod status, descriptions, Service details, events, and logs.

`.github/workflows/teardown.yml` is manual-only (type `DESTROY`) and removes the workloads,
optionally deleting ECR images, so the project can be taken to ~$0.

### Branch protection

`main` requires a pull request whose **Unit tests** and **Build, scan, and push image** checks
pass, so nothing reaches the cluster without a green scan.

### Slack notifications

Add an [incoming webhook](https://api.slack.com/messaging/webhooks) once:

```bash
gh secret set SLACK_WEBHOOK_URL --body "https://hooks.slack.com/services/..."
```

Messages carry the repo, short commit, actor, trigger, image tag, app URL, and a button to the
run. Without the secret the notification steps skip cleanly, and a broken webhook never fails a
good deploy.

## Deployed shape

- 2 replicas, rolling update with `maxUnavailable: 0`
- Requests 50m CPU / 96Mi, limits 500m CPU / 256Mi (sized for the cluster's `t3.small` nodes)
- Startup, readiness, and liveness probes on `/health`
- Non-root (uid 10001), read-only root filesystem, all capabilities dropped, `RuntimeDefault` seccomp
- PodDisruptionBudget `minAvailable: 1`
- `/health` reports the serving pod and node via the downward API

## One-time bootstrap

`infra/bootstrap.sh` is idempotent and needs cluster-admin AWS credentials. It creates the ECR
repo and lifecycle policy, the GitHub OIDC provider (if missing), the deploy IAM role scoped to
this repo, the `bmi-api-metrics` IRSA role for CloudWatch, the namespace with least-privilege
RBAC, and the `aws-auth` mapping that lets the deploy role authenticate to the cluster.

```bash
./infra/bootstrap.sh
gh secret set AWS_IAM_ROLE_ARN --body "arn:aws:iam::985539781710:role/github-actions-bmi-api-eks"
```

The CI role can only push to one ECR repo, call `eks:DescribeCluster`, and act inside the
`bmi-api` namespace — it has no cluster-wide Kubernetes rights.

## Accessing the UI

The pipeline deploys `k8s/overlays/public`, which fronts the Service with an **internet-facing
NLB** — the cheapest managed load balancer at about $0.0225/hour (~$16/month) plus negligible
LCU charges. The deploy summary and the Slack message both print the URL.

Why a load balancer at all: the nodes run in private subnets with no public IPs, so a NodePort
would not be reachable from the internet. An NLB is cheaper than a Classic ELB ($0.025/hour) and
avoids the AWS Load Balancer Controller that an ALB would require.

Cross-zone load balancing is **on** deliberately: only two of the three AZs run nodes, and
without it the NLB address in the empty AZ would blackhole roughly a third of requests.

Zero-cost alternatives:

```bash
# No load balancer at all — deploy the base overlay
gh workflow run "Build and Deploy to EKS" -f expose_public=false

# Reach it privately instead
kubectl -n bmi-api port-forward svc/bmi-api 8080:80
```

## Performance monitoring

CloudWatch dashboard **`bmi-health-check`** (`infra/cloudwatch-dashboard.json`, applied by CI):
request volume, BMI calculations, latency average/max, 5xx count with a computed error-rate
percentage, EKS node CPU, and NLB target health plus connection counts.

The app publishes its own metrics rather than relying on Container Insights. `app/metrics.py`
aggregates in memory and flushes **four** metrics every 60 seconds to namespace
`BMI/HealthCheck` — `RequestCount`, `ErrorCount`, `BmiCalculations`, and `LatencyMs` as a
statistic set. Custom metrics are billed per metric per month, so this runs about **$1.20/month**;
cluster-wide Container Insights would publish hundreds of metrics and cost tens of dollars.

Credentials come from **IRSA** — the `bmi-api` ServiceAccount assumes `bmi-api-metrics`, whose
policy only allows `cloudwatch:PutMetricData` into that one namespace. No static keys in the pod.

Two alarms ship with it (about $0.20/month total):

| Alarm | Condition |
| --- | --- |
| `bmi-health-check-5xx-errors` | `ErrorCount` sum ≥ 5 in 5 minutes |
| `bmi-health-check-slow-requests` | `LatencyMs` max > 2000ms for 2 periods |

Latency uses max rather than a percentile because CloudWatch cannot compute percentiles from
statistic sets. Alarms have no actions wired yet; point them at an SNS topic to route to Slack.

### Running cost

| Item | Monthly |
| --- | --- |
| NLB | ~$16 |
| Custom metrics (4) | ~$1.20 |
| Alarms (2) | ~$0.20 |
| Dashboard | $0 (first 3 free) |
| ECR storage (keep last 5) | a few cents |

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | End-user UI |
| `GET` | `/health` | Probe payload: `status`, `pod`, `node` |
| `GET` | `/bmi?height_cm=175&weight_kg=70` | BMI + advice via query params |
| `POST` | `/bmi` | BMI + advice via JSON body |

## Layout

```text
app/                  FastAPI app, BMI + advice logic, CloudWatch metrics, UI assets
tests/                unit tests
Dockerfile            non-root python:3.12-slim image
k8s/base/             ServiceAccount, Deployment, Service, PDB (kustomize)
k8s/overlays/public/  NLB exposure (what CI deploys)
infra/                bootstrap, namespace + RBAC, dashboard and alarms
.github/workflows/    deploy and teardown pipelines
.github/scripts/      Slack notifier
```

## Local development only

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r app/requirements.txt -r requirements-dev.txt
PYTHONPATH=. pytest -q
uvicorn app.main:app --reload --port 8080
```
