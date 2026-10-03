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
8. **Smoke test** — runs *inside* a live pod: anonymous visitors are blocked, then a
   throwaway account signs up, signs in, and calls `/bmi` through the Service so both
   replicas accept the same session cookie. The row is checked in Postgres and deleted.
9. **Load balancer gate** — waits for the NLB address and polls until `/health` returns 200
10. **Observability** — publishes the CloudWatch dashboard and alarms
11. **Slack** — notifies the final deploy result with the app URL

On failure, a diagnostics step dumps pod status, descriptions, Service details, events, and logs.

`.github/workflows/teardown.yml` is manual-only (type `DESTROY`) and removes the workloads,
optionally deleting ECR images, so the project can be taken to ~$0.

### Branch protection

A pull request still runs the tests and the Trivy scan before anything is deployed, because only
a push to `main` deploys. GitHub will not enforce that as a required check on a private repo
unless the org has GitHub Pro (or the repo is public). Turn the requirement on in
**Settings → Branches** once that is available.

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
- One `bmi-db` Postgres pod with a persistent volume, reachable only inside the cluster

## Client accounts and the database

The UI is behind a sign-in. `/` and `/bmi` redirect to `/signin` (or answer `401` for JSON
callers) unless the request carries a valid session; `/health`, `/signup`, `/signin`, and the
static assets stay public.

Clients live in Postgres, running as a single-replica StatefulSet (`bmi-db`) on a 2Gi `gp2`
volume — a few cents a month, versus roughly $13/month for the smallest RDS instance. The app
creates the table on startup if it is missing:

```sql
clients(id, email UNIQUE, full_name, password_hash, created_at, last_login_at)
```

Passwords are hashed with `hashlib.scrypt` and a per-user salt, so the image needs no native
crypto library and plaintext is never stored. Sessions are HMAC-SHA256-signed cookies
(`bmi_session`, HttpOnly, SameSite=Lax, 12h). The signing key and the database URL both come from
the `bmi-db-credentials` secret, which `infra/bootstrap.sh` generates — the key **must** be shared
so a session issued by one replica validates on the other. Set `SESSION_COOKIE_SECURE=true` once
the endpoint is on TLS.

Without `DATABASE_URL` the app falls back to an in-memory store, which is what makes the test
suite and local runs work with no database.

### Connect to the database

Postgres is not on the internet. The `bmi-db` Service is headless and only answers inside the
`bmi-api` namespace, so every connection goes through the `bmi-db-0` pod. From a laptop that
already has `kubectl` pointed at the cluster:

```bash
./infra/list-clients.sh                                 # the table, no password hashes
./infra/db-shell.sh                                     # interactive psql (\dt, \q to quit)
./infra/db-shell.sh "SELECT id, email, full_name, created_at FROM clients;"
```

The same session by hand:

```bash
kubectl -n bmi-api exec -it bmi-db-0 -- \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

That opens database `bmi` as user `bmi`. The scripts read the username and password from the
pod's own environment, so they never land in your shell history.

A desktop client (DBeaver, pgAdmin, DataGrip) needs a port forward, because there is no public
hostname:

```bash
kubectl -n bmi-api port-forward pod/bmi-db-0 5432:5432
```

Connect that client to `localhost:5432`, database `bmi`, user `bmi`. Read the password out of
the cluster secret when the client asks for it:

```bash
kubectl -n bmi-api get secret bmi-db-credentials \
  -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d; echo
```

CI cannot read that secret, and it has no `delete` on the StatefulSet or its volume, so a bad
apply cannot drop the clients table.

## Replicate this on your own cluster

Start here once the EKS cluster already exists and `kubectl get nodes` works. You do not build
or deploy from your laptop. `infra/bootstrap.sh` creates the AWS and cluster prerequisites, and
a merge to `main` is what ships the app.

This repo targets `us-east-2` and cluster `etechapp-eks-4QAQxDD3`. Substitute yours, export them
once, and reuse them in every step:

```bash
export AWS_REGION=us-east-2
export CLUSTER_NAME=your-cluster-name
export GITHUB_REPO=your-org/bmi-health-check-k8s
export ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
```

The caller for steps 1–4 needs to administer IAM and the cluster. The GitHub Actions role you
are about to create is much narrower, and it is not the identity that runs bootstrap.

### 1. Check the cluster can hold the app

```bash
aws eks update-kubeconfig --region "$AWS_REGION" --name "$CLUSTER_NAME"
kubectl get nodes
kubectl get storageclass
kubectl -n kube-system get deploy ebs-csi-controller
```

You need all three of these:

- **Free pod slots.** A `t3.small` caps out at 11 pods, and the EKS add-ons can fill a single
  node by themselves. This app adds 3 pods (2 API + 1 Postgres). Add a node before continuing
  if `kubectl describe node` shows pods at the limit.
- **A `gp2` StorageClass.** `k8s/base/postgres.yaml` asks for `storageClassName: gp2`. If your
  cluster's class has another name, change that one field or the volume will sit `Pending`.
- **The EBS CSI driver running** (`ebs-csi-controller` above). Without it the Postgres volume
  is never created. Current EKS clusters ship with it; an older cluster needs the add-on.

### 2. Point the repo at your cluster

Clone your copy of this repo and change the four places that name the cluster. Bootstrap reads
its own values from the environment, so leave `infra/bootstrap.sh` alone.

| File | Change |
| --- | --- |
| `.github/workflows/deploy.yml` | `AWS_REGION`, `EKS_CLUSTER` |
| `.github/workflows/teardown.yml` | `AWS_REGION`, `EKS_CLUSTER` |
| `k8s/base/deployment.yaml` | both `AWS_REGION` and `AWS_DEFAULT_REGION` |

`AWS_DEFAULT_REGION` is required. The AWS SDK reads that name, not `AWS_REGION`, and the
metrics publisher silently does nothing without it.

Leave `k8s/base/serviceaccount.yaml` for step 5. Its role ARN contains your account id, which
bootstrap is about to confirm.

### 3. Register the cluster's OIDC provider in IAM

Pods assume `bmi-api-metrics` through the cluster's own OIDC issuer (IRSA). EKS creates the
issuer, but it does **not** register it as an IAM identity provider, and bootstrap does not
either. Skip this step only if the provider is already there.

```bash
ISSUER="$(aws eks describe-cluster --region "$AWS_REGION" --name "$CLUSTER_NAME" \
  --query 'cluster.identity.oidc.issuer' --output text)"
OIDC_HOST="${ISSUER#https://}"

aws iam get-open-id-connect-provider \
  --open-id-connect-provider-arn "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_HOST}" \
  || aws iam create-open-id-connect-provider \
       --url "$ISSUER" \
       --client-id-list sts.amazonaws.com \
       --thumbprint-list 9e99a48a9960b14926bb7f3b02e22da2b0ab7280
```

`9e99a48a9960b14926bb7f3b02e22da2b0ab7280` is the root CA thumbprint AWS documents for EKS OIDC
issuers. `eksctl utils associate-iam-oidc-provider --cluster "$CLUSTER_NAME" --region "$AWS_REGION" --approve`
does the same thing if you already use eksctl.

### 4. Run bootstrap

```bash
AWS_REGION="$AWS_REGION" CLUSTER_NAME="$CLUSTER_NAME" GITHUB_REPO="$GITHUB_REPO" \
  ./infra/bootstrap.sh
```

The script is safe to re-run. It creates, in order:

1. ECR repository `bmi-health-check-api`, scan-on-push, keep the last 5 images.
2. The GitHub Actions OIDC provider (`token.actions.githubusercontent.com`) if this account
   does not have one yet.
3. IAM role `github-actions-bmi-api-eks` and its inline policy `bmi-api-ecr-eks`.
4. IAM role `bmi-api-metrics` and its inline policy `put-metric-data`.
5. Namespace `bmi-api`, the `bmi-api-deployer` Role, and a RoleBinding to the group
   `bmi-api-deployers`.
6. Secret `bmi-db-credentials` (database password and session signing key, generated, never
   written to git). Delete the secret and re-run to rotate it, then restart the pods.
7. An `aws-auth` entry so the deploy role signs in to the cluster as user `gha-bmi-api` in
   group `bmi-api-deployers`.

The printed `AWS_IAM_ROLE_ARN` at the end is what step 6 stores in GitHub.

**If your cluster's authentication mode is `API` only**, there is no `aws-auth` ConfigMap and
that last step fails. Create an access entry that lands in the same Kubernetes group, then
re-run bootstrap (it will skip everything it already made):

```bash
aws eks create-access-entry \
  --region "$AWS_REGION" --cluster-name "$CLUSTER_NAME" \
  --principal-arn "arn:aws:iam::${ACCOUNT_ID}:role/github-actions-bmi-api-eks" \
  --type STANDARD \
  --username gha-bmi-api \
  --kubernetes-groups bmi-api-deployers
```

### 5. Annotate the ServiceAccount with your metrics role

`k8s/base/serviceaccount.yaml` ships with this repo's account id. Replace it:

```yaml
eks.amazonaws.com/role-arn: arn:aws:iam::ACCOUNT_ID:role/bmi-api-metrics
```

A wrong account id here does not fail the deploy. The pods start, and CloudWatch simply stays
empty.

### 6. Store the deploy role in GitHub

```bash
gh secret set AWS_IAM_ROLE_ARN --repo "$GITHUB_REPO" \
  --body "arn:aws:iam::${ACCOUNT_ID}:role/github-actions-bmi-api-eks"
```

Slack is optional. With no webhook the notification steps skip and the deploy still succeeds:

```bash
gh secret set SLACK_WEBHOOK_URL --repo "$GITHUB_REPO" \
  --body "https://hooks.slack.com/services/..."
```

### 7. Open a pull request and merge it

Push a branch and open a PR into `main`. The PR runs tests, builds the image, and scans it with
Trivy. It does **not** push to ECR and does **not** touch the cluster. Merging the PR pushes to
`main`, and that run deploys.

Watch it with `gh run watch`. The job summary prints the public URL. The same address is on the
Service:

```bash
kubectl -n bmi-api get svc bmi-api
```

Open `http://<that hostname>/`. You should land on the sign-in page. Create an account, then
confirm the row:

```bash
./infra/list-clients.sh
```

To deploy with no load balancer (nothing public, no NLB charge):

```bash
gh workflow run "Build and Deploy to EKS" -f expose_public=false
kubectl -n bmi-api port-forward svc/bmi-api 8080:80
```

### 8. Tear it down later

The **Teardown** workflow (type `DESTROY`) deletes the API deployment, Service, and load
balancer. It leaves Postgres and the client data in place. Erasing those is a separate local
step, and it asks you to type `ERASE` after printing how many clients are stored:

```bash
./infra/destroy-database.sh
```

## IAM roles and trust policies

Bootstrap writes both roles. This is what you should find in IAM afterwards, and what to
compare if a deploy or the metrics publisher is denied. Neither role carries static access
keys. Each can be assumed only by a specific OIDC subject.

### `github-actions-bmi-api-eks` — GitHub Actions deploy

**Who can assume it.** GitHub Actions for this one repository, and only when the workflow has
`permissions: id-token: write` (already set in both workflows).

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": [
            "repo:YOUR_ORG/YOUR_REPO:*",
            "repo:YOUR_ORG@*/YOUR_REPO@*:*"
          ]
        }
      }
    }
  ]
}
```

The first `sub` pattern is the normal GitHub subject (`repo:org/name:ref:refs/heads/main`, and
the same for pull requests and manual runs). The second matches the subject GitHub issues when
the organization belongs to an enterprise. Any other repo in the account fails the condition.

**What it can do.** Inline policy `bmi-api-ecr-eks`:

| Statement | Actions | Resource |
| --- | --- | --- |
| `EcrAuth` | `ecr:GetAuthorizationToken` | `*` (AWS requires this; the token itself grants nothing) |
| `EcrPushPull` | push, pull, describe, and delete images | only `bmi-health-check-api` |
| `EksDescribe` | `eks:DescribeCluster` | only your cluster |
| `Observability` | `cloudwatch:PutDashboard`, `GetDashboard`, `PutMetricAlarm`, `DescribeAlarms`, `elasticloadbalancing:DescribeLoadBalancers` | `*` (these APIs have no resource-level ARN) |

That role can describe the cluster. It cannot list other clusters, and it has no EC2, IAM, or
S3 rights. Kubernetes rights come from the `aws-auth` mapping, not from this policy:

```yaml
- rolearn: arn:aws:iam::ACCOUNT_ID:role/github-actions-bmi-api-eks
  username: gha-bmi-api
  groups:
    - bmi-api-deployers
```

`infra/namespace-and-rbac.yaml` binds that group to a Role in the `bmi-api` namespace only.
The Role can apply the app and run the smoke test (`pods/exec`). It cannot read Secrets, cannot
delete the database StatefulSet, and cannot delete the Postgres volume.

### `bmi-api-metrics` — pods publishing CloudWatch metrics

**Who can assume it.** Only the `bmi-api` ServiceAccount in the `bmi-api` namespace. No other
pod in the cluster matches the `sub` claim.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/OIDC_HOST"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "OIDC_HOST:aud": "sts.amazonaws.com",
          "OIDC_HOST:sub": "system:serviceaccount:bmi-api:bmi-api"
        }
      }
    }
  ]
}
```

`OIDC_HOST` is the cluster issuer with `https://` removed, for example
`oidc.eks.us-east-2.amazonaws.com/id/EXAMPLED539D4633E53DE1B71EXAMPLE`.

**What it can do.** Inline policy `put-metric-data`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "cloudwatch:PutMetricData",
      "Resource": "*",
      "Condition": {
        "StringEquals": { "cloudwatch:namespace": "BMI/HealthCheck" }
      }
    }
  ]
}
```

`PutMetricData` does not support a resource ARN, so the condition is what keeps the pod from
writing metrics into any other namespace. The pod has no other AWS permissions.

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
| 2Gi gp2 volume for Postgres | ~$0.20 |

## Endpoints

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/` | session | End-user UI |
| `GET` | `/signup` · `POST` `/signup` | public | Register a client |
| `GET` | `/signin` · `POST` `/signin` | public | Start a session |
| `POST` | `/logout` | — | Clear the session cookie |
| `GET` | `/health` | public | Probe payload: `status`, `pod`, `node` |
| `GET` | `/me` | session | The signed-in client's record |
| `GET` | `/bmi?height_cm=175&weight_kg=70` | session | BMI + advice via query params |
| `POST` | `/bmi` | session | BMI + advice via JSON body |

## Layout

```text
app/                  FastAPI app, BMI + advice logic, auth, client storage, metrics, UI
tests/                unit tests
Dockerfile            non-root python:3.12-slim image
k8s/base/             ServiceAccount, Deployment, Service, PDB, Postgres (kustomize)
k8s/overlays/public/  NLB exposure (what CI deploys)
infra/                bootstrap, namespace + RBAC, dashboard and alarms, psql helpers
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
