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

`.github/workflows/deploy.yml` runs on push to `main` or manual dispatch:

1. **Unit tests** — pytest (BMI logic, advice payloads, UI route, brand assets)
2. **Build** — Docker Buildx, non-root image on `python:3.12-slim`
3. **Scan** — Trivy, fails the run on CRITICAL/HIGH (fixable); SARIF goes to GitHub Security
4. **Push** — image to ECR, then resolve its digest
5. **Deploy** — `kubectl apply` of the kustomize base with the **digest-pinned** image
6. **Rollout wait** — `kubectl rollout status` (300s timeout)
7. **Smoke test** — runs *inside* a live pod: localhost `/health`, `/` HTML, and `/bmi`
   through the Service DNS so DNS and endpoint routing are both proven

On failure, a diagnostics step dumps pod status, descriptions, events, and container logs.

`.github/workflows/teardown.yml` is manual-only (type `DESTROY`) and removes the workloads,
optionally deleting ECR images, so the project can be taken to ~$0.

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
this repo, the namespace with least-privilege RBAC, and the `aws-auth` mapping that lets the role
authenticate to the cluster.

```bash
./infra/bootstrap.sh
gh secret set AWS_IAM_ROLE_ARN --body "arn:aws:iam::985539781710:role/github-actions-bmi-api-eks"
```

The CI role can only push to one ECR repo, call `eks:DescribeCluster`, and act inside the
`bmi-api` namespace — it has no cluster-wide Kubernetes rights.

## Accessing the app

ClusterIP keeps this free of load balancer charges, so reach it with a port-forward:

```bash
aws eks update-kubeconfig --region us-east-2 --name etechapp-eks-4QAQxDD3
kubectl -n bmi-api port-forward svc/bmi-api 8080:80
open http://localhost:8080
```

To publish it on an internet-facing NLB instead (**adds roughly $16-18/month**), deploy the
overlay by pointing the workflow's apply step at `k8s/overlays/public`:

```bash
kubectl kustomize k8s/overlays/public | sed "s|__IMAGE__|<image>|" | kubectl apply -f -
```

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | End-user UI |
| `GET` | `/health` | Probe payload: `status`, `pod`, `node` |
| `GET` | `/bmi?height_cm=175&weight_kg=70` | BMI + advice via query params |
| `POST` | `/bmi` | BMI + advice via JSON body |

## Layout

```text
app/                  FastAPI app, BMI + advice logic, UI assets
tests/                unit tests
Dockerfile            non-root python:3.12-slim image
k8s/base/             ServiceAccount, Deployment, Service, PDB (kustomize)
k8s/overlays/public/  opt-in NLB exposure
infra/                bootstrap script, namespace + RBAC
.github/workflows/    deploy and teardown pipelines
```

## Local development only

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r app/requirements.txt -r requirements-dev.txt
PYTHONPATH=. pytest -q
uvicorn app.main:app --reload --port 8080
```
