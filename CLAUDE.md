STACK (replaces any earlier stack or Vercel/Railway notes)
- Pipeline: AWS, cheapest design. EventBridge schedule -> Lambda ingest -> S3 raw (JSON, partitioned by date and forecast issue time) -> Parquet -> Athena or DuckDB. Training as a containerised job or scheduled Lambda/ECS task, whichever is cheaper.
- Frontend: static site (Next.js static export or similar) served from S3 + CloudFront. Use GitHub Pages instead if it's simpler. No Vercel, no Railway.
- Infra as code: Terraform for everything. CI/CD: GitHub Actions deploying to AWS via OIDC (an IAM role, no stored long-lived keys).
- Everything is tagged project=river-forecast so it can be found and cleaned up.

AUTONOMY
Work independently and use your best judgement. Do not stop for approval between phases or before actions. Run all phases in order, make decisions, and record them in a DECISIONS.md log (what, why, alternatives) as you go. You may, without asking:
- create and push to the GitHub repo (create duncan-bakare/<name> yourself, public), commit and push to main, open and merge your own PRs
- create, modify and destroy AWS resources in this project, run terraform apply and destroy
- create IAM roles, policies, OIDC providers, and any access keys or tokens the project itself needs, and store them in GitHub Actions secrets or AWS Secrets Manager. Never commit secrets, never print them in logs, never write them into files in the repo.
Stop and report only if: a step needs a login or approval only I can give (browser sign-in, MFA, email verification, third-party account creation), a data source doesn't work and no workaround exists, or you are about to exceed the cost limit below.

GUARDRAILS (cheap to follow, protect me)
- First action in AWS: a monthly budget of EUR 10 with alerts at 50/80/100%, and a billing alarm. Keep total spend under EUR 10/month. If a design would exceed that, pick a cheaper one.
- Only destroy resources you created for this project (tagged project=river-forecast). Never touch anything else in the account or any other GitHub repo.
- Use the AWS profile I've configured. Do not use or ask for root credentials. Prefer least-privilege roles you create over broad ones.
- Do not force-push over history you didn't write. Don't delete the repo.
- Never put API keys, tokens or passwords in chat or in the repo.
- Respect data licences: attribute OPW and Met Éireann, show Met Éireann warnings if forecasts are public, and state clearly that this is not an official flood warning.
- If a source doesn't behave as expected, say so plainly in DECISIONS.md and the README rather than inventing around it.

WORKFLOW
- Commit small and often with normal human-style messages. Push as you go.
- Write tests for ingestion, validation and metrics; CI must pass before merging to main.
- At the end of each phase, update README.md and DECISIONS.md, and write a short PROGRESS.md entry with what was done, what the numbers actually were, and what didn't work.
- Phase 0 (ingestion for both catchments) starts immediately, since the real-time data is only retained for about 5 weeks.